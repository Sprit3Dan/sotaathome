from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from evaluator.models import CandidateAggregate, RunArtifact


GroupKey = Tuple[str, str]  # (candidate_id, resource_class)


def _safe_mean(values: List[float]):
    return statistics.mean(values) if values else None


def _safe_median(values: List[float]):
    return statistics.median(values) if values else None


def _safe_stddev(values: List[float]):
    return statistics.stdev(values) if len(values) >= 2 else None


def aggregate_candidates(runs: Iterable[RunArtifact]) -> List[CandidateAggregate]:
    grouped: Dict[GroupKey, List[RunArtifact]] = defaultdict(list)
    for run in runs:
        grouped[(run.candidate_id, run.resource_class)].append(run)

    output: List[CandidateAggregate] = []
    for (candidate_id, resource_class), group in sorted(grouped.items()):
        valid_runs = [run for run in group if run.valid]
        scored_valid_runs = [
            run
            for run in valid_runs
            if run.normalized_delta is not None and run.delta_primary_metric is not None
        ]
        deltas = [run.delta_primary_metric for run in scored_valid_runs]
        normalized = [run.normalized_delta for run in scored_valid_runs]
        workers = {run.worker_id for run in valid_runs}
        seeds = {run.seed for run in valid_runs}
        parent_ids = {run.parent_candidate_id for run in group if run.parent_candidate_id}
        direction = valid_runs[0].primary_metric_direction if valid_runs else ""
        if deltas:
            if direction == "max":
                best_delta = max(deltas)
                worst_delta = min(deltas)
            else:
                best_delta = min(deltas)
                worst_delta = max(deltas)
        else:
            best_delta = None
            worst_delta = None

        aggregate = CandidateAggregate(
            candidate_id=candidate_id,
            parent_candidate_id=next(iter(parent_ids), None),
            resource_class=resource_class,
            primary_metric_name=(valid_runs[0].primary_metric_name if valid_runs else ""),
            primary_metric_direction=direction,
            run_count=len(group),
            valid_run_count=len(valid_runs),
            success_count=sum(1 for r in group if r.status == "completed"),
            unique_worker_count=len(workers),
            unique_seed_count=len(seeds),
            mean_delta_primary_metric=_safe_mean(deltas),
            median_delta_primary_metric=_safe_median(deltas),
            stddev_delta_primary_metric=_safe_stddev(deltas),
            best_delta_primary_metric=best_delta,
            worst_delta_primary_metric=worst_delta,
            mean_normalized_delta=_safe_mean(normalized),
            best_normalized_delta=(max(normalized) if normalized else None),
            improved_run_count=sum(1 for value in normalized if value > 0),
            run_ids=[run.run_id for run in group],
        )
        output.append(aggregate)

    return output
