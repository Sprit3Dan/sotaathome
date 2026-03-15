"""SoTA@Home evaluation and next-iteration selection package."""

from evaluator.models import (
    AllocationSummary,
    CandidateAggregate,
    EvaluatorConfig,
    FrontierEntry,
    MergeExperiment,
    NextJob,
    PromotionDecision,
    RunArtifact,
)

__all__ = [
    "EvaluatorConfig",
    "AllocationSummary",
    "RunArtifact",
    "CandidateAggregate",
    "PromotionDecision",
    "FrontierEntry",
    "NextJob",
    "MergeExperiment",
]
