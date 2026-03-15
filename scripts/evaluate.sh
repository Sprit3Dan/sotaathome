#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <input-dir> <output-dir> [config.json]"
  exit 1
fi

INPUT_DIR="$1"
OUTPUT_DIR="$2"
CONFIG_PATH="${3:-}"

if [[ -n "$CONFIG_PATH" ]]; then
  python3 -m evaluator.cli --input-dir "$INPUT_DIR" --output-dir "$OUTPUT_DIR" --config "$CONFIG_PATH"
else
  python3 -m evaluator.cli --input-dir "$INPUT_DIR" --output-dir "$OUTPUT_DIR"
fi
