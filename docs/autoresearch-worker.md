# autoresearch-worker: Design and Runtime Contract

The `autoresearch-worker` Docker image wraps [karpathy/autoresearch](https://github.com/karpathy/autoresearch)
for single-GPU agentic training runs. One container = one GPU.

Under normal operation, pods are created and managed by the orchestration server
via the Kubernetes deployer — not launched manually via `scripts/run.sh`.

---

## Upstream

- Repo: `https://github.com/karpathy/autoresearch`
- Pinned SHA: `c2450add72cc80317be1fe8111974b892da10944`
- Key files: `prepare.py` (tokenizer training), `train.py` (training loop)

## Base image

`nvidia/cuda:12.8.1-devel-ubuntu22.04`

- `12.8.1` matches `torch==2.9.1` cu128 wheels
- `devel` (not `runtime`) — needed for Triton JIT via `torch.compile()`
- CUDA 13.0 host driver on turtle is forward-compatible with 12.8 container ABI

---

## Runtime contract

### Bind mounts

| Container path | Purpose | Required |
|---|---|---|
| `/artifacts/cache` | Dataset shards + tokenizer; persists across runs | Yes |
| `/artifacts/output` | Logs + summaries; persists across runs | Yes |

### Environment variables

| Variable | Default | Notes |
|---|---|---|
| `AUTORESEARCH_CACHE_DIR` | `/artifacts/cache` | Cache bind-mount path |
| `AUTORESEARCH_OUTPUT_DIR` | `/artifacts/output` | Output bind-mount path |
| `AUTORESEARCH_RUN_ID` | `default` | Labels output files and S3 keys |
| `AUTORESEARCH_MAX_ITERATIONS` | `3` | Agent loop max iterations; `0` = prepare-only |
| `AUTORESEARCH_NUM_SHARDS` | `2` | Dataset shards to create |
| `AUTORESEARCH_GENERATION_ID` | `ungrouped` | S3 path prefix for this generation |
| `AUTORESEARCH_RESEARCH_DIRECTION` | (unset) | Written into `program.md`; steers the agent |
| `AUTORESEARCH_PARENT_CANDIDATE_ID` | (unset) | Parent run ID for lineage.json |
| `AUTORESEARCH_PARENT_METRIC_VALUE` | (unset) | Parent val_bpb for lineage.json |
| `AUTORESEARCH_AGENT_S3_KEY` | (unset) | S3 key of custom agent script; replaces built-in agent |
| `DATASET_HF_REPO` | `roneneldan/TinyStories` | HuggingFace dataset repo |
| `DATASET_TEXT_COLUMN` | `text` | Text column in the dataset |
| `DATASET_TRAIN_SPLIT` | `train` | Training split |
| `DATASET_VAL_SPLIT` | `validation` | Validation split |
| `DEPTH` | `4` | Model depth: n_layers=DEPTH, dim=DEPTH×64 rounded to 128 |
| `DEVICE_BATCH_SIZE` | `8` | Sequences per micro-step; reduce if CUDA OOM |
| `TIME_BUDGET_SECS` | `300` | Per-pod time budget passed to the agent loop |
| `S3_ENDPOINT_URL` | (unset) | MinIO endpoint; upload skipped if not set |
| `S3_ACCESS_KEY` | (unset) | MinIO access key |
| `S3_SECRET_KEY` | (unset) | MinIO secret key |
| `HF_TOKEN` | (unset) | Pass if HuggingFace rate-limits dataset download |

### GPU assignment

Pass `--gpus "device=N"` to `docker run`. Docker presents the selected GPU as `device=0`
inside the container. The entrypoint always runs `CUDA_VISIBLE_DEVICES=0`.

Under Kubernetes (normal operation), the RuntimeClass `nvidia` and node-level GPU
resources handle GPU assignment automatically.

---

## Entrypoint phases

1. **Validate mounts** — die loudly if `/artifacts/cache` or `/artifacts/output` missing
2. **Cache symlink** — `ln -s /artifacts/cache/autoresearch /root/.cache/autoresearch`;
   also sets `HF_DATASETS_CACHE` on the bind-mount
3. **prepare-dataset.py** — downloads the HF dataset as parquet shards into the cache;
   output goes under `${AR_CACHE}/<dataset-slug>/data/`
4. **Dataset symlinks** — `${AR_CACHE}/data` and `${AR_CACHE}/tokenizer` are symlinked
   to the dataset-slug subdirectory so autoresearch sees its expected paths
5. **prepare.py** — trains the BPE tokenizer; skipped if `tokenizer.pkl` already exists
6. **Prepare-only exit** — if `MAX_ITERATIONS=0`, exits here
7. **Workspace copy + patches** — copies upstream to `/workspace/<run-id>`;
   sed-patches `DEPTH` and `DEVICE_BATCH_SIZE` in the workspace `train.py`;
   writes `AUTORESEARCH_RESEARCH_DIRECTION` into `program.md` if set;
   applies FA3→SDPA fallback patch via `patch-train.py`
8. **Agent loop** — runs `agent_loop.py` (or a custom agent downloaded from S3);
   all output tee'd to `${OUTPUT_DIR}/agent-<run-id>.log`
9. **Summary** — writes `${OUTPUT_DIR}/summary-<run-id>.txt` with key=value lines
10. **Artifact contract + S3 upload** — writes `run.json`, `metrics.json`, `lineage.json`,
    and copies final `train.py` into `${OUTPUT_DIR}/<run-id>/`;
    uploads all files to `s3://runs/generations/<gen-id>/<run-id>/` if `S3_ENDPOINT_URL` is set

### Prepare-only mode

Set `AUTORESEARCH_MAX_ITERATIONS=0`. Entrypoint exits after the tokenizer step.
Used by `scripts/prepare-cache.sh` to seed the cache without running training.

---

## Output file layout

All paths are relative to `HOST_OUTPUT_DIR` on the host (default `~/hackathon/output`):

```
<HOST_OUTPUT_DIR>/
  prepare-ds-<run-id>.log      Dataset download + tokenizer log
  agent-<run-id>.log           Agent loop stdout/stderr
  summary-<run-id>.txt         Key=value summary
  <run-id>/
    run.json                   Operational metadata (artifact contract)
    metrics.json               Primary metric: val_bpb
    lineage.json               Parent candidate linkage
    train.py                   Final train.py the agent produced
```

`summary-<run-id>.txt` fields:
```
run_id=<run-id>
date=<ISO timestamp>
depth=<DEPTH>
device_batch_size=<DEVICE_BATCH_SIZE>
num_shards=<NUM_SHARDS>
iterations=<MAX_ITERATIONS>
failed=0           # or 1 if agent loop exited non-zero
dataset_hf_repo=<DATASET_HF_REPO>
wall_clock_seconds=<elapsed>
```

---

## Disk budget

| Item | Size |
|---|---|
| Base CUDA image | ~5 GB |
| torch 2.9.1 cu128 + deps | ~3 GB |
| Dataset (TinyStories, 3 shards) | ~2.4 GB |
| **Total** | **~10.4 GB** |

Turtle has ~24 GB free disk. Avoid parallel builds. Run `docker system prune -f`
after failed builds to reclaim space.

---

## OOM recovery

Default `DEVICE_BATCH_SIZE=8` (set by k8s_deployer). If a pod OOMs, the job is
marked `failed` and preserved for inspection. To retry with smaller batch, adjust
`DEVICE_BATCH_SIZE` in `orchestration/k8s_deployer.py` and resubmit.

For manual runs via `scripts/run.sh`:
```bash
DEVICE_BATCH_SIZE=4 ./scripts/run.sh
```

---

## Flash Attention 3 / Triton notes

- FA3 requires Hopper (sm90+). RTX 2060 is Turing (sm75).
- `patch-train.py` replaces FA3 calls with SDPA fallback — this is applied automatically.
- `torch.compile()` warmup takes 30–60 s on first iteration. This is normal.

---

## Adding new autoresearch capabilities

When modifying the worker:
- Update the pinned `AUTORESEARCH_COMMIT` ARG in `docker/Dockerfile` and re-verify patches apply
- Extend `docker/entrypoint.sh` following the existing phase structure
- Update this doc's env var table and phase list if behavior changes
- Add recovery notes to `docs/turtle-test-workflow.md` for new failure modes
- Rebuild and push: `./orchestration/buildAndPush.sh`
