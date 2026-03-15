#!/usr/bin/env python3
"""
prepare-tinystories.py — Convert TinyStories into autoresearch-compatible parquet shards.

autoresearch expects parquet files with a 'text' column in:
  ~/.cache/autoresearch/data/shard_NNNNN.parquet

Validation is hardcoded to shard_06542.parquet (VAL_SHARD in prepare.py).
Training shards are shard_00000 through shard_N-1.

After this script runs, autoresearch's prepare.py can train the BPE tokenizer
on the resulting files (it will skip download since all shards already exist).

Usage:
    python prepare-tinystories.py [--num-shards N]
"""

import argparse
import math
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# Match autoresearch's cache layout exactly
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "autoresearch")
DATA_DIR  = os.path.join(CACHE_DIR, "data")
VAL_SHARD = 6542  # autoresearch hardcodes this as the val shard

# Point HF datasets cache inside our mounted cache dir so it persists
os.environ.setdefault("HF_DATASETS_CACHE", os.path.join(CACHE_DIR, "hf_datasets"))


def write_shard(texts: list, path: str) -> None:
    table = pa.table({"text": texts})
    pq.write_table(table, path)
    size_mb = Path(path).stat().st_size / 1024 / 1024
    print(f"  wrote {len(texts):,} stories  ({size_mb:.1f} MB) → {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--num-shards", type=int, default=3,
        help="Number of training shards (default 3). Val shard is always created."
    )
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    val_path    = os.path.join(DATA_DIR, f"shard_{VAL_SHARD:05d}.parquet")
    train_paths = [os.path.join(DATA_DIR, f"shard_{i:05d}.parquet")
                   for i in range(args.num_shards)]

    all_done = os.path.exists(val_path) and all(os.path.exists(p) for p in train_paths)
    if all_done:
        print(f"[prepare-ts] All {args.num_shards} train shards + val shard already exist — done.")
        return

    print("[prepare-ts] Downloading TinyStories from HuggingFace ...")
    from datasets import load_dataset  # imported late so error is clear if datasets missing

    # ── Validation shard ──────────────────────────────────────────────────────
    if not os.path.exists(val_path):
        print("[prepare-ts] Fetching validation split ...")
        val_ds = load_dataset("roneneldan/TinyStories", split="validation")
        write_shard(val_ds["text"], val_path)
    else:
        print(f"[prepare-ts] Val shard exists, skipping: {val_path}")

    # ── Training shards ───────────────────────────────────────────────────────
    missing = [i for i, p in enumerate(train_paths) if not os.path.exists(p)]
    if not missing:
        print("[prepare-ts] All training shards exist — done.")
        return

    print("[prepare-ts] Fetching training split ...")
    train_ds = load_dataset("roneneldan/TinyStories", split="train")
    total            = len(train_ds)
    stories_per_shard = math.ceil(total / args.num_shards)
    print(f"[prepare-ts] {total:,} stories → {args.num_shards} shards of ~{stories_per_shard:,} each")

    for i in range(args.num_shards):
        p = train_paths[i]
        if os.path.exists(p):
            print(f"  shard {i} exists, skipping")
            continue
        lo = i * stories_per_shard
        hi = min(lo + stories_per_shard, total)
        write_shard(train_ds[lo:hi]["text"], p)

    print(f"\n[prepare-ts] Done. {args.num_shards} training shards + val in {DATA_DIR}")
    print("[prepare-ts] Next: run autoresearch prepare.py to train BPE tokenizer.")


if __name__ == "__main__":
    main()
