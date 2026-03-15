# Repo Structure

```
SAxFS-hackathon/
├── AGENTS.md                     # Agent guidance: design principles, roadmap
├── README.md                     # Human-facing project overview
├── join.sh                       # Worker node bootstrap entrypoint
├── leave.sh                      # Worker node cleanup
│
├── evaluator/
│   ├── models.py                 # Evaluator data model and configuration
│   ├── loader.py                 # Artifact scanning/loading from output volumes
│   ├── validate.py               # Pass/fail validation for run eligibility
│   ├── score.py                  # Parent-relative primary metric scoring
│   ├── aggregate.py              # Candidate aggregation inside resource class
│   ├── promote.py                # Bronze/silver/gold decision logic
│   ├── frontier.py               # Frontier role construction
│   ├── allocate.py               # Next-iteration exploit/explore/verify planning
│   └── cli.py                    # Runnable CLI entrypoint for evaluation
│
├── docker/
│   ├── Dockerfile                # autoresearch-worker image definition
│   └── entrypoint.sh             # Container entrypoint: prepare → train → summarize
│
├── scripts/
│   ├── build.sh                  # Build Docker image on worker node
│   ├── prepare-cache.sh          # Download dataset shards (prepare-only run)
│   ├── run.sh                    # Full training run wrapper
│   └── evaluate.sh               # Evaluator wrapper (input dir -> output JSONs)
│
└── docs/
    ├── repo-structure.md         # This file
    ├── autoresearch-worker.md    # Worker image design and runtime contract
    ├── artifact-contract.md      # Completed-run artifact contract
    ├── evaluation.md             # Evaluator pipeline design and usage
    ├── promotion-policy.md       # Bronze/silver/gold policy details
    ├── next-iteration.md         # Frontier and allocation behavior
    └── turtle-test-workflow.md   # Step-by-step test procedure on turtle
```

## Component roles

| Component | Role |
|-----------|------|
| `join.sh` | One-time machine bootstrap: checks NVIDIA driver, Docker, etc. |
| `leave.sh` | Undo join.sh changes; cleanup node |
| `docker/Dockerfile` | Builds single-GPU worker image from `nvidia/cuda:12.8.1-devel-ubuntu22.04` |
| `docker/entrypoint.sh` | Container runtime logic: data prep + training loop + summary |
| `evaluator/cli.py` | Loads completed artifacts, scores by resource class, and writes promotions/frontier/next-jobs JSON |
| `scripts/build.sh` | Convenience wrapper for `docker build` on the worker node |
| `scripts/prepare-cache.sh` | Runs container in prepare-only mode to seed persistent cache |
| `scripts/run.sh` | Runs container for N training iterations with correct mounts/env |
| `scripts/evaluate.sh` | Runs evaluator CLI over completed artifact directories |
