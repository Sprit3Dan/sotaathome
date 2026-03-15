# Run Artifact Contract (v0)

This defines how a completed training pod writes outputs that the evaluator can ingest.

## Directory layout

Each run has its own directory under a shared output root:

```text
/artifacts/output/<run-id>/
  run.json
  metrics.json
  lineage.json
  patch.diff            # optional but recommended
  stdout.log            # optional but recommended
  stderr.log            # optional but recommended
```

The evaluator scans recursively for `run.json` and expects `metrics.json` + `lineage.json` beside it.

## `run.json`

Operational metadata and budget usage.

```json
{
  "run_id": "run-2060-c1-001",
  "candidate_id": "cand-2060-c1",
  "agent_id": "agent-a",
  "worker_id": "worker-2060-a",
  "resource_class": "2060-12gb",
  "model_family": "gpt2-small",
  "task_type": "tinystories",
  "seed": 101,
  "status": "completed",
  "training_budget": {
    "target_steps": 1000,
    "normalized_budget_units": 1.0,
    "max_wall_clock_seconds": 300
  },
  "wall_clock_used_seconds": 292,
  "created_at": "2026-03-10T17:00:00Z",
  "completed_at": "2026-03-10T17:05:00Z"
}
```

Required fields:
- `run_id`, `candidate_id`, `agent_id`, `worker_id`, `resource_class`, `seed`, `status`
- `training_budget` (object)
- `wall_clock_used_seconds`
- `created_at`

Allowed `resource_class` values (v0):
- `2060-12gb`
- `3090-24gb`
- `H100-80gb`

Allowed `status` values for gradable runs (v0):
- `completed`

Known non-gradable terminal statuses:
- `failed`
- `oom`
- `timeout`
- `crashed`

## `metrics.json`

Scientific score data. Primary metric is required.

```json
{
  "primary_metric": {
    "name": "val_bpb",
    "direction": "min",
    "value": 1.495
  },
  "secondary_metrics": {
    "train_loss": 2.11
  }
}
```

Required fields:
- `primary_metric.name`
- `primary_metric.direction` (`"min"` or `"max"`)
- `primary_metric.value`

## `lineage.json`

Parent linkage and baseline used for comparison.

```json
{
  "candidate_id": "cand-2060-c1",
  "parent_candidate_id": "parent-2060-base",
  "parent_primary_metric_value": 1.5,
  "mutation_type": "patch"
}
```

Seed/baseline example:

```json
{
  "candidate_id": "cand-h100-seed-a",
  "is_seed_run": true,
  "parent_candidate_id": null,
  "mutation_type": "baseline"
}
```

Required fields:
- `candidate_id`
- either:
  - parent-linked run: `parent_candidate_id` + `parent_primary_metric_value`
  - first-generation baseline run: `is_seed_run: true` with `parent_candidate_id: null`

Optional merge experiment fields:
- `parent_a_candidate_id`
- `parent_b_candidate_id`
- `merge_strategy`

## Notes

- Scheduler-owned resource fit is authoritative; evaluator only checks `resource_class` validity.
- Wall clock is treated as budget envelope validation, not the primary scientific score.
- OOM/timeout/crash statuses fail validation and are excluded from scoring.
- Seed/baseline runs are valid but not scored on parent-relative delta until they have a parent linkage.
