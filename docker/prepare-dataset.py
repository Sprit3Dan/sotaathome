#!/usr/bin/env python3
"""
prepare-dataset.py — Convert any HuggingFace text dataset into autoresearch-compatible parquet shards.

Reads env vars:
  DATASET_HF_REPO        (default: roneneldan/TinyStories)
  DATASET_TEXT_COLUMN    (default: text)
  DATASET_TRAIN_SPLIT    (default: train)
  DATASET_VAL_SPLIT      (default: validation)
  AUTORESEARCH_NUM_SHARDS (default: 3)

The script writes parquet files into:
  ~/.cache/autoresearch/<dataset-slug>/data/

where dataset-slug = DATASET_HF_REPO with "/" replaced by "-".

autoresearch expects parquet files with a 'text' column in:
  ~/.cache/autoresearch/data/shard_NNNNN.parquet

entrypoint.sh symlinks that path to the dataset-specific directory after this script runs.

Validation is hardcoded to shard_06542.parquet (VAL_SHARD in autoresearch's prepare.py).
"""

import argparse
import math
import os
from pathlib import Path

DATASET_HF_REPO = os.environ.get("DATASET_HF_REPO", "roneneldan/TinyStories")
DATASET_TEXT_COLUMN = os.environ.get("DATASET_TEXT_COLUMN", "text")
DATASET_TRAIN_SPLIT = os.environ.get("DATASET_TRAIN_SPLIT", "train")
DATASET_VAL_SPLIT = os.environ.get("DATASET_VAL_SPLIT", "validation")

# Dataset-specific cache layout under ~/.cache/autoresearch/<slug>/
_ar_cache = os.path.join(os.path.expanduser("~"), ".cache", "autoresearch")
DATASET_SLUG = DATASET_HF_REPO.replace("/", "-")
DATASET_CACHE_DIR = os.path.join(_ar_cache, DATASET_SLUG)
DATA_DIR = os.path.join(DATASET_CACHE_DIR, "data")
VAL_SHARD = 6542  # autoresearch hardcodes this as the val shard

# HF datasets raw downloads share a single cache
os.environ.setdefault("HF_DATASETS_CACHE", os.path.join(_ar_cache, "hf_datasets"))


def write_shard(texts: list, path: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({"text": texts})
    pq.write_table(table, path)
    size_mb = Path(path).stat().st_size / 1024 / 1024
    print(f"  wrote {len(texts):,} items  ({size_mb:.1f} MB) → {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--num-shards",
        type=int,
        default=int(os.environ.get("AUTORESEARCH_NUM_SHARDS", "3")),
        help="Number of training shards (default 3). Val shard is always created.",
    )
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    val_path = os.path.join(DATA_DIR, f"shard_{VAL_SHARD:05d}.parquet")
    train_paths = [os.path.join(DATA_DIR, f"shard_{i:05d}.parquet") for i in range(args.num_shards)]

    all_done = os.path.exists(val_path) and all(os.path.exists(p) for p in train_paths)
    if all_done:
        print(
            f"[prepare-dataset] All {args.num_shards} train shards + val shard already exist"
            f" for {DATASET_HF_REPO} — done."
        )
        return

    print(f"[prepare-dataset] Downloading {DATASET_HF_REPO} from HuggingFace ...")
    from datasets import load_dataset

    # Validation shard
    if not os.path.exists(val_path):
        print(f"[prepare-dataset] Fetching {DATASET_VAL_SPLIT} split ...")
        val_ds = load_dataset(DATASET_HF_REPO, split=DATASET_VAL_SPLIT)
        write_shard(val_ds[DATASET_TEXT_COLUMN], val_path)
    else:
        print(f"[prepare-dataset] Val shard exists, skipping: {val_path}")

    # Training shards
    missing = [i for i, p in enumerate(train_paths) if not os.path.exists(p)]
    if not missing:
        print("[prepare-dataset] All training shards exist — done.")
        return

    print(f"[prepare-dataset] Fetching {DATASET_TRAIN_SPLIT} split ...")
    train_ds = load_dataset(DATASET_HF_REPO, split=DATASET_TRAIN_SPLIT)
    total = len(train_ds)
    items_per_shard = math.ceil(total / args.num_shards)
    print(f"[prepare-dataset] {total:,} items → {args.num_shards} shards of ~{items_per_shard:,} each")

    for i in range(args.num_shards):
        p = train_paths[i]
        if os.path.exists(p):
            print(f"  shard {i} exists, skipping")
            continue
        lo = i * items_per_shard
        hi = min(lo + items_per_shard, total)
        write_shard(train_ds[lo:hi][DATASET_TEXT_COLUMN], p)

    print(f"\n[prepare-dataset] Done. {args.num_shards} training shards + val in {DATA_DIR}")
    print("[prepare-dataset] Next: run autoresearch prepare.py to train BPE tokenizer.")


if __name__ == "__main__":
    main()
