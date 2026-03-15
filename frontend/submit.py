import argparse
import os
import sys
from typing import Any

import requests


QUEUE_URL = os.getenv("QUEUE_URL", "http://127.0.0.1:8000")
DEFAULT_DATASET_REPO = os.getenv("TUI_DATASET_HF_REPO", "HuggingFaceFW/fineweb")
DEFAULT_TEXT_COLUMN = os.getenv("TUI_DATASET_TEXT_COLUMN", "text")
DEFAULT_TRAIN_SPLIT = os.getenv("TUI_DATASET_TRAIN_SPLIT", "train")
DEFAULT_VAL_SPLIT = os.getenv("TUI_DATASET_VAL_SPLIT", "validation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Submit an autoresearch job to the orchestrator."
    )
    parser.add_argument(
        "--queue-url",
        default=QUEUE_URL,
        help=f"Orchestrator base URL (default: {QUEUE_URL})",
    )
    parser.add_argument("--generation-num", type=int, default=1)
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--n", type=int, default=1, help="Number of parallel jobs")
    parser.add_argument("--m", type=int, default=1, help="Max iterations")
    parser.add_argument("--t", type=int, default=300, help="Time budget seconds")
    parser.add_argument(
        "--dataset-hf-repo",
        default=DEFAULT_DATASET_REPO,
        help=f"Dataset repo (default: {DEFAULT_DATASET_REPO})",
    )
    parser.add_argument(
        "--dataset-text-column",
        default=DEFAULT_TEXT_COLUMN,
        help=f"Dataset text column (default: {DEFAULT_TEXT_COLUMN})",
    )
    parser.add_argument(
        "--dataset-train-split",
        default=DEFAULT_TRAIN_SPLIT,
        help=f"Dataset train split (default: {DEFAULT_TRAIN_SPLIT})",
    )
    parser.add_argument(
        "--dataset-val-split",
        default=DEFAULT_VAL_SPLIT,
        help=f"Dataset val split (default: {DEFAULT_VAL_SPLIT})",
    )
    parser.add_argument(
        "--research-direction",
        default="",
        help="Optional research direction override",
    )
    parser.add_argument(
        "--agent-script-file",
        default="",
        help="Optional path to a Python file to upload as agent_script",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.generation_num < 1:
        raise ValueError("--generation-num must be >= 1")
    if args.generations < 1:
        raise ValueError("--generations must be >= 1")
    if args.n < 1:
        raise ValueError("--n must be >= 1")
    if args.m < 1:
        raise ValueError("--m must be >= 1")
    if args.t < 1:
        raise ValueError("--t must be >= 1")
    if not args.dataset_hf_repo.strip():
        raise ValueError("--dataset-hf-repo is required")
    if not args.dataset_text_column.strip():
        raise ValueError("--dataset-text-column is required")
    if not args.dataset_train_split.strip():
        raise ValueError("--dataset-train-split is required")
    if not args.dataset_val_split.strip():
        raise ValueError("--dataset-val-split is required")


def load_agent_script(path: str) -> str:
    if not path:
        return ""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generation_num": args.generation_num,
        "generations": args.generations,
        "n": args.n,
        "m": args.m,
        "t": args.t,
        "dataset_hf_repo": args.dataset_hf_repo,
        "dataset_text_column": args.dataset_text_column,
        "dataset_train_split": args.dataset_train_split,
        "dataset_val_split": args.dataset_val_split,
    }
    if args.research_direction.strip():
        payload["research_direction"] = args.research_direction.strip()
    agent_script = load_agent_script(args.agent_script_file)
    if agent_script:
        payload["agent_script"] = agent_script
    return payload


def submit_job(queue_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{queue_url.rstrip('/')}/submit"
    response = requests.post(url, json=payload, timeout=20)
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        validate_args(args)
        payload = build_payload(args)
        result = submit_job(args.queue_url, payload)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        print(f"http error: {detail}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"request error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"file error: {exc}", file=sys.stderr)
        return 1

    print("submitted successfully")
    print(f"generation_id: {result.get('generation_id', '')}")
    print(f"task_id: {result.get('task_id', '')}")
    print(f"generation_num: {result.get('generation_num', '')}")
    print(f"total_generations: {result.get('total_generations', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())