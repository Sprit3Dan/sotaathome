# SoTA@Home Agent Skill

How an external agent submits training jobs and retrieves results.

The system is a multi-generation autoresearch loop orchestrated through a FastAPI server.
You submit once, and the server handles all generations automatically — including
evaluation, parent selection, and re-submission of subsequent generations.

---

## System overview

```
External agent
     │  POST /submit
     ▼
Orchestration server (FastAPI)
     │  enqueues task
     ▼
Redis queue → background worker → Kubernetes Job (n pods on turtle)
                                        │  each pod:
                                        │  • prepares dataset (HF)
                                        │  • runs agent loop (train.py mutations)
                                        │  • uploads artifacts to MinIO S3
                                        ▼
                                  Watcher thread (polls every 10s)
                                        │  when all pods done:
                                        │  • runs evaluator
                                        │  • picks best candidates (frontier)
                                        │  • re-submits next generation (if any)
                                        ▼
                                 Status: "done" when final generation completes
```

---

## Step 1 — Submit a job

**Endpoint:** `POST /submit`

**Request body** (`AutoresearchJobRequest`):

| Field | Type | Default | Description |
|---|---|---|---|
| `dataset_hf_repo` | str | `"roneneldan/TinyStories"` | HuggingFace dataset repo |
| `dataset_text_column` | str | `"text"` | Column containing text |
| `dataset_train_split` | str | `"train"` | Training split name |
| `dataset_val_split` | str | `"validation"` | Validation split name |
| `research_direction` | str \| null | null | Free-text hypothesis; written into `program.md` for the agent |
| `n` | int | `1` | Parallel pods per generation (one per GPU run) |
| `m` | int | `10` | Max agent iterations per pod |
| `t` | int | `300` | Time budget in seconds per pod |
| `generations` | int | `1` | Total number of generations to run end-to-end |
| `agent_script` | str \| null | null | Custom Python agent source code (uploaded to S3; replaces built-in `agent_loop.py`) |

Do **not** set `generation_num`, `parent_candidate_ids`, or `parent_metric_values` —
these are populated internally by the watcher on re-submission.

**Example:**

```bash
curl -X POST http://<orchestrator>/submit \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_hf_repo": "roneneldan/TinyStories",
    "research_direction": "Try reducing learning rate to 3e-4 and see if val_bpb improves",
    "n": 3,
    "m": 5,
    "t": 300,
    "generations": 3
  }'
```

**Response:**

```json
{
  "status": "success",
  "generation_id": "a1b2c3d4",
  "task_id": "e5f6a7b8...",
  "generation_num": 1,
  "total_generations": 3
}
```

Save `generation_id` — you need it to check status and retrieve results.

---

## Step 2 — Poll for completion

**Endpoint:** `GET /cluster_status`

Returns all tracked generations with their current status and best metric.

```bash
curl http://<orchestrator>/cluster_status
```

Relevant fields under `generations[]`:

| Field | Description |
|---|---|
| `gen_id` | The `generation_id` from `/submit` |
| `generation_num` | Which generation this is (1-indexed) |
| `total_generations` | Total generations requested |
| `status` | See lifecycle below |
| `pods_done` | Completed pods so far |
| `expected_pods` | Total pods in this generation |
| `best_val_bpb` | Best validation bits-per-byte across all runs (lower is better) |
| `best_run_id` | Run ID that achieved `best_val_bpb` |

**Generation status lifecycle:**

```
running → evaluating → evaluated → next_gen_submitted   (if more gens remain)
                                 → done                 (if final generation)
```

- `running`: pods executing
- `evaluating`: watcher triggered evaluation
- `evaluated`: evaluator complete, best metrics stored
- `next_gen_submitted`: next generation enqueued (watcher auto-handles)
- `done`: final generation complete — you can retrieve results

To check a specific task:

```bash
curl http://<orchestrator>/task/<task_id>
```

---

## Step 3 — Retrieve the best train.py

Once the final generation reaches status `"done"` or `"evaluated"`:

1. Read `best_run_id` from the generation's cluster_status entry.
2. Fetch `train.py` for that run:

```bash
curl http://<orchestrator>/generation/<gen_id>/train/<best_run_id>
```

Returns the final `train.py` as plain text (the exact file the winning pod executed,
with all agent-applied patches).

**Example (full flow):**

```bash
# 1. Submit
GEN_ID=$(curl -s -X POST http://<orchestrator>/submit \
  -H "Content-Type: application/json" \
  -d '{"n": 4, "m": 8, "t": 300, "generations": 2}' \
  | jq -r '.generation_id')

# 2. Poll until done
while true; do
  STATUS=$(curl -s http://<orchestrator>/cluster_status \
    | jq -r --arg g "$GEN_ID" '.generations[] | select(.gen_id==$g) | .status')
  echo "Status: $STATUS"
  [[ "$STATUS" == "done" ]] && break
  sleep 30
done

# 3. Get best run ID and fetch train.py
BEST_RUN=$(curl -s http://<orchestrator>/cluster_status \
  | jq -r --arg g "$GEN_ID" '.generations[] | select(.gen_id==$g) | .best_run_id')

curl http://<orchestrator>/generation/$GEN_ID/train/$BEST_RUN > best_train.py
```

---

## Supplying a custom agent script

If you want to control how the agent mutates `train.py`, pass your Python script
as `agent_script` in the submit request. The orchestrator uploads it to S3 and
the pod downloads and runs it instead of the built-in `agent_loop.py`.

Your script receives these CLI args:

```
--workspace <path>       Path to the working copy of the autoresearch repo
--output-dir <path>      Where to write logs
--run-id <str>           Unique run identifier
--max-iterations <int>   Maximum agent iterations
--time-budget <int>      Time budget in seconds
```

```bash
AGENT=$(cat my_agent.py)

curl -X POST http://<orchestrator>/submit \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg script "$AGENT" \
    --arg direction "Try reducing dropout" \
    '{n:2, m:5, t:300, generations:1, research_direction:$direction, agent_script:$script}')"
```

---

## What each pod produces

Every pod writes artifacts to S3 at `s3://runs/generations/<gen_id>/<run_id>/`:

| File | Contents |
|---|---|
| `run.json` | Operational metadata (status, wall clock, worker) |
| `metrics.json` | Primary metric: `val_bpb` (lower is better) |
| `lineage.json` | Parent candidate linkage for evaluator |
| `train.py` | Final train.py the pod executed |
| `agent-<run_id>.log` | Full agent loop output |
| `prepare-ds-<run_id>.log` | Dataset preparation log |

The evaluator runs automatically when all pods in a generation complete.
Its outputs land at `s3://runs/evaluations/<gen_id>/`:
`runs.json`, `aggregates.json`, `promotions.json`, `frontier.json`,
`next_jobs.json`, `allocation_summary.json`.

---

## Key facts

- **Metric**: `val_bpb` (validation bits per byte) — lower is better.
- **Multi-generation**: fully automatic. Submit once with `generations=N`.
  The watcher evaluates each generation, picks the best parent candidates
  (gold/silver frontier), and re-submits the next generation automatically.
- **Parallelism**: `n` controls how many pods run in parallel per generation.
  Each pod runs on one GPU on turtle.
- **OOM**: if pods OOM, the job is marked `failed`. The deployer preserves failed
  pods for log inspection. Retry with a smaller `DEVICE_BATCH_SIZE` (not currently
  a submit param — requires a deployer config change).
- **Evaluation is not your job**: do not run `scripts/evaluate.sh` or the evaluator CLI
  manually. The watcher handles it automatically after each generation.
