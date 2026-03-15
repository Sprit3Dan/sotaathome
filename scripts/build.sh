#!/usr/bin/env bash
# build.sh — Build the autoresearch-worker Docker image on this machine.
# Run on the worker node (turtle) after git pull.
#
# Usage:
#   ./scripts/build.sh
#   IMAGE_TAG=autoresearch-worker:v2 ./scripts/build.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-autoresearch-worker:latest}"

echo "[build.sh] Building ${IMAGE_TAG} from ${REPO_ROOT}"

docker build \
    --progress=plain \
    -t "${IMAGE_TAG}" \
    -f "${REPO_ROOT}/docker/Dockerfile" \
    "${REPO_ROOT}"

echo "[build.sh] Done: ${IMAGE_TAG}"
docker images "${IMAGE_TAG}"
