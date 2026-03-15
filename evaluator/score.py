from __future__ import annotations

from typing import Tuple


def compute_metric_deltas(
    run_value: float,
    parent_value: float,
    direction: str,
    epsilon: float = 1e-3,
) -> Tuple[float, float]:
    """
    Returns (delta_primary_metric, normalized_delta).

    - delta_primary_metric preserves natural subtraction: run - parent.
    - normalized_delta is positive when the run improves over parent.
    """
    delta = run_value - parent_value
    if direction == "min":
        improvement = parent_value - run_value
    elif direction == "max":
        improvement = run_value - parent_value
    else:
        raise ValueError(f"Unknown primary metric direction: {direction}")

    scale = max(abs(parent_value), epsilon)
    normalized_delta = improvement / scale
    return delta, normalized_delta
