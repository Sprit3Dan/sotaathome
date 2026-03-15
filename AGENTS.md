# AGENTS.md

## Purpose of this repo

This repository is the early bootstrap for **SoTA@Home**:

- a client-side node that lets people contribute local compute to distributed AI/autoresearch workloads
- conceptually similar to **SETI@Home**, but for modern agentic workloads
- intended to support both:
  - **human operators** setting up a machine manually
  - **automation/agents** such as OpenClaw provisioning or joining machines non-interactively

The repo currently contains:

- `join.sh` / `leave.sh` — machine bootstrap and cleanup
- `docker/` — autoresearch-worker Docker image
- `scripts/` — build, prepare-cache, and run helpers
- `docs/` — design docs and test workflow

`join.sh` is the machine bootstrap entrypoint. The Docker worker image is the first
concrete job-execution capability.

---

## Current repo state

### Implemented now

- machine bootstrap via `join.sh`
- basic host readiness checks for:
  - NVIDIA driver presence / usability
  - Docker availability
- installation flow that can be:
  - safe and non-interactive by default
  - optionally allowed to install missing pieces with an explicit dangerous flag
  - interactive when a human is expected to fix missing prerequisites
- **autoresearch-worker Docker image** (`docker/Dockerfile` + `docker/entrypoint.sh`)
  - wraps karpathy/autoresearch at pinned SHA `c2450add`
  - one container per GPU contract: `--gpus device=N`
  - standard bind-mounts: `/artifacts/cache`, `/artifacts/output`
  - env-var driven: `AUTORESEARCH_MAX_ITERATIONS`, `DEVICE_BATCH_SIZE`, etc.
  - entrypoint phases: validate mounts → symlink cache → copy workspace →
    patch batch size → prepare data → training loop → write summary
- **Scripts**: `scripts/build.sh`, `scripts/prepare-cache.sh`, `scripts/run.sh`
- **Evaluator prototype** (`evaluator/` + docs):
  - ingests completed run artifacts from output storage
  - validates required files/fields and resource-class correctness
  - scores parent-relative primary metric deltas at normalized budget
  - aggregates by candidate within resource class
  - computes bronze/silver/gold decisions
  - builds frontier roles (`gold`, `silver`, `near_miss`, `diversity`)
  - emits next-iteration job recommendations (`exploit`, `explore`, `verify`)
- **Reporting pipeline** (`evaluator/report.py`, `evaluator/report_cli.py`):
  - runs automatically after each generation is evaluated (called from `watcher.py`)
  - downloads evaluation artifacts from `s3://runs/evaluations/{gen_id}/`
  - generates charts (gnuplot + graphviz dot), renders `single.md`, zips, uploads to `s3://runs/reports/{gen_id}/report.zip`
  - non-fatal: if report generation fails the evaluation result is already saved
  - CLI: `python3 -m evaluator.report_cli --gen-id <gen_id> [--no-upload]`
- **Docs**: `docs/repo-structure.md`, `docs/autoresearch-worker.md`, `docs/turtle-test-workflow.md`, `docs/artifact-contract.md`, `docs/evaluation.md`, `docs/promotion-policy.md`, `docs/next-iteration.md`, `docs/reporting.md`, `docs/report-format.md`, `docs/storage-upload.md`

### Expected future evolution

This repo will likely grow into a client/node package that includes:

- machine bootstrap
- local runtime installation
- node registration / cluster join
- tailscale or equivalent connectivity setup
- k3s compatibility / cluster joining
- local agent runtime
- job pulling / execution
- telemetry / health reporting
- client-side UI or local web status surface
- update / self-healing workflows

When adding new code, treat `join.sh` as the seed of a larger client bootstrap system rather than as a throwaway script.

---

---

## autoresearch-worker image

The worker image (`docker/Dockerfile`) is the first concrete job-execution primitive for
SoTA@Home. It embodies the one-container-per-GPU contract.

### Design principles

- **One GPU in, results out**: `--gpus device=N` assigns the GPU; container always uses `CUDA_VISIBLE_DEVICES=0` internally.
- **Persistent cache via bind-mount**: dataset shards + tokenizer live at `/artifacts/cache`, shared across runs.
- **Per-run workspace**: upstream files copied to `/workspace/<run-id>` so the reference image is never mutated.
- **Env-var driven**: all tunable parameters (iterations, batch size, shards, run ID) are env vars with safe defaults.
- **Fail loudly**: entrypoint validates mounts, verifies patches, and dies with actionable messages on any misconfiguration.

### Key risk: disk on turtle

Turtle has ~24 GB free disk. Image + data ≈ 10 GB. Never run parallel builds.
If disk pressure occurs: `docker system prune -f` then retry.

### Default batch size is 64 (not 128)

Upstream train.py hardcodes `DEVICE_BATCH_SIZE=128`. The entrypoint sed-patches this to
`DEVICE_BATCH_SIZE` env var (default 64). Use `BATCH_SIZE=32` if CUDA OOM occurs on 12 GB VRAM.

### Adding new autoresearch capabilities

When modifying the worker:
- Update the pinned `AUTORESEARCH_COMMIT` ARG in `docker/Dockerfile` and re-verify the sed patch still applies
- Extend `docker/entrypoint.sh` following the existing phase structure
- Update `docs/autoresearch-worker.md` runtime contract table if env vars or mounts change
- Add recovery notes to `docs/turtle-test-workflow.md` for any new failure modes

---

## What `join.sh` does

`join.sh` is intended to be the bootstrap script for a worker node.

### Behavioral goals

It should work for both:

1. **Humans**
   - run manually on a fresh or semi-fresh machine
   - detect missing prerequisites
   - tell the user clearly what is missing
   - switch to or require interactive mode only when necessary

2. **Agents / automation**
   - run unattended when possible
   - avoid surprising system mutations unless explicitly allowed
   - support a flag such as `--dangerously-skip-permissions` for one-time installation of missing system dependencies

### Current responsibilities

The script is designed to:

- check whether NVIDIA drivers are installed and functioning
- check whether Docker is installed
- verify machine readiness for future k3s use
- keep future Tailscale setup in mind
- fail clearly when prerequisites are missing
- distinguish between:
  - safe validation mode
  - interactive/manual remediation
  - explicit opt-in install mode

### Important design intent

The script should **not** silently mutate a machine in default mode.

Default mode should prefer:

- detect
- report
- instruct

Only explicit opt-in modes should:

- install packages
- change services
- modify permissions
- perform privileged system setup

---

## Design principles for coding agents

When modifying this repo, follow these rules.

### 1. Preserve dual-use operation

Everything added should be usable by both:

- a human at a terminal
- an automated provisioning agent

Avoid flows that only make sense for one of those audiences.

### 2. Default to safe behavior

Do not silently install or reconfigure system software unless the script is running in an explicit install mode.

Good default behavior:

- check state
- print actionable errors
- exit non-zero

### 3. Make failures actionable

If something is missing, error messages should say:

- what is missing
- why it matters
- what the operator should run next
- whether interactive mode or a dangerous flag is required

Bad error:

- `docker missing`

Good error:

- `Docker is missing. Re-run interactively to install it, or use --dangerously-skip-permissions to allow one-time automated installation.`

### 4. Keep it idempotent

Assume the script may be run multiple times.

New logic should be safe when rerun:
- do not duplicate config unnecessarily
- do not re-install blindly if something already exists
- do not assume a pristine machine

### 5. Optimize for Ubuntu/Linux worker hosts

Unless explicitly changed, assume the initial target environment is a Linux box that may become a GPU worker.

Prefer:
- straightforward bash
- minimal dependencies
- widely available tools
- compatibility with common Ubuntu server/desktop environments

### 6. Build toward modularity

As the repo grows, avoid turning `join.sh` into an unmaintainable monolith.

If functionality expands, prefer splitting into logical helpers such as:

- `lib/common.sh`
- `lib/checks.sh`
- `lib/install.sh`
- `lib/network.sh`
- `lib/gpu.sh`
- `lib/k3s.sh`

But keep the top-level UX centered on `join.sh`.

### 7. Future-proof for cluster join

Even if k3s join is not implemented yet, changes should not block future support for:

- node identity
- secure enrollment
- tailscale networking
- GPU runtime enablement
- pulling distributed jobs

---

## Near-term roadmap assumptions

Coding agents should assume the repo will likely add features in roughly this direction:

1. **Bootstrap**
   - OS/package checks
   - Docker install/validation
   - NVIDIA runtime validation

2. **Connectivity**
   - Tailscale install and auth
   - node reachability checks

3. **Cluster readiness**
   - k3s prerequisites
   - container runtime alignment
   - GPU runtime integration

4. **Node enrollment**
   - register node with coordinator/control plane
   - machine labels/capabilities
   - auth tokens or certificates

5. **Work execution**
   - fetch jobs
   - run workloads safely
   - report results/status

6. **Client UX**
   - maybe a local web UI or CLI status/reporting tools

Do not hardcode assumptions that prevent this progression.

---

## Guidance for editing `join.sh`

When updating `join.sh`, preserve these qualities:

- readable top-to-bottom flow
- clear log lines prefixed consistently, for example:
  - `[join.sh] Checking NVIDIA driver`
  - `[join.sh] ERROR: Docker is missing`
- strict shell behavior where reasonable
- explicit exit codes
- small helper functions instead of repeated inline logic

Preferred style:

- use functions for checks/install steps
- keep messages short and operational
- avoid clever bash tricks unless they make maintenance easier
- comment non-obvious logic
- assume `sudo` may be involved

### Example responsibility split inside the script

Useful internal functions may include:

- `log()`
- `warn()`
- `error()`
- `require_root_or_sudo()`
- `check_nvidia()`
- `check_docker()`
- `install_docker()`
- `check_k3s_prereqs()`
- `check_tailscale()`
- `parse_args()`

---

## What not to do

Unless explicitly requested, do not:

- rewrite the repo around another language
- replace bash bootstrap with a heavy framework
- add unnecessary abstraction for a one-file repo
- assume cloud-only deployment
- assume the machine is already in a cluster
- silently install software in default mode
- remove human-readable operator guidance

---

## Summary for agents

If you are a coding agent working in this repo, your job is to help evolve a **safe, repeatable, agent-friendly worker bootstrap and client runtime** for SoTA@Home.

Right now:
- the repo is centered on `join.sh`
- `join.sh` is the machine bootstrap entrypoint
- the project is headed toward distributed AI/autoresearch worker nodes

When in doubt:
- keep bootstrap safe
- keep behavior idempotent
- make errors actionable
- preserve compatibility with both humans and automation
- build toward a future distributed worker/client architecture
