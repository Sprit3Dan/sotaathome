#!/bin/sh
set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/sprit3dan/sotaathome-orchestrator:latest \
  -f "$SCRIPT_DIR/Dockerfile" \
  "$REPO_ROOT" \
  --push
