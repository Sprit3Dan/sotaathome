#!/usr/bin/env bash
# run.sh — Run the autoresearch-worker (TinyStories + tiny GPT) on one GPU.
#
# Usage:
#   ./scripts/run.sh
#   GPU_INDEX=1 DEPTH=6 DEVICE_BATCH_SIZE=16 ./scripts/run.sh
#
# Key env overrides:
#   GPU_INDEX            GPU device index (docker --gpus device=N)     (0)
#   HOST_CACHE_DIR       host path for dataset/tokenizer/HF cache      (~/hackathon/cache)
#   HOST_OUTPUT_DIR      host path for logs + summaries                 (~/hackathon/output)
#   RUN_ID               unique label                                   (run-<timestamp>)
#   IMAGE_TAG            docker image                                   (autoresearch-worker:latest)
#
# Model config (passed to autoresearch train.py via entrypoint sed-patches):
#   DEPTH                n_layers; model_dim=DEPTH×64 rounded to 128   (4)
#                          DEPTH=4  →  256 dim,  2 heads,  ~10M params
#                          DEPTH=6  →  384 dim,  3 heads,  ~25M params
#                          DEPTH=8  →  512 dim,  4 heads,  ~45M params
#   DEVICE_BATCH_SIZE    sequences per micro-step (reduce if OOM)       (32)
#
# Run control:
#   AUTORESEARCH_MAX_ITERATIONS  training iterations (0=prepare-only)  (3)
#   AUTORESEARCH_NUM_SHARDS      TinyStories training shards to use     (3)

set -euo pipefail

GPU_INDEX="${GPU_INDEX:-0}"
HOST_CACHE_DIR="${HOST_CACHE_DIR:-${HOME}/hackathon/cache}"
HOST_OUTPUT_DIR="${HOST_OUTPUT_DIR:-${HOME}/hackathon/output}"
RUN_ID="${RUN_ID:-run-$(date +%Y%m%d-%H%M%S)}"
IMAGE_TAG="${IMAGE_TAG:-autoresearch-worker:latest}"
DEPTH="${DEPTH:-4}"
DEVICE_BATCH_SIZE="${DEVICE_BATCH_SIZE:-8}"
MAX_ITERATIONS="${AUTORESEARCH_MAX_ITERATIONS:-3}"
NUM_SHARDS="${AUTORESEARCH_NUM_SHARDS:-3}"

echo "[run.sh] RUN_ID=${RUN_ID}  GPU=device:${GPU_INDEX}  image=${IMAGE_TAG}"
echo "[run.sh] model: DEPTH=${DEPTH}  DEVICE_BATCH_SIZE=${DEVICE_BATCH_SIZE}"
echo "[run.sh] data: NUM_SHARDS=${NUM_SHARDS}  iterations=${MAX_ITERATIONS}"
echo "[run.sh] cache=${HOST_CACHE_DIR}  output=${HOST_OUTPUT_DIR}"

mkdir -p "${HOST_CACHE_DIR}" "${HOST_OUTPUT_DIR}"

docker run --rm \
    --gpus "device=${GPU_INDEX}" \
    -v "${HOST_CACHE_DIR}:/artifacts/cache" \
    -v "${HOST_OUTPUT_DIR}:/artifacts/output" \
    -e AUTORESEARCH_RUN_ID="${RUN_ID}" \
    -e AUTORESEARCH_MAX_ITERATIONS="${MAX_ITERATIONS}" \
    -e AUTORESEARCH_NUM_SHARDS="${NUM_SHARDS}" \
    -e DEPTH="${DEPTH}" \
    -e DEVICE_BATCH_SIZE="${DEVICE_BATCH_SIZE}" \
    "${IMAGE_TAG}"

echo ""
echo "[run.sh] Summary:"
cat "${HOST_OUTPUT_DIR}/summary-${RUN_ID}.txt" 2>/dev/null || echo "  (no summary — check logs)"
