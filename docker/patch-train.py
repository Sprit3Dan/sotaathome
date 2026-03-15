#!/usr/bin/env python3
"""
patch-train.py — Patch autoresearch train.py to fall back to SDPA when
FlashAttention3 is unavailable (requires Ampere sm80+; RTX 2060 is Turing sm75).

Patches:
  1. Replace FA3 load block with a GPU capability check; set _FA3_AVAILABLE.
  2. In CausalSelfAttention.forward, use SDPA when _FA3_AVAILABLE is False.

Usage: python3 patch-train.py <path-to-train.py>
"""

import sys

path = sys.argv[1]
with open(path) as f:
    src = f.read()

# ── Patch 1: guard FA3 load on GPU capability ─────────────────────────────────
# Original block in train.py:
#   cap = torch.cuda.get_device_capability()
#   # varunneal's FA3 is Hopper only, use kernels-community on non-Hopper GPUs
#   repo = "varunneal/flash-attention-3" if cap == (9, 0) else "kernels-community/flash-attn3"
#   fa3 = get_kernel(repo).flash_attn_interface

old1 = (
    'cap = torch.cuda.get_device_capability()\n'
    '# varunneal\'s FA3 is Hopper only, use kernels-community on non-Hopper GPUs\n'
    'repo = "varunneal/flash-attention-3" if cap == (9, 0) else "kernels-community/flash-attn3"\n'
    'fa3 = get_kernel(repo).flash_attn_interface'
)
new1 = (
    'cap = torch.cuda.get_device_capability()\n'
    '# FA3 requires Ampere (sm80+). Fall back to SDPA on older GPUs (e.g. Turing sm75).\n'
    'if cap >= (8, 0):\n'
    '    repo = "varunneal/flash-attention-3" if cap == (9, 0) else "kernels-community/flash-attn3"\n'
    '    fa3 = get_kernel(repo).flash_attn_interface\n'
    '    _FA3_AVAILABLE = True\n'
    'else:\n'
    '    print(f"[train] GPU capability {cap} < (8,0): skipping FlashAttention3, using SDPA")\n'
    '    fa3 = None\n'
    '    _FA3_AVAILABLE = False'
)

assert old1 in src, (
    "patch-train.py: could not find FA3 load block in train.py\n"
    f"Expected:\n{old1}"
)
src = src.replace(old1, new1, 1)

# ── Patch 2: SDPA fallback in CausalSelfAttention.forward ────────────────────
old2 = "        y = fa3.flash_attn_func(q, k, v, causal=True, window_size=window_size)"
new2 = (
    "        if _FA3_AVAILABLE:\n"
    "            y = fa3.flash_attn_func(q, k, v, causal=True, window_size=window_size)\n"
    "        else:\n"
    "            # SDPA expects (B, n_head, T, head_dim); q/k/v are (B, T, n_head, head_dim)\n"
    "            y = F.scaled_dot_product_attention(\n"
    "                q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2), is_causal=True\n"
    "            ).transpose(1, 2).contiguous()"
)

assert old2 in src, (
    "patch-train.py: could not find fa3.flash_attn_func call in train.py\n"
    f"Expected:\n{old2}"
)
src = src.replace(old2, new2, 1)

with open(path, "w") as f:
    f.write(src)

print(f"[patch-train] Patched {path}: FA3 guarded by capability check, SDPA fallback added")
