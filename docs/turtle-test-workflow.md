# Turtle Test Workflow

Step-by-step instructions for building and testing the autoresearch-worker image
on **turtle** (6× RTX 2060, Ubuntu 24.04).

All steps after step 1 run **on turtle** over SSH.

---

## Prerequisites

- turtle is reachable via SSH: `ssh alex@turtle.local`
- turtle has passed `./join.sh` (NVIDIA driver + Docker + NVIDIA Container Toolkit present)
- Repo cloned at `/home/alex/hackathon/SAxFS-hackathon`

---

## Step 1 — Push changes (dev machine)

```bash
git add -A && git commit -m "add autoresearch-worker image"
git push
```

---

## Step 2 — Pull on turtle

```bash
ssh alex@turtle.local
cd /home/alex/hackathon/SAxFS-hackathon
git pull
```

---

## Step 3 — Build image (~20–30 min first time; subsequent runs use cached layers)

```bash
./scripts/build.sh
```

Expected output ends with:
```
[build.sh] Build complete: autoresearch-worker:latest
REPOSITORY              TAG       IMAGE ID       CREATED         SIZE
autoresearch-worker     latest    <sha>           <time>          ~8GB
```

If build fails mid-way due to disk pressure:
```bash
docker system prune -f
./scripts/build.sh
```

---

## Step 4 — Create host directories

```bash
mkdir -p /home/alex/hackathon/{cache,output}
```

---

## Step 5 — Download dataset shards (~10 min; skipped on repeat runs)

```bash
./scripts/prepare-cache.sh
```

Downloads 2 shards of ClimbMix-400B + tokenizer into `/home/alex/hackathon/cache/`.
Run this only once; subsequent training runs reuse the cached data.

To download more shards:
```bash
NUM_SHARDS=4 ./scripts/prepare-cache.sh
```

If HuggingFace rate-limits the download:
```bash
HF_TOKEN=hf_... ./scripts/prepare-cache.sh
```

---

## Step 6 — Run training (3 × 5-min iterations, ~17 min wall-clock)

```bash
./scripts/run.sh
```

Watch live output. First iteration includes `torch.compile()` warmup (30–60 s silence
before training starts — this is normal).

Override defaults as needed:
```bash
GPU_INDEX=1 BATCH_SIZE=32 MAX_ITERATIONS=1 ./scripts/run.sh
```

---

## Step 7 — Inspect results

```bash
# Find the run directory (timestamped)
ls /home/alex/hackathon/output/

# Summary
cat /home/alex/hackathon/output/run-<timestamp>/summary.txt

# Training log for first iteration
tail -50 /home/alex/hackathon/output/run-<timestamp>/iter-001.log
```

Expected `summary.txt`:
```
run_id=run-20260314-120000
date=2026-03-14T12:17:00Z
batch_size=64
iterations=3
num_shards=2
failed=0
```

---

## OOM recovery

If `iter-001.log` ends with `CUDA out of memory`:
```bash
BATCH_SIZE=32 ./scripts/run.sh
```

---

## Multi-GPU test (parallel workers)

Run one container per GPU with different `GPU_INDEX` and `RUN_ID`:
```bash
GPU_INDEX=0 RUN_ID=run-gpu0 ./scripts/run.sh &
GPU_INDEX=1 RUN_ID=run-gpu1 ./scripts/run.sh &
wait
```

Each container is fully isolated; they share only the read-after-write cache.

---

## Verification checklist

- [ ] `docker images` shows `autoresearch-worker:latest`
- [ ] `/home/alex/hackathon/cache/autoresearch/data/` is non-empty
- [ ] `/home/alex/hackathon/cache/autoresearch/tokenizer/` is non-empty
- [ ] `iter-001.log`, `iter-002.log`, `iter-003.log` exist and end with `val_bpb=...`
- [ ] `summary.txt` contains `failed=0`
