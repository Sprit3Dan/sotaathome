# Repo Structure

```
SAxFS-hackathon/
├── AGENTS.md                     # Agent guidance: design principles, roadmap
├── README.md                     # Human-facing project overview
├── skill.md                      # External agent skill: how to submit jobs and get results
├── join.sh                       # Worker node bootstrap entrypoint
├── leave.sh                      # Worker node cleanup
│
├── orchestration/
│   ├── server.py                 # FastAPI server: /submit, /cluster_status, queue worker, watcher startup
│   ├── main.py                   # Standalone queue-poller (superseded by server.py background thread)
│   ├── agent.py                  # LLM-based InitContainerSpec generator (OpenAI + tool calls)
│   ├── k8s_deployer.py           # Kubernetes Job creation and monitoring
│   ├── models.py                 # Pydantic models: ResearchItem, AutoresearchJobRequest, etc.
│   ├── settings.py               # Centralized env-var configuration
│   ├── Dockerfile                # Orchestration server image
│   └── buildAndPush.sh           # Build and push orchestration image to GHCR
│
├── evaluator/
│   ├── models.py                 # Evaluator data model and configuration
│   ├── loader.py                 # Artifact scanning/loading (filesystem + MinIO)
│   ├── validate.py               # Pass/fail validation for run eligibility
│   ├── score.py                  # Parent-relative primary metric scoring
│   ├── aggregate.py              # Candidate aggregation inside resource class
│   ├── promote.py                # Bronze/silver/gold decision logic
│   ├── frontier.py               # Frontier role construction
│   ├── allocate.py               # Next-iteration exploit/explore/verify planning
│   ├── watcher.py                # Daemon thread: polls MinIO, triggers eval, re-submits next gen
│   └── cli.py                    # Runnable CLI entrypoint (for manual evaluation only)
│
├── docker/
│   ├── Dockerfile                # autoresearch-worker image definition
│   └── entrypoint.sh             # Container entrypoint: prepare → agent loop → artifact upload
│
├── infra/
│   ├── __main__.py               # Pulumi program for cluster infrastructure
│   ├── deploy.sh                 # Deploy infrastructure via Pulumi
│   ├── kubeconfig.yaml           # Kubeconfig for the k3s cluster
│   ├── Pulumi.yaml               # Pulumi project config
│   ├── Pulumi.dev.yaml           # Dev stack config
│   └── requirements.txt          # Pulumi Python dependencies
│
├── scripts/
│   ├── build.sh                  # Build Docker image on worker node
│   ├── prepare-cache.sh          # Download dataset shards (prepare-only run)
│   ├── run.sh                    # Manual full training run wrapper (bypasses orchestrator)
│   ├── evaluate.sh               # Manual evaluator CLI wrapper (bypasses watcher)
│   └── smoke-test-evaluator.sh   # Smoke test for evaluator pipeline
│
└── docs/
    ├── repo-structure.md         # This file
    ├── orchestration.md          # Orchestration system: server, queue, deployer, watcher, API
    ├── autoresearch-worker.md    # Worker image design and runtime contract
    ├── artifact-contract.md      # Completed-run artifact contract (run.json, metrics.json, lineage.json)
    ├── evaluation.md             # Evaluator pipeline design and CLI usage
    ├── promotion-policy.md       # Bronze/silver/gold policy details
    ├── next-iteration.md         # Frontier and allocation behavior
    └── turtle-test-workflow.md   # Step-by-step manual test procedure on turtle
```

## Component roles

| Component | Role |
|---|---|
| `join.sh` | One-time machine bootstrap: checks NVIDIA driver, Docker, k3s join |
| `leave.sh` | Undo join.sh changes; cleanup node |
| `skill.md` | External agent reference: how to submit jobs and retrieve train.py |
| `orchestration/server.py` | Central control plane: HTTP API, Redis queue, background worker, watcher |
| `orchestration/k8s_deployer.py` | Creates Kubernetes Jobs on turtle; monitors until completion |
| `orchestration/agent.py` | LLM analyzes a repo and generates an `InitContainerSpec` (used for non-autoresearch tasks) |
| `evaluator/watcher.py` | Daemon that auto-evaluates completed generations and re-submits next gen |
| `evaluator/cli.py` | Manual evaluation CLI (not used in normal operation — watcher handles it) |
| `docker/Dockerfile` | Builds single-GPU worker image from `nvidia/cuda:12.8.1-devel-ubuntu22.04` |
| `docker/entrypoint.sh` | Container runtime: dataset prep → agent loop → artifact upload to S3 |
| `infra/__main__.py` | Pulumi-managed cluster infrastructure |
| `scripts/build.sh` | Convenience wrapper for `docker build` on the worker node |
| `scripts/prepare-cache.sh` | Runs container in prepare-only mode to seed persistent cache |
| `scripts/run.sh` | Manual training run (useful for testing; bypasses orchestrator and k8s) |
| `scripts/evaluate.sh` | Manual evaluator run (useful for debugging; bypasses watcher) |

## Normal vs manual operation

Under normal operation:
- External agents call `POST /submit` on the orchestration server
- The server enqueues jobs, deploys Kubernetes pods, and the watcher auto-evaluates
- Results are retrieved via `GET /generation/<gen_id>/train/<run_id>`

`scripts/run.sh` and `scripts/evaluate.sh` are for local testing and debugging only.
They bypass the orchestrator and should not be used in production workflows.
