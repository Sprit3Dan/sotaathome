from __future__ import annotations

from typing import Dict, List

from evaluator.loader import REQUIRED_FILES
from evaluator.models import (
    EvaluatorConfig,
    INVALID_TERMINAL_RUN_STATUSES,
    KNOWN_RESOURCE_CLASSES,
    RunArtifact,
    VALID_RUN_STATUSES,
)
from evaluator.score import compute_metric_deltas


def validate_run(
    run: RunArtifact,
    payloads: Dict,
    config: EvaluatorConfig,
) -> RunArtifact:
    errors: List[str] = []

    missing = [name for name in REQUIRED_FILES if name not in payloads]
    if missing:
        errors.append(f"missing required files: {', '.join(missing)}")

    if not run.run_id:
        errors.append("run_id missing")
    if not run.candidate_id:
        errors.append("candidate_id missing")
    if not run.agent_id:
        errors.append("agent_id missing")
    if not run.worker_id:
        errors.append("worker_id missing")
    if run.resource_class not in KNOWN_RESOURCE_CLASSES:
        errors.append(
            f"unknown resource_class '{run.resource_class}' (expected one of {sorted(KNOWN_RESOURCE_CLASSES)})"
        )

    if run.status in INVALID_TERMINAL_RUN_STATUSES:
        errors.append(f"invalid run status for grading: {run.status}")
    elif run.status not in VALID_RUN_STATUSES:
        errors.append(f"run status must be one of {sorted(VALID_RUN_STATUSES)}")

    if not run.primary_metric_name:
        errors.append("primary metric missing")
    if run.primary_metric_direction not in {"min", "max"}:
        errors.append("primary metric direction must be 'min' or 'max'")

    metrics = payloads.get("metrics.json", {})
    lineage = payloads.get("lineage.json", {})
    if "primary_metric" not in metrics:
        errors.append("metrics.json.primary_metric missing")

    # Seed/baseline runs are valid with no parent linkage and are not delta-scored.
    if run.is_seed_run:
        run.parent_candidate_id = None
        run.parent_primary_metric_value = None
        run.delta_primary_metric = None
        run.normalized_delta = None
    else:
        if not run.parent_candidate_id:
            errors.append("parent_candidate_id missing in lineage.json")
        if "parent_primary_metric_value" not in lineage:
            errors.append("lineage.json.parent_primary_metric_value missing")

    max_wall_clock = None
    budget = run.training_budget
    if isinstance(budget, dict):
        max_wall_clock = budget.get("max_wall_clock_seconds")
    elif isinstance(budget, (int, float)) and budget > 0:
        max_wall_clock = budget

    if max_wall_clock is not None and float(max_wall_clock) > 0:
        allowed = float(max_wall_clock) * config.budget_overrun_tolerance
        if run.wall_clock_used_seconds > allowed:
            errors.append(
                f"wall clock {run.wall_clock_used_seconds:.2f}s exceeded budget envelope {allowed:.2f}s"
            )

    if not run.is_seed_run and run.parent_primary_metric_value is not None:
        try:
            run.delta_primary_metric, run.normalized_delta = compute_metric_deltas(
                run.run_primary_metric_value,
                run.parent_primary_metric_value,
                run.primary_metric_direction,
                epsilon=config.metric_normalization_epsilon,
            )
        except Exception as exc:
            errors.append(str(exc))

    run.validation_errors = errors
    run.valid = len(errors) == 0
    return run
