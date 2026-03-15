from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Union

from evaluator.models import RunArtifact

REQUIRED_FILES = ("run.json", "metrics.json", "lineage.json")


def _load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_run_dirs(root: Path) -> List[Path]:
    run_dirs = []
    for run_file in root.rglob("run.json"):
        run_dirs.append(run_file.parent)
    return sorted(run_dirs)


def _normalize_parent_candidate_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned if cleaned else None


def _build_run_artifact(run_dir: Path, payloads: Dict[str, Dict]) -> RunArtifact:
    run_payload = payloads.get("run.json", {})
    metrics_payload = payloads.get("metrics.json", {})
    lineage_payload = payloads.get("lineage.json", {})

    primary = metrics_payload.get("primary_metric", {})
    run_value = primary.get("value", 0.0)
    direction = primary.get("direction", "min")
    parent_candidate_id = _normalize_parent_candidate_id(lineage_payload.get("parent_candidate_id"))
    parent_value = lineage_payload.get("parent_primary_metric_value")
    is_seed_run = bool(lineage_payload.get("is_seed_run", False)) or parent_candidate_id is None

    artifact_paths = {
        "run": str((run_dir / "run.json")),
        "metrics": str((run_dir / "metrics.json")),
        "lineage": str((run_dir / "lineage.json")),
        "stdout": str((run_dir / "stdout.log")),
        "stderr": str((run_dir / "stderr.log")),
        "patch": str((run_dir / "patch.diff")),
    }

    artifact = RunArtifact(
        run_id=str(run_payload.get("run_id", "")),
        candidate_id=str(run_payload.get("candidate_id", "")),
        parent_candidate_id=parent_candidate_id,
        agent_id=str(run_payload.get("agent_id", "")),
        worker_id=str(run_payload.get("worker_id", "")),
        resource_class=str(run_payload.get("resource_class", "")),
        seed=int(run_payload.get("seed", 0)),
        status=str(run_payload.get("status", "")),
        model_family=run_payload.get("model_family"),
        task_type=run_payload.get("task_type"),
        primary_metric_name=str(primary.get("name", "")),
        primary_metric_direction=str(direction),
        run_primary_metric_value=float(run_value) if run_value is not None else 0.0,
        parent_primary_metric_value=float(parent_value) if parent_value is not None else None,
        delta_primary_metric=None,
        normalized_delta=None,
        training_budget=run_payload.get("training_budget", {}),
        wall_clock_used_seconds=float(run_payload.get("wall_clock_used_seconds", 0.0)),
        artifact_paths=artifact_paths,
        created_at=str(run_payload.get("created_at", "")),
        completed_at=run_payload.get("completed_at"),
        source_dir=str(run_dir),
        is_seed_run=is_seed_run,
    )
    return artifact


def load_run_dir(run_dir: Path) -> RunArtifact:
    payloads: Dict[str, Dict] = {}
    for filename in REQUIRED_FILES:
        path = run_dir / filename
        if path.exists():
            payloads[filename] = _load_json(path)
    return _build_run_artifact(run_dir, payloads)


@dataclass
class LoadedRun:
    run: RunArtifact
    payloads: Dict[str, Dict]


class ArtifactLoader(Protocol):
    def list_runs(self, source: Union[str, Path]) -> List[LoadedRun]:
        ...


class FilesystemArtifactLoader:
    def list_runs(self, source: Union[str, Path]) -> List[LoadedRun]:
        source_path = Path(source)
        loaded: List[LoadedRun] = []
        for run_dir in find_run_dirs(source_path):
            payloads: Dict[str, Dict] = {}
            for filename in REQUIRED_FILES:
                path = run_dir / filename
                if path.exists():
                    payloads[filename] = _load_json(path)
            loaded.append(LoadedRun(run=_build_run_artifact(run_dir, payloads), payloads=payloads))
        return loaded


def load_runs(root: Union[str, Path], loader: Optional[ArtifactLoader] = None) -> List[LoadedRun]:
    selected_loader = loader or FilesystemArtifactLoader()
    return selected_loader.list_runs(root)
