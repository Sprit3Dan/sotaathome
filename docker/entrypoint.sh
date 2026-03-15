#!/usr/bin/env bash
# entrypoint.sh — autoresearch-worker container entrypoint
#
# Phases:
#   1. Validate bind-mounts
#   2. Redirect ~/.cache/autoresearch → /artifacts/cache/autoresearch
#   3. prepare-dataset.py  — create parquet shards for configured HF dataset
#   4. Symlink dataset-specific data/tokenizer dirs into autoresearch cache
#   5. prepare.py          — train BPE tokenizer (skips if already done)
#   6. exit if MAX_ITERATIONS=0 (prepare-only)
#   7. Copy workspace, patch DEPTH + DEVICE_BATCH_SIZE, optionally override program.md
#   8. Training loop (N × train.py)
#   9. Write summary
#  10. Write artifact contract (run.json, metrics.json, lineage.json) + upload to S3

set -euo pipefail

log() { echo "[entrypoint] $*"; }
die() { echo "[entrypoint] ERROR: $*" >&2; exit 1; }

# ── Dataset env var defaults (backward-compatible) ────────────────────────────
DATASET_HF_REPO="${DATASET_HF_REPO:-roneneldan/TinyStories}"
DATASET_TEXT_COLUMN="${DATASET_TEXT_COLUMN:-text}"
DATASET_TRAIN_SPLIT="${DATASET_TRAIN_SPLIT:-train}"
DATASET_VAL_SPLIT="${DATASET_VAL_SPLIT:-validation}"

export DATASET_HF_REPO DATASET_TEXT_COLUMN DATASET_TRAIN_SPLIT DATASET_VAL_SPLIT

CACHE_DIR="${AUTORESEARCH_CACHE_DIR}"
OUTPUT_DIR="${AUTORESEARCH_OUTPUT_DIR}"
RUN_ID="${AUTORESEARCH_RUN_ID}"
NUM_SHARDS="${AUTORESEARCH_NUM_SHARDS}"
MAX_ITERATIONS="${AUTORESEARCH_MAX_ITERATIONS}"

START_EPOCH=$(date +%s)
START_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ── 1. Validate mounts ────────────────────────────────────────────────────────
[[ -d "${CACHE_DIR}" ]]  || die "Cache mount missing. Run with: -v <host>:${CACHE_DIR}"
[[ -d "${OUTPUT_DIR}" ]] || die "Output mount missing. Run with: -v <host>:${OUTPUT_DIR}"
log "CACHE_DIR=${CACHE_DIR}  OUTPUT_DIR=${OUTPUT_DIR}  RUN_ID=${RUN_ID}"
log "DATASET_HF_REPO=${DATASET_HF_REPO}  MAX_ITERATIONS=${MAX_ITERATIONS}"

# ── 2. Cache symlink ──────────────────────────────────────────────────────────
AR_CACHE="${CACHE_DIR}/autoresearch"
mkdir -p "${AR_CACHE}"
mkdir -p /root/.cache

if [[ -d /root/.cache/autoresearch && ! -L /root/.cache/autoresearch ]]; then
    die "/root/.cache/autoresearch exists as a real directory (not a symlink). Remove it and retry."
fi
if [[ ! -L /root/.cache/autoresearch ]]; then
    ln -s "${AR_CACHE}" /root/.cache/autoresearch
    log "Symlinked ~/.cache/autoresearch → ${AR_CACHE}"
fi

# HF datasets cache also goes on the bind-mount
export HF_DATASETS_CACHE="${AR_CACHE}/hf_datasets"

# ── 3. prepare-dataset.py ─────────────────────────────────────────────────────
log "Running prepare-dataset.py --num-shards ${NUM_SHARDS} for ${DATASET_HF_REPO} ..."
python3 /app/prepare-dataset.py --num-shards "${NUM_SHARDS}" \
    2>&1 | tee "${OUTPUT_DIR}/prepare-ds-${RUN_ID}.log"

# ── 4. Dataset-specific data/tokenizer symlinks ───────────────────────────────
# prepare-dataset.py writes to ${AR_CACHE}/<slug>/data/
# autoresearch expects data at ${AR_CACHE}/data/ — create symlink per dataset
DATASET_SLUG="${DATASET_HF_REPO//\//-}"
DATASET_DATA_DIR="${AR_CACHE}/${DATASET_SLUG}/data"
DATASET_TOKENIZER_DIR="${AR_CACHE}/${DATASET_SLUG}/tokenizer"
mkdir -p "${DATASET_DATA_DIR}" "${DATASET_TOKENIZER_DIR}"

# Data symlink (ln -sf is atomic enough to avoid race with parallel pods)
if [[ -d "${AR_CACHE}/data" && ! -L "${AR_CACHE}/data" ]]; then
    log "WARNING: ${AR_CACHE}/data is a real directory; backing up as data.bak"
    mv "${AR_CACHE}/data" "${AR_CACHE}/data.bak" || true
fi
ln -sf "${DATASET_DATA_DIR}" "${AR_CACHE}/data"
log "Symlinked ${AR_CACHE}/data → ${DATASET_DATA_DIR}"

# Tokenizer symlink (ln -sf is atomic enough to avoid race with parallel pods)
if [[ -d "${AR_CACHE}/tokenizer" && ! -L "${AR_CACHE}/tokenizer" ]]; then
    log "WARNING: ${AR_CACHE}/tokenizer is a real directory; backing up as tokenizer.bak"
    mv "${AR_CACHE}/tokenizer" "${AR_CACHE}/tokenizer.bak" || true
fi
ln -sf "${DATASET_TOKENIZER_DIR}" "${AR_CACHE}/tokenizer"
log "Symlinked ${AR_CACHE}/tokenizer → ${DATASET_TOKENIZER_DIR}"

# ── 5. Tokenizer training (autoresearch prepare.py) ───────────────────────────
TOKENIZER_DIR="${DATASET_TOKENIZER_DIR}"
if [[ -f "${TOKENIZER_DIR}/tokenizer.pkl" && -f "${TOKENIZER_DIR}/token_bytes.pt" ]]; then
    log "Tokenizer already trained for ${DATASET_HF_REPO}, skipping prepare.py."
else
    log "Training BPE tokenizer via prepare.py --num-shards ${NUM_SHARDS} ..."
    (cd /opt/autoresearch-upstream && python prepare.py --num-shards "${NUM_SHARDS}") \
        2>&1 | tee -a "${OUTPUT_DIR}/prepare-ds-${RUN_ID}.log"
fi

# ── 6. Prepare-only mode ──────────────────────────────────────────────────────
if [[ "${MAX_ITERATIONS}" -eq 0 ]]; then
    log "AUTORESEARCH_MAX_ITERATIONS=0: prepare-only mode, done."
    exit 0
fi

# ── 7. Workspace copy + patches ───────────────────────────────────────────────
WORKSPACE="/workspace/${RUN_ID}"
log "Copying upstream to workspace: ${WORKSPACE}"
mkdir -p /workspace
cp -r /opt/autoresearch-upstream "${WORKSPACE}"

# ── 7a. Inherit parent train.py (if provided) ─────────────────────────────────
# When a parent generation promoted a candidate, its train.py is downloaded from
# S3 and placed into the workspace before patching. This is how improvements
# compound across generations: each generation starts from the best prior result
# rather than from upstream scratch.
if [[ -n "${AUTORESEARCH_PARENT_TRAIN_S3_KEY:-}" && -n "${S3_ENDPOINT_URL:-}" ]]; then
    log "Inheriting parent train.py from s3://runs/${AUTORESEARCH_PARENT_TRAIN_S3_KEY}"
    python3 -c "
import boto3, os
s3 = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT_URL'],
    aws_access_key_id=os.environ['S3_ACCESS_KEY'],
    aws_secret_access_key=os.environ['S3_SECRET_KEY'])
s3.download_file('runs', os.environ['AUTORESEARCH_PARENT_TRAIN_S3_KEY'], '${WORKSPACE}/train.py')
print('[entrypoint] Parent train.py downloaded.')
"
    log "Parent train.py installed into workspace (patches will be applied next)"
fi

# Patch DEPTH (model size: n_layers = DEPTH, model_dim = DEPTH × 64 rounded to HEAD_DIM=128)
log "Patching DEPTH=${DEPTH} in workspace train.py ..."
sed -i "s/^DEPTH = [0-9]\+/DEPTH = ${DEPTH}/" "${WORKSPACE}/train.py"
grep -qE "^DEPTH = ${DEPTH}( |$)" "${WORKSPACE}/train.py" \
    || die "DEPTH patch failed — check that train.py contains a line starting 'DEPTH = <N>'"

# Patch DEVICE_BATCH_SIZE
log "Patching DEVICE_BATCH_SIZE=${DEVICE_BATCH_SIZE} in workspace train.py ..."
sed -i "s/^DEVICE_BATCH_SIZE = [0-9]\+/DEVICE_BATCH_SIZE = ${DEVICE_BATCH_SIZE}/" "${WORKSPACE}/train.py"
grep -qE "^DEVICE_BATCH_SIZE = ${DEVICE_BATCH_SIZE}( |$)" "${WORKSPACE}/train.py" \
    || die "DEVICE_BATCH_SIZE patch failed — check train.py format"

# Patch TIME_BUDGET in workspace prepare.py so train.py respects the per-run time budget
log "Patching TIME_BUDGET=${TIME_BUDGET_SECS} in workspace prepare.py ..."
sed -i "s/^TIME_BUDGET = [0-9]\+/TIME_BUDGET = ${TIME_BUDGET_SECS}/" "${WORKSPACE}/prepare.py"
grep -qE "^TIME_BUDGET = ${TIME_BUDGET_SECS}( |$)" "${WORKSPACE}/prepare.py" \
    || die "TIME_BUDGET patch failed — check that prepare.py contains a line 'TIME_BUDGET = <N>'"

log "Patches verified."

# Apply FA3 → SDPA fallback patch (safe no-op on Ampere/Hopper)
python3 /app/patch-train.py "${WORKSPACE}/train.py"

# Override program.md if research direction is specified
if [[ -n "${AUTORESEARCH_RESEARCH_DIRECTION:-}" ]]; then
    log "Overriding ${WORKSPACE}/program.md with AUTORESEARCH_RESEARCH_DIRECTION"
    printf '%s\n' "${AUTORESEARCH_RESEARCH_DIRECTION}" > "${WORKSPACE}/program.md"
fi

# ── 8. Agent loop ─────────────────────────────────────────────────────────────
FAILED=0

if [[ -n "${AUTORESEARCH_AGENT_S3_KEY:-}" && -n "${S3_ENDPOINT_URL:-}" ]]; then
    log "Downloading custom agent: ${AUTORESEARCH_AGENT_S3_KEY}"
    python3 -c "
import boto3, os
s3 = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT_URL'],
    aws_access_key_id=os.environ['S3_ACCESS_KEY'],
    aws_secret_access_key=os.environ['S3_SECRET_KEY'])
s3.download_file('runs', os.environ['AUTORESEARCH_AGENT_S3_KEY'], '/tmp/custom_agent.py')
"
    AGENT_SCRIPT="/tmp/custom_agent.py"
else
    log "Using built-in agent."
    AGENT_SCRIPT="/app/agent_loop.py"
fi

set +e
python3 "${AGENT_SCRIPT}" \
    --workspace      "${WORKSPACE}" \
    --output-dir     "${OUTPUT_DIR}" \
    --run-id         "${RUN_ID}" \
    --max-iterations "${MAX_ITERATIONS}" \
    --time-budget    "${TIME_BUDGET_SECS}" \
    2>&1 | tee "${OUTPUT_DIR}/agent-${RUN_ID}.log"
[[ ${PIPESTATUS[0]} -ne 0 ]] && FAILED=1
set -e

# ── 9. Summary ────────────────────────────────────────────────────────────────
END_EPOCH=$(date +%s)
END_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ELAPSED=$(( END_EPOCH - START_EPOCH ))

SUMMARY="${OUTPUT_DIR}/summary-${RUN_ID}.txt"
cat > "${SUMMARY}" <<EOF
run_id=${RUN_ID}
date=${END_ISO}
depth=${DEPTH}
device_batch_size=${DEVICE_BATCH_SIZE}
num_shards=${NUM_SHARDS}
iterations=${MAX_ITERATIONS}
failed=${FAILED}
dataset_hf_repo=${DATASET_HF_REPO}
wall_clock_seconds=${ELAPSED}
EOF

log "Summary → ${SUMMARY}"
cat "${SUMMARY}"

# ── 10. Artifact contract + S3 upload ─────────────────────────────────────────
export _ELAPSED_S="${ELAPSED}"
export _START_ISO="${START_ISO}"
export _END_ISO="${END_ISO}"
export _FAILED="${FAILED}"

python3 - <<'PYEOF'
import glob
import json
import os
import re
import socket
from pathlib import Path

run_id = os.environ["AUTORESEARCH_RUN_ID"]
output_dir = os.environ["AUTORESEARCH_OUTPUT_DIR"]
artifact_dir = Path(output_dir) / run_id
artifact_dir.mkdir(parents=True, exist_ok=True)

max_iterations = int(os.environ.get("AUTORESEARCH_MAX_ITERATIONS", 0))
elapsed_s = int(os.environ.get("_ELAPSED_S", 0))
start_iso = os.environ.get("_START_ISO", "")
end_iso = os.environ.get("_END_ISO", "")
failed = int(os.environ.get("_FAILED", 0))
parent_candidate_id = os.environ.get("AUTORESEARCH_PARENT_CANDIDATE_ID", "") or None
parent_metric_str = os.environ.get("AUTORESEARCH_PARENT_METRIC_VALUE", "")
parent_metric_value = float(parent_metric_str) if parent_metric_str else None
gen_id = os.environ.get("AUTORESEARCH_GENERATION_ID", "ungrouped")
time_budget = int(os.environ.get("TIME_BUDGET_SECS", 300))

# Extract best val_bpb across all iteration logs
iter_logs = sorted(glob.glob(f"{output_dir}/iter-*-{run_id}.log"))
best_val_bpb = None
for log_path in iter_logs:
    try:
        with open(log_path) as f:
            for line in f:
                m = re.search(r'val_bpb:\s+([0-9.]+)', line)
                if m:
                    v = float(m.group(1))
                    if best_val_bpb is None or v < best_val_bpb:
                        best_val_bpb = v
    except OSError:
        pass
if best_val_bpb is None:
    best_val_bpb = 0.0

worker_id = socket.gethostname()

# run.json
run_doc = {
    "run_id": run_id,
    "candidate_id": run_id,
    "agent_id": "autoresearch-agent",
    "worker_id": worker_id,
    "resource_class": "gpu-worker",
    "seed": 0,
    "status": "completed" if not failed else "failed",
    "training_budget": {
        "max_iterations": max_iterations,
        "time_budget_secs": time_budget,
    },
    "wall_clock_used_seconds": elapsed_s,
    "created_at": start_iso,
    "completed_at": end_iso,
}
(artifact_dir / "run.json").write_text(json.dumps(run_doc, indent=2))

# metrics.json
metrics_doc = {
    "primary_metric": {
        "name": "val_bpb",
        "direction": "min",
        "value": best_val_bpb,
    }
}
(artifact_dir / "metrics.json").write_text(json.dumps(metrics_doc, indent=2))

# lineage.json
lineage_doc = {
    "candidate_id": run_id,
    "parent_candidate_id": parent_candidate_id,
    "parent_primary_metric_value": parent_metric_value,
    "is_seed_run": parent_candidate_id is None,
}
(artifact_dir / "lineage.json").write_text(json.dumps(lineage_doc, indent=2))

# Copy final train.py into artifact dir for upload
workspace_dir = Path("/workspace") / run_id
train_src = workspace_dir / "train.py"
if train_src.exists():
    import shutil
    shutil.copy2(str(train_src), str(artifact_dir / "train.py"))

print(f"[entrypoint] Artifact contract written to {artifact_dir}")
print(f"[entrypoint] val_bpb={best_val_bpb}  elapsed={elapsed_s}s  gen={gen_id}")

# S3 upload (skipped gracefully if endpoint not set)
endpoint = os.environ.get("S3_ENDPOINT_URL", "")
if endpoint:
    import boto3
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    )
    for f in artifact_dir.rglob("*"):
        if f.is_file():
            key = f"generations/{gen_id}/{run_id}/{f.relative_to(artifact_dir)}"
            s3.upload_file(str(f), "runs", key)
            print(f"[entrypoint] Uploaded s3://runs/{key}")
    print(f"[entrypoint] S3 upload complete for run {run_id}")
else:
    print("[entrypoint] S3_ENDPOINT_URL not set, skipping upload")
PYEOF
