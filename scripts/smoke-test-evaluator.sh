#!/usr/bin/env bash
# Smoke-test the evaluator using the k3s-test-001 training outputs on turtle.
# Pulls the iter logs, synthesizes run.json/metrics.json/lineage.json, runs evaluator.
set -euo pipefail

TURTLE_OUTPUT="/root/hackathon/output"
LOCAL_TMP="$(mktemp -d)"
RUN_DIR="${LOCAL_TMP}/k3s-test-001"
EVAL_OUT="${LOCAL_TMP}/eval-out"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "[smoke-test] Fetching outputs from turtle → ${RUN_DIR}"
mkdir -p "${RUN_DIR}"

# Copy logs from turtle (need sudo because files are owned by root)
ssh alex@turtle.local "echo alexai6787 | sudo -S cat ${TURTLE_OUTPUT}/summary-k3s-test-001.txt" \
    > "${RUN_DIR}/summary.txt"
ssh alex@turtle.local "echo alexai6787 | sudo -S cat ${TURTLE_OUTPUT}/iter-002-k3s-test-001.log" \
    > "${RUN_DIR}/iter-002.log"

echo "[smoke-test] Parsing metrics from iter-002 log"
VAL_BPB=$(grep 'val_bpb:' "${RUN_DIR}/iter-002.log" | awk '{print $2}' | tr -d '[:space:]')
TRAIN_LOSS=$(grep 'step 00038' "${RUN_DIR}/iter-002.log" | grep -oE 'loss: [0-9.]+' | tail -1 | awk '{print $2}' || echo "3.727")
MFU=$(grep 'mfu_percent:' "${RUN_DIR}/iter-002.log" | awk '{print $2}' | tr -d '[:space:]')
TOKENS=$(grep 'total_tokens_M:' "${RUN_DIR}/iter-002.log" | awk '{print $2}' | tr -d '[:space:]')
WALL=$(grep 'training_seconds:' "${RUN_DIR}/iter-002.log" | awk '{print $2}' | tr -d '[:space:]')

echo "[smoke-test] val_bpb=${VAL_BPB} train_loss=${TRAIN_LOSS} mfu=${MFU} tokens=${TOKENS} wall=${WALL}"

echo "[smoke-test] Writing run.json"
cat > "${RUN_DIR}/run.json" <<EOF
{
  "run_id": "k3s-test-001",
  "candidate_id": "cand-k3s-001",
  "agent_id": "agent-smoke",
  "worker_id": "turtle",
  "resource_class": "2060-12gb",
  "model_family": "gpt2-small",
  "task_type": "tinystories",
  "seed": 42,
  "status": "completed",
  "training_budget": {
    "target_steps": 39,
    "normalized_budget_units": 1.0,
    "max_wall_clock_seconds": 300
  },
  "wall_clock_used_seconds": ${WALL%%.*},
  "created_at": "2026-03-15T10:13:00Z",
  "completed_at": "2026-03-15T11:04:32Z"
}
EOF

echo "[smoke-test] Writing metrics.json"
cat > "${RUN_DIR}/metrics.json" <<EOF
{
  "primary_metric": {
    "name": "val_bpb",
    "direction": "min",
    "value": ${VAL_BPB}
  },
  "secondary_metrics": {
    "train_loss": ${TRAIN_LOSS},
    "mfu_percent": ${MFU},
    "total_tokens_M": ${TOKENS}
  }
}
EOF

echo "[smoke-test] Writing lineage.json (seed run — no parent)"
cat > "${RUN_DIR}/lineage.json" <<EOF
{
  "candidate_id": "cand-k3s-001",
  "is_seed_run": true,
  "parent_candidate_id": null,
  "parent_primary_metric_value": null,
  "mutation_type": "baseline"
}
EOF

echo "[smoke-test] Running evaluator"
mkdir -p "${EVAL_OUT}"
cd "${REPO_ROOT}"
python3 -m evaluator.cli --input-dir "${LOCAL_TMP}" --output-dir "${EVAL_OUT}"

echo ""
echo "[smoke-test] === Results ==="
for f in runs aggregates promotions frontier next_jobs allocation_summary; do
    if [[ -f "${EVAL_OUT}/${f}.json" ]]; then
        echo ""
        echo "--- ${f}.json ---"
        cat "${EVAL_OUT}/${f}.json"
    fi
done

echo ""
echo "[smoke-test] Done. Full output in ${EVAL_OUT}"
