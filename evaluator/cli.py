from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from evaluator.aggregate import aggregate_candidates
from evaluator.allocate import build_next_jobs
from evaluator.frontier import build_frontier
from evaluator.loader import FilesystemArtifactLoader, load_runs
from evaluator.models import EvaluatorConfig
from evaluator.promote import decide_promotions
from evaluator.validate import validate_run


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def run_evaluation(input_dir: Path, output_dir: Path, config: EvaluatorConfig) -> Dict[str, Any]:
    loaded = load_runs(input_dir, loader=FilesystemArtifactLoader())
    runs = [validate_run(item.run, item.payloads, config) for item in loaded]
    aggregates = aggregate_candidates(runs)
    promotions = decide_promotions(aggregates, runs, config)
    frontier = build_frontier(aggregates, promotions, config)
    next_jobs, allocation_summary = build_next_jobs(frontier, config)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "runs.json", [run.to_dict() for run in runs])
    _write_json(output_dir / "aggregates.json", [agg.to_dict() for agg in aggregates])
    _write_json(output_dir / "promotions.json", [decision.to_dict() for decision in promotions])
    _write_json(output_dir / "frontier.json", [entry.to_dict() for entry in frontier])
    _write_json(output_dir / "next_jobs.json", [job.to_dict() for job in next_jobs])
    _write_json(output_dir / "allocation_summary.json", allocation_summary.to_dict())

    valid_count = sum(1 for run in runs if run.valid)
    return {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "run_count": len(runs),
        "valid_run_count": valid_count,
        "invalid_run_count": len(runs) - valid_count,
        "aggregate_count": len(aggregates),
        "promotion_count": len(promotions),
        "frontier_count": len(frontier),
        "next_job_count": len(next_jobs),
        "allocation_unfilled_slots": allocation_summary.unfilled_slots,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate completed SoTA@Home autoresearch runs and produce next-iteration recommendations."
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        type=Path,
        help="Directory tree containing completed run artifact directories.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory where evaluator JSON outputs will be written.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional JSON config overriding evaluator thresholds and allocation ratios.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print summary as JSON instead of human-readable lines.",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.config:
        config = EvaluatorConfig.from_dict(_read_json(args.config))
    else:
        config = EvaluatorConfig()

    summary = run_evaluation(args.input_dir, args.output_dir, config)
    if args.print_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    print("[evaluator] Evaluation complete")
    for key, value in summary.items():
        print(f"[evaluator] {key}={value}")


if __name__ == "__main__":
    main()
