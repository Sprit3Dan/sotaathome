from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from evaluator.models import AllocationSummary, EvaluatorConfig, FrontierEntry, NextJob


def _round_split(total: int, exploit_ratio: float, explore_ratio: float, verify_ratio: float):
    raw = {
        "exploit": total * exploit_ratio,
        "explore": total * explore_ratio,
        "verify": total * verify_ratio,
    }
    counts = {key: int(math.floor(value)) for key, value in raw.items()}

    assigned = sum(counts.values())
    remainder = total - assigned
    fractional_order = sorted(
        raw.keys(),
        key=lambda key: (raw[key] - counts[key], raw[key]),
        reverse=True,
    )
    idx = 0
    while remainder > 0:
        key = fractional_order[idx % len(fractional_order)]
        counts[key] += 1
        remainder -= 1
        idx += 1
    return counts["exploit"], counts["explore"], counts["verify"]


def _take_cycle(items: List[FrontierEntry], n: int) -> Tuple[List[FrontierEntry], List[str], int]:
    if n <= 0:
        return [], [], 0
    if not items:
        return [], [f"no candidates available for {n} requested slots"], n
    out: List[FrontierEntry] = []
    warnings: List[str] = []
    if len(items) < n:
        warnings.append(
            f"reusing {len(items)} candidate(s) to fill {n} requested slots"
        )
    idx = 0
    while len(out) < n:
        out.append(items[idx % len(items)])
        idx += 1
    return out, warnings, 0


def build_next_jobs(
    frontier: Iterable[FrontierEntry], config: EvaluatorConfig
) -> Tuple[List[NextJob], AllocationSummary]:
    by_resource: Dict[str, List[FrontierEntry]] = defaultdict(list)
    for entry in frontier:
        by_resource[entry.resource_class].append(entry)

    jobs: List[NextJob] = []
    warnings: List[str] = []
    requested_total = 0
    unfilled_total = 0
    for resource_class, entries in sorted(by_resource.items()):
        total = config.jobs_per_resource_class
        requested_total += total
        exploit_n, explore_n, verify_n = _round_split(
            total,
            config.exploit_ratio,
            config.explore_ratio,
            config.verify_ratio,
        )

        exploit_pool = [entry for entry in entries if entry.role in {"gold", "silver"}]
        explore_pool = [entry for entry in entries if entry.role in {"near_miss", "diversity"}]
        verify_pool = [entry for entry in entries if entry.role in {"silver", "near_miss"}]

        selected, cycle_warnings, unfilled = _take_cycle(exploit_pool or entries, exploit_n)
        warnings.extend([f"{resource_class}: exploit: {message}" for message in cycle_warnings])
        unfilled_total += unfilled
        for entry in selected:
            jobs.append(
                NextJob(
                    resource_class=resource_class,
                    parent_candidate_id=entry.candidate_id,
                    job_type="exploit",
                    rationale=f"focus compute on promoted lineage ({entry.role})",
                    mutation_budget="medium",
                    notes="Prioritize local mutations around confirmed improvement.",
                )
            )

        selected, cycle_warnings, unfilled = _take_cycle(explore_pool or entries, explore_n)
        warnings.extend([f"{resource_class}: explore: {message}" for message in cycle_warnings])
        unfilled_total += unfilled
        for entry in selected:
            jobs.append(
                NextJob(
                    resource_class=resource_class,
                    parent_candidate_id=entry.candidate_id,
                    job_type="explore",
                    rationale=f"probe alternative branch from {entry.role} frontier",
                    mutation_budget="high",
                    notes="Increase diversity in experiment proposals.",
                )
            )

        selected, cycle_warnings, unfilled = _take_cycle(
            verify_pool or exploit_pool or entries, verify_n
        )
        warnings.extend([f"{resource_class}: verify: {message}" for message in cycle_warnings])
        unfilled_total += unfilled
        for entry in selected:
            jobs.append(
                NextJob(
                    resource_class=resource_class,
                    parent_candidate_id=entry.candidate_id,
                    job_type="verify",
                    rationale="re-run with independent seed/worker to increase confidence",
                    mutation_budget="low",
                    notes="No major mutation; focus on reproducibility.",
                )
            )

    summary = AllocationSummary(
        requested_jobs=requested_total,
        allocated_jobs=len(jobs),
        unfilled_slots=unfilled_total,
        warnings=warnings,
    )
    return jobs, summary
