from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

KNOWN_RESOURCE_CLASSES = {"2060-12gb", "3090-24gb", "H100-80gb"}
VALID_RUN_STATUSES = {"completed"}
INVALID_TERMINAL_RUN_STATUSES = {"failed", "oom", "timeout", "crashed"}


@dataclass
class EvaluatorConfig:
    bronze_min_delta: float = 0.001
    silver_min_runs: int = 2
    silver_min_distinct_workers: int = 2
    silver_min_distinct_seeds: int = 2
    gold_min_runs: int = 3
    gold_min_distinct_workers: int = 2
    gold_min_mean_delta: float = 0.001
    near_miss_delta: float = 0.0005
    diversity_slots: int = 2
    budget_overrun_tolerance: float = 1.10
    metric_normalization_epsilon: float = 1e-3
    exploit_ratio: float = 0.70
    explore_ratio: float = 0.20
    verify_ratio: float = 0.10
    jobs_per_resource_class: int = 10

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "EvaluatorConfig":
        config = EvaluatorConfig()
        unknown_keys = [key for key in payload.keys() if not hasattr(config, key)]
        if unknown_keys:
            raise ValueError(
                f"Unknown evaluator config key(s): {', '.join(sorted(unknown_keys))}"
            )
        for key, value in payload.items():
            setattr(config, key, value)
        return config


@dataclass
class RunArtifact:
    run_id: str
    candidate_id: str
    parent_candidate_id: Optional[str]
    agent_id: str
    worker_id: str
    resource_class: str
    seed: int
    status: str
    model_family: Optional[str]
    task_type: Optional[str]
    primary_metric_name: str
    primary_metric_direction: str
    run_primary_metric_value: float
    parent_primary_metric_value: Optional[float]
    delta_primary_metric: Optional[float]
    normalized_delta: Optional[float]
    training_budget: Any
    wall_clock_used_seconds: float
    artifact_paths: Dict[str, str]
    created_at: str
    completed_at: Optional[str]
    source_dir: str
    is_seed_run: bool = False
    valid: bool = False
    validation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateAggregate:
    candidate_id: str
    parent_candidate_id: Optional[str]
    resource_class: str
    primary_metric_name: str
    primary_metric_direction: str
    run_count: int
    valid_run_count: int
    success_count: int
    unique_worker_count: int
    unique_seed_count: int
    mean_delta_primary_metric: Optional[float]
    median_delta_primary_metric: Optional[float]
    stddev_delta_primary_metric: Optional[float]
    best_delta_primary_metric: Optional[float]
    worst_delta_primary_metric: Optional[float]
    mean_normalized_delta: Optional[float]
    best_normalized_delta: Optional[float]
    improved_run_count: int
    run_ids: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PromotionDecision:
    candidate_id: str
    parent_candidate_id: Optional[str]
    resource_class: str
    promotion_level: str
    reasons: List[str]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FrontierEntry:
    candidate_id: str
    parent_candidate_id: Optional[str]
    resource_class: str
    role: str
    promotion_level: str
    score_hint: Optional[float]
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NextJob:
    resource_class: str
    parent_candidate_id: str
    job_type: str
    rationale: str
    mutation_budget: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AllocationSummary:
    requested_jobs: int
    allocated_jobs: int
    unfilled_slots: int
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MergeExperiment:
    merge_id: str
    resource_class: str
    parent_a_candidate_id: str
    parent_b_candidate_id: str
    proposed_candidate_id: str
    requires_validation: bool = True
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
