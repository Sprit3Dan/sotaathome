from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from evaluator.models import CandidateAggregate, EvaluatorConfig, PromotionDecision, RunArtifact


def _index_runs_by_candidate_resource(
    runs: Iterable[RunArtifact],
) -> Dict[Tuple[str, str], List[RunArtifact]]:
    index: Dict[Tuple[str, str], List[RunArtifact]] = defaultdict(list)
    for run in runs:
        index[(run.candidate_id, run.resource_class)].append(run)
    return index


def decide_promotions(
    aggregates: Iterable[CandidateAggregate],
    runs: Iterable[RunArtifact],
    config: EvaluatorConfig,
) -> List[PromotionDecision]:
    run_index = _index_runs_by_candidate_resource(runs)
    decisions: List[PromotionDecision] = []

    for aggregate in aggregates:
        key = (aggregate.candidate_id, aggregate.resource_class)
        candidate_runs = [run for run in run_index.get(key, []) if run.valid]
        improved_runs = [
            run
            for run in candidate_runs
            if run.normalized_delta is not None and run.normalized_delta >= config.bronze_min_delta
        ]
        distinct_workers = {run.worker_id for run in improved_runs}
        distinct_seeds = {run.seed for run in improved_runs}

        reasons: List[str] = []
        level = "none"

        if improved_runs:
            level = "bronze"
            reasons.append(
                f"{len(improved_runs)} run(s) met bronze threshold normalized_delta>={config.bronze_min_delta:.6f}"
            )

            silver_reproducible = (
                len(improved_runs) >= config.silver_min_runs
                and len(distinct_workers) >= config.silver_min_distinct_workers
            )
            if silver_reproducible:
                level = "silver"
                reasons.append("improvement reproduced across multiple workers")

                gold_ready = (
                    len(improved_runs) >= config.gold_min_runs
                    and len(distinct_workers) >= config.gold_min_distinct_workers
                    and (aggregate.mean_normalized_delta or 0.0) >= config.gold_min_mean_delta
                )
                if gold_ready:
                    level = "gold"
                    reasons.append("sufficient verification depth for resource-class default parent")

        if level == "none":
            best = aggregate.best_normalized_delta or 0.0
            if best >= config.near_miss_delta:
                reasons.append(
                    f"near miss: best normalized_delta {best:.6f} >= near_miss_delta {config.near_miss_delta:.6f}"
                )
            elif aggregate.valid_run_count == 0:
                reasons.append("no valid runs")
            elif aggregate.best_normalized_delta is None:
                reasons.append("baseline seed candidate: no parent-relative delta to score")
            else:
                reasons.append("did not meet bronze threshold")

        decisions.append(
            PromotionDecision(
                candidate_id=aggregate.candidate_id,
                parent_candidate_id=aggregate.parent_candidate_id,
                resource_class=aggregate.resource_class,
                promotion_level=level,
                reasons=reasons,
                stats={
                    "run_count": aggregate.run_count,
                    "valid_run_count": aggregate.valid_run_count,
                    "improved_run_count": len(improved_runs),
                    "distinct_improved_workers": len(distinct_workers),
                    "distinct_improved_seeds": len(distinct_seeds),
                    "best_normalized_delta": aggregate.best_normalized_delta,
                    "mean_normalized_delta": aggregate.mean_normalized_delta,
                },
            )
        )

    return decisions
