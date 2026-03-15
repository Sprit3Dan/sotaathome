# autoresearch-worker: Design and Runtime Contract

The `autoresearch-worker` Docker image wraps [karpathy/autoresearch](https://github.com/karpathy/autoresearch)
for single-GPU distributed training runs. One container = one GPU.

---

## Upstream

- Repo: `https://github.com/karpathy/autoresearch`
- Pinned SHA: `c2450add72cc80317be1fe8111974b892da10944`
- Key files: `prepare.py` (data download), `train.py` (300s training run)
- No `research.py` / no LLM loop at this commit

## Base image

`nvidia/cuda:12.8.1-devel-ubuntu22.04`

- `12.8.1` matches `torch==2.9.1` cu128 wheels
- `devel` (not `runtime`) — needed for Triton JIT via `torch.compile()`
- `ubuntu22.04` — stable NVIDIA image support; Python 3.11 via deadsnakes PPA
- CUDA 13.0 host driver on turtle is forward-compatible with 12.8 container ABI

---

## Runtime contract

### Bind mounts

| Container path | Purpose | Required |
|----------------|---------|----------|
| `/artifacts/cache` | Dataset shards + tokenizer; persists across runs | Yes |
| `/artifacts/output` | Logs + summaries; persists across runs | Yes |
| `/artifacts/input` | Reserved for future orchestrator inputs | No |

### Environment variables

| Variable | Default | Notes |
|----------|---------|-------|
| `AUTORESEARCH_CACHE_DIR` | `/artifacts/cache` | Where cache symlink points |
| `AUTORESEARCH_OUTPUT_DIR` | `/artifacts/output` | Where logs and summary go |
| `AUTORESEARCH_INPUT_DIR` | `/artifacts/input` | Reserved |
| `AUTORESEARCH_RUN_ID` | `default` | Labels output subdirectory |
| `AUTORESEARCH_MAX_ITERATIONS` | `3` | Set to `0` for prepare-only |
| `AUTORESEARCH_NUM_SHARDS` | `2` | ~2.4 GB download |
| `AUTORESEARCH_VISIBLE_GPU` | `0` | Documentary; Docker `--gpus device=N` controls actual GPU |
| `DEVICE_BATCH_SIZE` | `64` | sed-patched into workspace copy of train.py |
| `HF_TOKEN` | (unset) | Pass if HuggingFace rate-limits prepare.py |

### GPU assignment

Pass `--gpus "device=N"` to `docker run`. Docker presents the selected GPU as `device=0`
inside the container. The entrypoint always runs `CUDA_VISIBLE_DEVICES=0`.

---

## Entrypoint phases

1. **Validate mounts** — die loudly if `/artifacts/cache` or `/artifacts/output` missing
2. **Cache symlink** — `ln -s /artifacts/cache/autoresearch /root/.cache/autoresearch`
   so `prepare.py` and `train.py` see their expected cache path
3. **Workspace copy** — `cp -r /opt/autoresearch-upstream /workspace/<run-id>`
   (upstream reference is never modified)
4. **DEVICE_BATCH_SIZE patch** — `sed` replaces `128` with the requested value in workspace `train.py`;
   `grep` verifies the patch applied; fails hard if not
5. **prepare.py** — skipped if `tokenizer/` and `data/` dirs already exist in cache
6. **Training loop** — N × `train.py` (each runs for 300 s); logs to `iter-001.log` etc.
7. **Summary** — writes `summary.txt` with `key=value` lines including `failed=0/1`

### prepare-only mode

Set `AUTORESEARCH_MAX_ITERATIONS=0`. Entrypoint exits after `prepare.py` completes.
Used by `scripts/prepare-cache.sh` to seed the cache without running training.

---

## Disk budget

| Item | Size |
|------|------|
| Base CUDA image | ~5 GB |
| torch 2.9.1 cu128 + deps | ~3 GB |
| 2 data shards + tokenizer | ~2.4 GB |
| **Total** | **~10.4 GB** |

Turtle has ~24 GB free disk. Avoid parallel builds; run `docker system prune -f` after
failed builds to reclaim space.

---

## OOM recovery

Default `DEVICE_BATCH_SIZE=64` targets 12 GB VRAM (RTX 2060). If iteration 1 exits
with CUDA OOM:

```bash
BATCH_SIZE=32 ./scripts/run.sh
```

---

## Flash Attention 3 / Triton notes

- FA3 requires Hopper (sm90+). RTX 2060 is Turing (sm75).
- Upstream falls back silently — expect slower throughput and slightly higher VRAM use.
- `torch.compile()` warmup takes 30–60 s on first iteration. This is normal.
