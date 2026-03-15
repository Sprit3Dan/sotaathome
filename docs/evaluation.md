# Evaluation Pipeline (v0)

This repo now includes a first-pass evaluator for completed SoTA@Home autoresearch runs.

## What it does

Inputs:
- directory tree of completed run artifacts (from mounted output volumes)

Pipeline:
1. Load run artifacts (`run.json`, `metrics.json`, `lineage.json`)
2. Validate run eligibility (pass/fail)
3. Score parent-relative improvement at normalized budget
4. Aggregate by `(candidate_id, resource_class)`
5. Compute bronze/silver/gold promotion decisions
6. Build frontier entries (`gold`, `silver`, `near_miss`, `diversity`)
7. Emit next-iteration job recommendations (`exploit`, `explore`, `verify`)

Outputs:
- `runs.json`
- `aggregates.json`
- `promotions.json`
- `frontier.json`
- `next_jobs.json`
- `allocation_summary.json`

## Why resource classes matter

The scheduler assigns normalized classes (`2060-12gb`, `3090-24gb`, `H100-80gb`).
Evaluator decisions are scoped to the assigned class:
- promotion is class-local
- frontier is class-local
- next jobs are class-local

This avoids misleading cross-hardware comparisons based on wall clock.

## Why wall clock is not the main score

Wall clock is used as a budget envelope check and scheduling control.
Scientific grading is based on primary metric improvement vs parent baseline.

## CLI usage

```bash
python -m evaluator.cli \
  --input-dir evaluator/examples/sample_artifacts \
  --output-dir /tmp/sotahome-eval
```

Or with wrapper script:

```bash
./scripts/evaluate.sh evaluator/examples/sample_artifacts /tmp/sotahome-eval
```

Optional config override:

```bash
python -m evaluator.cli \
  --input-dir evaluator/examples/sample_artifacts \
  --output-dir /tmp/sotahome-eval \
  --config /path/to/evaluator-config.json
```

Example config payload:

```json
{
  "bronze_min_delta": 0.001,
  "silver_min_runs": 2,
  "silver_min_distinct_workers": 2,
  "gold_min_runs": 3,
  "near_miss_delta": 0.0005,
  "diversity_slots": 2,
  "metric_normalization_epsilon": 0.001,
  "jobs_per_resource_class": 10,
  "exploit_ratio": 0.7,
  "explore_ratio": 0.2,
  "verify_ratio": 0.1
}
```

Unknown config keys are rejected with an explicit error to avoid silent typo fallbacks.

## Module layout

```text
evaluator/
  __init__.py
  models.py
  loader.py
  validate.py
  score.py
  aggregate.py
  promote.py
  frontier.py
  allocate.py
  cli.py
```

`loader.py` provides a loader interface (`ArtifactLoader`) and a filesystem implementation (`FilesystemArtifactLoader`), so switching to S3/object-store transport is isolated to loader code.
`ArtifactLoader.list_runs(source)` accepts a local path or URI-like string.
Validation checks required files from loader-provided payload keys instead of direct filesystem `Path.exists()` calls.

## Future extension points

- persist runs/aggregates/promotions in Postgres tables
- support candidate families beyond autoresearch defaults
- richer validity policy per task type/model family
- explicit merge experiment queue + verification workflows
- confidence intervals and robust statistics for noisy metrics
