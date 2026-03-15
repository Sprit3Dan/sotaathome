# Orchestration System

The `orchestration/` directory contains the server that coordinates training jobs
across the k3s cluster. It is the primary interface between external agents and
the GPU workers running on turtle.

---

## Components

```
orchestration/
  server.py       FastAPI server — API, Redis queue, watcher thread startup
  main.py         Standalone queue-poller (deprecated path; server.py is used)
  agent.py        LLM-based InitContainerSpec generator (OpenAI + tool calls)
  k8s_deployer.py Kubernetes Job creation and monitoring
  models.py       Pydantic data models (ResearchItem, AutoresearchJobRequest, etc.)
  settings.py     Centralized env-var config
  Dockerfile      Image for the orchestration server itself
  buildAndPush.sh Build and push orchestration image to GHCR
```

---

## How it works end-to-end

### 1. Job submission (`POST /submit`)

An external caller (agent or human) POSTs an `AutoresearchJobRequest`.
The server:
- Generates a `generation_id` (8-char hex)
- Writes a `manifest.json` to MinIO at `s3://runs/generations/<gen_id>/manifest.json`
- Stores generation state in Redis at `generation:<gen_id>`
- Enqueues a `GitHubResearchItem` (wrapping `karpathy/autoresearch`) onto the Redis queue

### 2. Background queue worker

A daemon thread (`_process_queue_forever`) polls the Redis queue.
For each task:
- If `init_container_spec` is pre-built (it always is for `/submit` jobs): uses it directly
- If not: calls `agent.py` which asks OpenAI to generate an `InitContainerSpec`
- Calls `k8s_deployer.deploy_research_job()` which creates a Kubernetes Job and blocks until completion

### 3. Kubernetes Job execution

`k8s_deployer.py` creates a Kubernetes `Job` with:
- `completions=n` and `parallelism=n` (n parallel pods, one per generation "slot")
- Pods run `ghcr.io/sprit3dan/sotaathome:latest` (the training image)
- Node pinned to `turtle`, RuntimeClass `nvidia`
- Host-path mounts for `/artifacts/cache` and `/artifacts/output`
- S3 credentials injected from `minio-credentials` Kubernetes Secret
- OpenAI API key injected from `orchestrator-secrets` Kubernetes Secret

Each pod runs `docker/entrypoint.sh` which:
1. Validates mounts
2. Prepares the HF dataset (parquet shards via `prepare-dataset.py`)
3. Trains the BPE tokenizer via `prepare.py` (skipped if cached)
4. Copies the autoresearch workspace and patches `DEPTH` and `DEVICE_BATCH_SIZE`
5. Writes `research_direction` into `program.md` if provided
6. Runs the agent loop (`agent_loop.py` or a custom `agent_script` from S3)
7. Writes summary and artifact contract (`run.json`, `metrics.json`, `lineage.json`, `train.py`)
8. Uploads all artifacts to MinIO under `s3://runs/generations/<gen_id>/<run_id>/`

### 4. Generation watcher

A second daemon thread (`evaluator/watcher.py`) polls Redis every 10 seconds.
For each `generation:<gen_id>` with status `"running"`:
- Counts `run.json` uploads in MinIO to detect pod completions
- When `pods_done == expected_pods`:
  - Runs the full evaluator pipeline (validate → score → aggregate → promote → frontier → allocate)
  - Writes evaluator outputs to `s3://runs/evaluations/<gen_id>/`
  - Stores `best_val_bpb` and `best_run_id` in Redis
  - If more generations remain: re-POSTs to `/submit` with parent candidate info
  - If final generation: marks status `"done"`

### 5. Result retrieval

`GET /generation/<gen_id>/train/<run_id>` serves the `train.py` artifact
from `s3://runs/generations/<gen_id>/<run_id>/train.py` as plain text.

---

## API reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/submit` | Submit an autoresearch job (preferred entrypoint) |
| `GET` | `/cluster_status` | Tasks, jobs, nodes, and generation progress |
| `GET` | `/task/<task_id>` | Status and logs for a specific task |
| `GET` | `/tasks` | List all tracked tasks |
| `GET` | `/generation/<gen_id>/train/<run_id>` | Fetch final `train.py` as plain text |
| `POST` | `/enqueue` | Low-level: push a `ResearchItem` onto the queue |
| `GET` | `/dequeue` | Low-level: pop a task from the queue |
| `POST` | `/update_status` | Low-level: update task status |
| `POST` | `/execute` | Low-level: run a task synchronously (bypasses queue) |
| `GET` | `/status` | Queue depth |

For external agents, only `/submit`, `/cluster_status`, `/task/<id>`,
and `/generation/<gen_id>/train/<run_id>` are relevant.

---

## Generation status lifecycle

```
running
  └─► evaluating
        └─► evaluated
              ├─► next_gen_submitted   (more generations remain → auto-loops)
              └─► done                (final generation complete)
```

Error states: `eval_failed`, `next_gen_submit_failed`.

---

## MinIO / S3 layout

```
runs/
  generations/
    <gen_id>/
      manifest.json
      <run_id>/
        run.json          ← artifact contract: operational metadata
        metrics.json      ← primary metric (val_bpb)
        lineage.json      ← parent candidate linkage
        train.py          ← final train.py the pod executed
        agent-<run_id>.log
        prepare-ds-<run_id>.log
  agents/
    <gen_id>/
      agent.py            ← custom agent script (if supplied)
  evaluations/
    <gen_id>/
      runs.json
      aggregates.json
      promotions.json
      frontier.json
      next_jobs.json
      allocation_summary.json
```

---

## Redis key layout

| Key | Type | Contents |
|---|---|---|
| `training_queue` | List | Serialized `ResearchItem` JSON blobs |
| `task:<task_id>` | Hash | `status`, `repo_ref`, `research_direction`, `logs`, `pod_name`, `generation_id` |
| `generation:<gen_id>` | Hash | `status`, `generation_num`, `total_generations`, `expected_pods`, `pods_done`, `best_val_bpb`, `best_run_id`, `request_json` |

---

## Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `OPENAI_MODEL` | `gpt-5.4` | Model for LLM-based spec generation |
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `QUEUE_NAME` | `training_queue` | Redis list name |
| `K8S_NAMESPACE` | `default` | Kubernetes namespace |
| `KUBECONFIG` | (unset) | Path to kubeconfig; falls back to in-cluster config |
| `MAX_RETRIES` | `3` | Max pod launch retries before permanent failure |
| `POLL_INTERVAL` | `5` | Seconds between queue polls |
| `S3_ENDPOINT_URL` | `http://minio:9000` | MinIO endpoint |
| `S3_ACCESS_KEY` | (required) | MinIO access key |
| `S3_SECRET_KEY` | (required) | MinIO secret key |
| `GITHUB_TOKEN` | (optional) | For LLM repo exploration |
| `HF_TOKEN` | (optional) | For HuggingFace repo exploration |

---

## Kubernetes secrets required

| Secret name | Keys | Used by |
|---|---|---|
| `minio-credentials` | `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | Training pods |
| `orchestrator-secrets` | `OPENAI_API_KEY` | Training pods (agent loop) |
| `ghcr-secret` | (docker registry) | Image pull from GHCR |

---

## Adding a new API endpoint

1. Add the route to `orchestration/server.py`
2. Add any new models to `orchestration/models.py`
3. Rebuild and push: `./orchestration/buildAndPush.sh`
4. Redeploy: `./infra/deploy.sh` (Pulumi) or apply the updated Kubernetes manifests

---

## Notes

- The `main.py` standalone poller is superseded by the background worker thread
  in `server.py`. It still works as a standalone process but is not the active path.
- Failed Kubernetes jobs are preserved (not deleted) so logs can be inspected.
  Successful jobs are deleted after 1 hour (`ttlSecondsAfterFinished=3600`).
- All pods are pinned to `node_name="turtle"` in `k8s_deployer.py`. If you add more
  nodes, this needs to be made dynamic.
