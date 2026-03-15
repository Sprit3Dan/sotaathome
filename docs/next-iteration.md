# Next Iteration Selection (v0)

The evaluator emits `next_jobs.json` recommendations per resource class.

## Frontier roles

The frontier is built from candidate aggregates and promotion decisions:
- `gold`: strongest verified lineages
- `silver`: reproducible improvements still needing more confidence
- `near_miss`: close to threshold and worth another attempt
- `diversity`: preserve alternative branch roots

This prevents the next round from collapsing onto one lineage too early.

## Allocation split

Default split is configurable and currently:
- 70% `exploit`
- 20% `explore`
- 10% `verify`

Per resource class, the evaluator picks parent candidates from frontier roles:
- exploit jobs from `gold/silver`
- explore jobs from `near_miss/diversity`
- verify jobs from `silver/near_miss` (or fallback promoted entries)

Each recommended job includes:
- `resource_class`
- `parent_candidate_id`
- `job_type`
- `rationale`
- optional `mutation_budget`
- optional notes for the future agent

Allocator observability:
- evaluator also emits `allocation_summary.json` with requested/allocated counts and warnings when candidate reuse was needed to fill slots.

## Merge experiments

Merge candidates are represented explicitly (not auto-promoted):
- `parent_a_candidate_id`
- `parent_b_candidate_id`
- `proposed_candidate_id`
- `requires_validation=true`

Merges should run as dedicated experiments and pass normal validation/promotion before adoption.
