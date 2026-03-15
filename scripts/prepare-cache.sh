#!/usr/bin/env bash
# prepare-cache.sh — Download TinyStories and train the BPE tokenizer.
# Runs the container with AUTORESEARCH_MAX_ITERATIONS=0 so it exits after prepare.
#
# Usage:
#   ./scripts/prepare-cache.sh
#   HOST_CACHE_DIR=/data/cache NUM_SHARDS=5 ./scripts/prepare-cache.sh
#
# Env overrides:
#   HOST_CACHE_DIR   host path for cache mount   (~/hackathon/cache)
#   NUM_SHARDS       training shards to create   (3, ~700K stories total)
#   IMAGE_TAG        docker image                 (autoresearch-worker:latest)

set -euo pipefail

HOST_CACHE_DIR="${HOST_CACHE_DIR:-${HOME}/hackathon/cache}"
NUM_SHARDS="${NUM_SHARDS:-3}"
IMAGE_TAG="${IMAGE_TAG:-autoresearch-worker:latest}"

echo "[prepare-cache.sh] cache=${HOST_CACHE_DIR}  shards=${NUM_SHARDS}  image=${IMAGE_TAG}"
mkdir -p "${HOST_CACHE_DIR}"

docker run --rm \
    --gpus "device=0" \
    -v "${HOST_CACHE_DIR}:/artifacts/cache" \
    -v "${HOST_CACHE_DIR}:/artifacts/output" \
    -e AUTORESEARCH_MAX_ITERATIONS=0 \
    -e AUTORESEARCH_NUM_SHARDS="${NUM_SHARDS}" \
    -e AUTORESEARCH_RUN_ID="prepare" \
    "${IMAGE_TAG}"

echo ""
echo "[prepare-cache.sh] Cache contents:"
ls -lh "${HOST_CACHE_DIR}/autoresearch/" 2>/dev/null || echo "  (cache dir not populated yet)"
