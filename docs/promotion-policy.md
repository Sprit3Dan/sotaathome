# Promotion Policy (v0)

Promotions are decided per `resource_class`. Results are not merged into one global leaderboard.

## Primary grading axis

For each valid run, compute:

- `delta_primary_metric = run_value - parent_value`
- `normalized_delta = improvement / abs(parent_value)`

Where `improvement` is:
- `parent - run` for `direction="min"`
- `run - parent` for `direction="max"`

`normalized_delta > 0` means the candidate improved.

## Validation gates first

A run must pass validation to be graded:
- required artifact files exist
- parseable required fields
- known `resource_class`
- `status=completed`
- primary metric and parent baseline available
- wall-clock within configured budget envelope tolerance

Failures, OOM, crash, timeout are mostly validity gates, not weighted quality penalties.

## Bronze / Silver / Gold

Default thresholds (configurable):
- `bronze_min_delta = 0.001`
- `silver_min_runs = 2`
- `silver_min_distinct_workers = 2`
- `gold_min_runs = 3`
- `gold_min_distinct_workers = 2`
- `gold_min_mean_delta = 0.001`
- `near_miss_delta = 0.0005`

Decision shape:
- Bronze: at least one valid run with `normalized_delta >= bronze_min_delta`
- Silver: bronze + reproduced across runs with worker diversity (`>= silver_min_distinct_workers`)
- Gold: silver + deeper evidence suitable for resource-class default parent

Near miss:
- not promoted
- but `best_normalized_delta >= near_miss_delta`

Seed baseline candidates:
- valid if artifact contract marks them as seed (`is_seed_run: true`)
- excluded from parent-relative delta scoring until linked to a parent baseline

## Why this policy

- Keeps scientific score focused on parent-relative metric improvement.
- Uses wall clock as control/budget guard instead of core quality signal.
- Contains hardware differences by evaluating inside each scheduler resource class.
