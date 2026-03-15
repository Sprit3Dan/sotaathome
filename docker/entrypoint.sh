#!/usr/bin/env bash
# entrypoint.sh — autoresearch-worker container entrypoint (TinyStories edition)
#
# Phases:
#   1. Validate bind-mounts
#   2. Redirect ~/.cache/autoresearch → /artifacts/cache/autoresearch
#   3. prepare-tinystories.py  — create TinyStories parquet shards
#   4. prepare.py              — train BPE tokenizer (skips if already done)
#   5. exit if MAX_ITERATIONS=0 (prepare-only)
#   6. Copy workspace, patch DEPTH + DEVICE_BATCH_SIZE
#   7. Training loop (N × train.py)
#   8. Write summary

set -euo pipefail

log() { echo "[entrypoint] $*"; }
die() { echo "[entrypoint] ERROR: $*" >&2; exit 1; }

CACHE_DIR="${AUTORESEARCH_CACHE_DIR}"
OUTPUT_DIR="${AUTORESEARCH_OUTPUT_DIR}"
RUN_ID="${AUTORESEARCH_RUN_ID}"
NUM_SHARDS="${AUTORESEARCH_NUM_SHARDS}"
MAX_ITERATIONS="${AUTORESEARCH_MAX_ITERATIONS}"

# ── 1. Validate mounts ────────────────────────────────────────────────────────
[[ -d "${CACHE_DIR}" ]]  || die "Cache mount missing. Run with: -v <host>:${CACHE_DIR}"
[[ -d "${OUTPUT_DIR}" ]] || die "Output mount missing. Run with: -v <host>:${OUTPUT_DIR}"
log "CACHE_DIR=${CACHE_DIR}  OUTPUT_DIR=${OUTPUT_DIR}  RUN_ID=${RUN_ID}"

# ── 2. Cache symlink ──────────────────────────────────────────────────────────
# autoresearch hard-codes ~/.cache/autoresearch; redirect it to our bind-mount
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

# ── 3. prepare-tinystories.py ─────────────────────────────────────────────────
log "Running prepare-tinystories.py --num-shards ${NUM_SHARDS} ..."
python3 /app/prepare-tinystories.py --num-shards "${NUM_SHARDS}" \
    2>&1 | tee "${OUTPUT_DIR}/prepare-ts-${RUN_ID}.log"

# ── 4. Tokenizer training (autoresearch prepare.py) ───────────────────────────
TOKENIZER_DIR="${AR_CACHE}/tokenizer"
if [[ -f "${TOKENIZER_DIR}/tokenizer.pkl" && -f "${TOKENIZER_DIR}/token_bytes.pt" ]]; then
    log "Tokenizer already trained, skipping prepare.py."
else
    log "Training BPE tokenizer via prepare.py --num-shards ${NUM_SHARDS} ..."
    (cd /opt/autoresearch-upstream && python prepare.py --num-shards "${NUM_SHARDS}") \
        2>&1 | tee -a "${OUTPUT_DIR}/prepare-ts-${RUN_ID}.log"
fi

# ── 5. Prepare-only mode ──────────────────────────────────────────────────────
if [[ "${MAX_ITERATIONS}" -eq 0 ]]; then
    log "AUTORESEARCH_MAX_ITERATIONS=0: prepare-only mode, done."
    exit 0
fi

# ── 6. Workspace copy + patches ───────────────────────────────────────────────
WORKSPACE="/workspace/${RUN_ID}"
log "Copying upstream to workspace: ${WORKSPACE}"
mkdir -p /workspace
cp -r /opt/autoresearch-upstream "${WORKSPACE}"

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

log "Patches verified."

# Apply FA3 → SDPA fallback patch (safe no-op on Ampere/Hopper)
python3 /app/patch-train.py "${WORKSPACE}/train.py"

# ── 7. Training loop ──────────────────────────────────────────────────────────
FAILED=0

for (( i=1; i<=MAX_ITERATIONS; i++ )); do
    ITER_TAG="$(printf '%03d' ${i})"
    ITER_LOG="${OUTPUT_DIR}/iter-${ITER_TAG}-${RUN_ID}.log"
    log "--- Iteration ${i}/${MAX_ITERATIONS} → ${ITER_LOG}"

    set +e
    ( cd "${WORKSPACE}" && CUDA_VISIBLE_DEVICES=0 python train.py ) \
        2>&1 | tee "${ITER_LOG}"
    EXIT_CODE=${PIPESTATUS[0]}
    set -e

    if [[ ${EXIT_CODE} -ne 0 ]]; then
        log "WARNING: train.py exited ${EXIT_CODE} on iteration ${i}."
        FAILED=1
        break
    fi

    # Extract summary metrics (best-effort)
    VAL_BPB=$(grep -oP 'val_bpb:\s+\K[0-9.]+' "${ITER_LOG}"   | tail -1 || echo "N/A")
    VRAM_MB=$(grep -oP 'peak_vram_mb:\s+\K[0-9.]+' "${ITER_LOG}" | tail -1 || echo "N/A")
    MFU=$(grep -oP 'mfu_percent:\s+\K[0-9.]+' "${ITER_LOG}"    | tail -1 || echo "N/A")
    TOKENS_M=$(grep -oP 'total_tokens_M:\s+\K[0-9.]+' "${ITER_LOG}" | tail -1 || echo "N/A")
    log "iter=${i} val_bpb=${VAL_BPB} peak_vram_mb=${VRAM_MB} mfu_percent=${MFU} total_tokens_M=${TOKENS_M}"
done

# ── 8. Summary ────────────────────────────────────────────────────────────────
SUMMARY="${OUTPUT_DIR}/summary-${RUN_ID}.txt"
cat > "${SUMMARY}" <<EOF
run_id=${RUN_ID}
date=$(date -u +%Y-%m-%dT%H:%M:%SZ)
depth=${DEPTH}
device_batch_size=${DEVICE_BATCH_SIZE}
num_shards=${NUM_SHARDS}
iterations=${MAX_ITERATIONS}
failed=${FAILED}
EOF

log "Summary → ${SUMMARY}"
cat "${SUMMARY}"
