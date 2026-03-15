from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from evaluator.models import CandidateAggregate, EvaluatorConfig, FrontierEntry, PromotionDecision


def _index_aggregates(
    aggregates: Iterable[CandidateAggregate],
) -> Dict[Tuple[str, str], CandidateAggregate]:
    index: Dict[Tuple[str, str], CandidateAggregate] = {}
    for aggregate in aggregates:
        index[(aggregate.candidate_id, aggregate.resource_class)] = aggregate
    return index


def build_frontier(
    aggregates: Iterable[CandidateAggregate],
    decisions: Iterable[PromotionDecision],
    config: EvaluatorConfig,
) -> List[FrontierEntry]:
    aggregate_index = _index_aggregates(aggregates)
    entries: List[FrontierEntry] = []
    used: set[Tuple[str, str, str]] = set()

    by_resource: Dict[str, List[PromotionDecision]] = defaultdict(list)
    for decision in decisions:
        by_resource[decision.resource_class].append(decision)

    for resource_class, resource_decisions in by_resource.items():
        used_candidates: set[str] = set()
        for level in ("gold", "silver"):
            for decision in resource_decisions:
                if decision.promotion_level != level:
                    continue
                aggregate = aggregate_index[(decision.candidate_id, decision.resource_class)]
                key = (decision.candidate_id, resource_class, level)
                if key in used:
                    continue
                used.add(key)
                used_candidates.add(decision.candidate_id)
                entries.append(
                    FrontierEntry(
                        candidate_id=decision.candidate_id,
                        parent_candidate_id=decision.parent_candidate_id,
                        resource_class=resource_class,
                        role=level,
                        promotion_level=decision.promotion_level,
                        score_hint=aggregate.best_normalized_delta,
                        rationale="; ".join(decision.reasons),
                    )
                )

        near_miss = []
        for decision in resource_decisions:
            if decision.promotion_level != "none":
                continue
            best = decision.stats.get("best_normalized_delta") or 0.0
            if best >= config.near_miss_delta:
                near_miss.append((best, decision))

        for _, decision in sorted(near_miss, key=lambda item: item[0], reverse=True):
            key = (decision.candidate_id, resource_class, "near_miss")
            if key in used:
                continue
            used.add(key)
            used_candidates.add(decision.candidate_id)
            entries.append(
                FrontierEntry(
                    candidate_id=decision.candidate_id,
                    parent_candidate_id=decision.parent_candidate_id,
                    resource_class=resource_class,
                    role="near_miss",
                    promotion_level=decision.promotion_level,
                    score_hint=decision.stats.get("best_normalized_delta"),
                    rationale="close to threshold; worth targeted follow-up",
                )
            )

        diversity_added = 0
        seen_parents = {
            entry.parent_candidate_id
            for entry in entries
            if entry.resource_class == resource_class and entry.role in {"gold", "silver"}
        }
        diversity_candidates = sorted(
            [
                aggregate
                for aggregate in aggregate_index.values()
                if aggregate.resource_class == resource_class and aggregate.valid_run_count > 0
            ],
            key=lambda aggregate: aggregate.best_normalized_delta or -1.0,
            reverse=True,
        )
        for aggregate in diversity_candidates:
            if diversity_added >= config.diversity_slots:
                break
            if aggregate.parent_candidate_id is None:
                continue
            if aggregate.parent_candidate_id in seen_parents:
                continue
            if aggregate.candidate_id in used_candidates:
                continue
            key = (aggregate.candidate_id, resource_class, "diversity")
            if key in used:
                continue
            used.add(key)
            used_candidates.add(aggregate.candidate_id)
            seen_parents.add(aggregate.parent_candidate_id)
            entries.append(
                FrontierEntry(
                    candidate_id=aggregate.candidate_id,
                    parent_candidate_id=aggregate.parent_candidate_id,
                    resource_class=resource_class,
                    role="diversity",
                    promotion_level="n/a",
                    score_hint=aggregate.best_normalized_delta,
                    rationale="preserve branch diversity and avoid single-lineage collapse",
                )
            )
            diversity_added += 1

    role_order = {"gold": 0, "silver": 1, "near_miss": 2, "diversity": 3}
    return sorted(
        entries,
        key=lambda entry: (
            entry.resource_class,
            role_order.get(entry.role, 99),
            -(entry.score_hint or -1.0),
        ),
    )
