#!/usr/bin/env python3
"""
train.py — Train a small GPT on TinyStories from scratch.

Self-contained: downloads TinyStories, tokenizes with GPT-2 tokenizer, trains,
saves checkpoint. Everything is configurable via environment variables.

Config env vars (all have defaults):
  CACHE_DIR            where to store tokenized dataset    (/artifacts/cache)
  OUTPUT_DIR           where to write logs and checkpoint  (/artifacts/output)
  RUN_ID               label for this run                  (default)
  BATCH_SIZE           sequences per gradient step         (64)
  BLOCK_SIZE           context length in tokens            (256)
  N_LAYER              transformer layers                   (6)
  N_HEAD               attention heads                      (6)
  N_EMBD               embedding dimension                  (384)
  LEARNING_RATE        AdamW learning rate                  (3e-4)
  TIME_BUDGET_SECS     stop training after N seconds        (300)
  MAX_STEPS            hard step cap (0 = time-budget only) (0)
  EVAL_INTERVAL        evaluate val loss every N steps      (200)
  EVAL_STEPS           batches to average for val loss      (20)
  MAX_TRAIN_EXAMPLES   cap train set (0 = full ~2.1M)       (0)
  PREPARE_ONLY         if 1, exit after tokenizing          (0)
"""

import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.nn import functional as F

# ── Config ────────────────────────────────────────────────────────────────────

def _env_int(k, d):   return int(os.environ.get(k, d))
def _env_float(k, d): return float(os.environ.get(k, d))
def _env_str(k, d):   return os.environ.get(k, d)

CACHE_DIR           = Path(_env_str('CACHE_DIR',  '/artifacts/cache'))
OUTPUT_DIR          = Path(_env_str('OUTPUT_DIR', '/artifacts/output'))
RUN_ID              = _env_str('RUN_ID', 'default')
BATCH_SIZE          = _env_int('BATCH_SIZE', 64)
BLOCK_SIZE          = _env_int('BLOCK_SIZE', 256)
N_LAYER             = _env_int('N_LAYER', 6)
N_HEAD              = _env_int('N_HEAD', 6)
N_EMBD              = _env_int('N_EMBD', 384)
LEARNING_RATE       = _env_float('LEARNING_RATE', 3e-4)
TIME_BUDGET_SECS    = _env_float('TIME_BUDGET_SECS', 300)
MAX_STEPS           = _env_int('MAX_STEPS', 0)       # 0 = time-budget only
EVAL_INTERVAL       = _env_int('EVAL_INTERVAL', 200)
EVAL_STEPS          = _env_int('EVAL_STEPS', 20)
MAX_TRAIN_EXAMPLES  = _env_int('MAX_TRAIN_EXAMPLES', 0)  # 0 = full dataset
PREPARE_ONLY        = _env_str('PREPARE_ONLY', '0') == '1'

DATA_DIR  = CACHE_DIR / 'tinystories'
TRAIN_BIN = DATA_DIR / 'train.bin'
VAL_BIN   = DATA_DIR / 'val.bin'
CKPT_PATH = OUTPUT_DIR / f'ckpt-{RUN_ID}.pt'

print(f"[train] CACHE_DIR={CACHE_DIR}  OUTPUT_DIR={OUTPUT_DIR}  RUN_ID={RUN_ID}")
print(f"[train] model: {N_LAYER}L {N_HEAD}H {N_EMBD}D  block={BLOCK_SIZE}  batch={BATCH_SIZE}")
print(f"[train] time_budget={TIME_BUDGET_SECS}s  lr={LEARNING_RATE}")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Data preparation ──────────────────────────────────────────────────────────

def prepare_data():
    if TRAIN_BIN.exists() and VAL_BIN.exists():
        n_train = TRAIN_BIN.stat().st_size // 2  # uint16
        n_val   = VAL_BIN.stat().st_size   // 2
        print(f"[prepare] Cache exists: train={n_train:,}  val={n_val:,} tokens — skipping download")
        return

    print("[prepare] Loading TinyStories from HuggingFace...")
    # Set HF cache inside our mounted cache dir so it survives between runs
    os.environ.setdefault('HF_DATASETS_CACHE', str(CACHE_DIR / 'hf_datasets'))

    from datasets import load_dataset
    import tiktoken

    enc = tiktoken.get_encoding('gpt2')
    eot = enc.encode('<|endoftext|>', allowed_special={'<|endoftext|>'})[0]

    splits = {
        'train':      TRAIN_BIN,
        'validation': VAL_BIN,
    }

    for split, bin_path in splits.items():
        extra = ''
        if split == 'train' and MAX_TRAIN_EXAMPLES > 0:
            extra = f'[:{MAX_TRAIN_EXAMPLES}]'

        print(f"[prepare] Downloading split='{split}{extra}' ...")
        ds = load_dataset(
            'roneneldan/TinyStories',
            split=f'{split}{extra}',
            trust_remote_code=True,
        )
        print(f"[prepare] Tokenizing {len(ds):,} examples ...")

        all_tokens = []
        report_every = max(1, len(ds) // 20)
        for i, row in enumerate(ds):
            toks = enc.encode_ordinary(row['text'])
            toks.append(eot)
            all_tokens.extend(toks)
            if (i + 1) % report_every == 0:
                pct = 100 * (i + 1) / len(ds)
                print(f"  {pct:.0f}%  {len(all_tokens):,} tokens so far")

        arr = np.array(all_tokens, dtype=np.uint16)
        arr.tofile(str(bin_path))
        print(f"[prepare] {split}: {len(arr):,} tokens → {bin_path}")


prepare_data()

if PREPARE_ONLY:
    print("[train] PREPARE_ONLY=1, done.")
    sys.exit(0)

# ── Dataset ───────────────────────────────────────────────────────────────────

def get_batch(split: str, device: str):
    path = TRAIN_BIN if split == 'train' else VAL_BIN
    data = np.memmap(str(path), dtype=np.uint16, mode='r')
    if len(data) <= BLOCK_SIZE:
        raise RuntimeError(f"Dataset too small: {len(data)} tokens < block_size {BLOCK_SIZE}")
    ix = torch.randint(len(data) - BLOCK_SIZE, (BATCH_SIZE,))
    x = torch.stack([torch.from_numpy(data[i     : i + BLOCK_SIZE].astype(np.int64)) for i in ix])
    y = torch.stack([torch.from_numpy(data[i + 1 : i + 1 + BLOCK_SIZE].astype(np.int64)) for i in ix])
    return x.to(device), y.to(device)

# ── Model ─────────────────────────────────────────────────────────────────────

class CausalSelfAttention(nn.Module):
    def __init__(self):
        super().__init__()
        self.c_attn  = nn.Linear(N_EMBD, 3 * N_EMBD, bias=False)
        self.c_proj  = nn.Linear(N_EMBD, N_EMBD, bias=False)
        self.n_head  = N_HEAD
        self.n_embd  = N_EMBD
        self.flash   = hasattr(F, 'scaled_dot_product_attention')
        if not self.flash:
            self.register_buffer(
                'bias',
                torch.tril(torch.ones(BLOCK_SIZE, BLOCK_SIZE)).view(1, 1, BLOCK_SIZE, BLOCK_SIZE)
            )

    def forward(self, x):
        B, T, C = x.size()
        nh, hs = self.n_head, C // self.n_head
        q, k, v = self.c_attn(x).split(C, dim=2)
        q = q.view(B, T, nh, hs).transpose(1, 2)
        k = k.view(B, T, nh, hs).transpose(1, 2)
        v = v.view(B, T, nh, hs).transpose(1, 2)
        if self.flash:
            y = F.scaled_dot_product_attention(q, k, v, is_causal=True)
        else:
            att = (q @ k.transpose(-2, -1)) / math.sqrt(hs)
            att = att.masked_fill(self.bias[:, :, :T, :T] == 0, float('-inf'))
            att = torch.softmax(att, dim=-1)
            y = att @ v
        y = y.transpose(1, 2).contiguous().view(B, T, C)
        return self.c_proj(y)


class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.ln1  = nn.LayerNorm(N_EMBD)
        self.attn = CausalSelfAttention()
        self.ln2  = nn.LayerNorm(N_EMBD)
        self.mlp  = nn.Sequential(
            nn.Linear(N_EMBD, 4 * N_EMBD, bias=False),
            nn.GELU(),
            nn.Linear(4 * N_EMBD, N_EMBD, bias=False),
        )

    def forward(self, x):
        x = x + self.attn(self.ln1(x))
        x = x + self.mlp(self.ln2(x))
        return x


VOCAB_SIZE = 50257  # GPT-2 tokenizer


class GPT(nn.Module):
    def __init__(self):
        super().__init__()
        self.tok_emb = nn.Embedding(VOCAB_SIZE, N_EMBD)
        self.pos_emb = nn.Embedding(BLOCK_SIZE, N_EMBD)
        self.drop    = nn.Dropout(0.0)
        self.blocks  = nn.Sequential(*[Block() for _ in range(N_LAYER)])
        self.ln_f    = nn.LayerNorm(N_EMBD)
        self.head    = nn.Linear(N_EMBD, VOCAB_SIZE, bias=False)
        self.tok_emb.weight = self.head.weight  # weight tying
        self.apply(self._init_weights)
        # scale residual projections
        for name, p in self.named_parameters():
            if name.endswith('c_proj.weight'):
                nn.init.normal_(p, std=0.02 / math.sqrt(2 * N_LAYER))
        n = sum(p.numel() for p in self.parameters())
        print(f"[model] {N_LAYER}L {N_HEAD}H {N_EMBD}D → {n/1e6:.1f}M parameters")

    @staticmethod
    def _init_weights(m):
        if isinstance(m, nn.Linear):
            nn.init.normal_(m.weight, std=0.02)
        elif isinstance(m, nn.Embedding):
            nn.init.normal_(m.weight, std=0.02)

    def forward(self, idx, targets=None):
        B, T = idx.shape
        pos   = torch.arange(T, device=idx.device)
        x     = self.drop(self.tok_emb(idx) + self.pos_emb(pos))
        x     = self.blocks(x)
        logits = self.head(self.ln_f(x))
        loss   = None
        if targets is not None:
            loss = F.cross_entropy(logits.view(-1, VOCAB_SIZE), targets.view(-1))
        return logits, loss

# ── Setup ─────────────────────────────────────────────────────────────────────

device = 'cuda' if torch.cuda.is_available() else 'cpu'
if device == 'cuda':
    print(f"[train] GPU: {torch.cuda.get_device_name(0)}")
    print(f"[train] VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
else:
    print("[train] WARNING: no GPU found, running on CPU (will be slow)")

model = GPT().to(device)
optimizer = torch.optim.AdamW(
    model.parameters(),
    lr=LEARNING_RATE,
    betas=(0.9, 0.95),
    weight_decay=0.1,
    fused=(device == 'cuda'),
)
use_amp = (device == 'cuda')
scaler  = torch.cuda.amp.GradScaler(enabled=use_amp)

# ── Eval ──────────────────────────────────────────────────────────────────────

@torch.no_grad()
def estimate_val_loss() -> float:
    model.eval()
    losses = []
    for _ in range(EVAL_STEPS):
        xv, yv = get_batch('val', device)
        with torch.autocast('cuda', torch.float16, enabled=use_amp):
            _, loss = model(xv, yv)
        losses.append(loss.item())
    model.eval()  # keep in eval until we set train below
    model.train()
    return sum(losses) / len(losses)

# ── Training loop ─────────────────────────────────────────────────────────────

print(f"[train] Starting: budget={TIME_BUDGET_SECS}s  eval_every={EVAL_INTERVAL} steps")
print(f"[train]  step | train_loss | val_loss  | tok/s    | elapsed")

step            = 0
tokens_seen     = 0
best_val_loss   = float('inf')
t_start         = time.perf_counter()
model.train()

while True:
    elapsed = time.perf_counter() - t_start
    if elapsed >= TIME_BUDGET_SECS:
        print(f"[train] Time budget reached at step {step}.")
        break
    if MAX_STEPS > 0 and step >= MAX_STEPS:
        print(f"[train] MAX_STEPS={MAX_STEPS} reached.")
        break

    x, y = get_batch('train', device)

    with torch.autocast('cuda', torch.float16, enabled=use_amp):
        _, loss = model(x, y)

    scaler.scale(loss).backward()
    scaler.unscale_(optimizer)
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    scaler.step(optimizer)
    scaler.update()
    optimizer.zero_grad(set_to_none=True)

    tokens_seen += BATCH_SIZE * BLOCK_SIZE
    step        += 1

    if step % EVAL_INTERVAL == 0 or step == 1:
        val_loss = estimate_val_loss()
        elapsed  = time.perf_counter() - t_start
        tok_s    = tokens_seen / elapsed
        print(
            f"[train] {step:5d} | {loss.item():.4f}     | {val_loss:.4f}    "
            f"| {tok_s:8.0f} | {elapsed:.0f}s"
        )
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            ckpt = {
                'step':         step,
                'val_loss':     val_loss,
                'model_state':  model.state_dict(),
                'config': {
                    'n_layer': N_LAYER, 'n_head': N_HEAD, 'n_embd': N_EMBD,
                    'block_size': BLOCK_SIZE, 'vocab_size': VOCAB_SIZE,
                },
            }
            torch.save(ckpt, str(CKPT_PATH))
            print(f"[train] ✓ checkpoint saved (val_loss={val_loss:.4f}) → {CKPT_PATH}")

    if device == 'cuda' and step % 50 == 0:
        vram_gb = torch.cuda.max_memory_allocated() / 1e9
        print(f"[train] step={step}  peak_vram={vram_gb:.2f} GB")

# ── Summary ───────────────────────────────────────────────────────────────────

elapsed = time.perf_counter() - t_start
total_tokens_M = tokens_seen / 1e6
print()
print(f"[train] Finished: {step} steps  {total_tokens_M:.1f}M tokens  {elapsed:.0f}s")
print(f"[train] best_val_loss={best_val_loss:.4f}")

summary_path = OUTPUT_DIR / f'summary-{RUN_ID}.txt'
summary_path.write_text(
    f"run_id={RUN_ID}\n"
    f"steps={step}\n"
    f"tokens_M={total_tokens_M:.2f}\n"
    f"best_val_loss={best_val_loss:.4f}\n"
    f"elapsed_s={elapsed:.0f}\n"
    f"batch_size={BATCH_SIZE}\n"
    f"block_size={BLOCK_SIZE}\n"
    f"n_layer={N_LAYER}\n"
    f"n_head={N_HEAD}\n"
    f"n_embd={N_EMBD}\n"
    f"checkpoint={CKPT_PATH}\n"
)
print(f"[train] Summary → {summary_path}")
