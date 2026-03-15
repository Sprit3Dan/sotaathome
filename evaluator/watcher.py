"""
evaluator/watcher.py — Generation-aware minio polling loop.

Started as a daemon thread from orchestration/server.py on startup.
Every POLL_INTERVAL_S seconds it scans Redis for active generations,
counts completed run.json uploads, triggers evaluation when all pods
are done, and re-submits the next generation if needed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List

import boto3
import redis
import requests

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 10
BUCKET = "runs"


# ── S3 helpers ────────────────────────────────────────────────────────────────

def _make_s3(settings):
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
    )


def _count_completed_runs(s3, gen_id: str) -> int:
    paginator = s3.get_paginator("list_objects_v2")
    prefix = f"generations/{gen_id}/"
    count = 0
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/run.json"):
                count += 1
    return count


def _upload_json(s3, key: str, data: Any) -> None:
    body = json.dumps(data, indent=2, sort_keys=True).encode()
    s3.put_object(Bucket=BUCKET, Key=key, Body=body)


# ── Evaluation pipeline ───────────────────────────────────────────────────────

def _run_evaluation(s3, gen_id: str) -> List[Dict]:
    """
    Run the evaluator pipeline using MinioArtifactLoader.
    Uploads all result JSONs to minio under evaluations/{gen_id}/.
    Returns enriched next_jobs list (each dict includes metric_value).
    """
    from evaluator.aggregate import aggregate_candidates
    from evaluator.allocate import build_next_jobs
    from evaluator.frontier import build_frontier
    from evaluator.loader import MinioArtifactLoader, load_runs
    from evaluator.models import EvaluatorConfig
    from evaluator.promote import decide_promotions
    from evaluator.validate import validate_run

    config = EvaluatorConfig()
    loader = MinioArtifactLoader(s3_client=s3, bucket=BUCKET)
    prefix = f"generations/{gen_id}/"

    loaded = load_runs(prefix, loader=loader)
    if not loaded:
        logger.warning(f"[watcher] No runs loaded for gen {gen_id}")
        return []

    runs = [validate_run(item.run, item.payloads, config) for item in loaded]
    aggregates = aggregate_candidates(runs)
    promotions = decide_promotions(aggregates, runs, config)
    frontier = build_frontier(aggregates, promotions, config)
    next_jobs, allocation_summary = build_next_jobs(frontier, config)

    eval_prefix = f"evaluations/{gen_id}/"
    _upload_json(s3, eval_prefix + "runs.json", [r.to_dict() for r in runs])
    _upload_json(s3, eval_prefix + "aggregates.json", [a.to_dict() for a in aggregates])
    _upload_json(s3, eval_prefix + "promotions.json", [p.to_dict() for p in promotions])
    _upload_json(s3, eval_prefix + "frontier.json", [f.to_dict() for f in frontier])
    _upload_json(s3, eval_prefix + "next_jobs.json", [j.to_dict() for j in next_jobs])
    _upload_json(s3, eval_prefix + "allocation_summary.json", allocation_summary.to_dict())

    # Build best metric value per candidate (min direction assumed for val_bpb)
    metric_by_candidate: Dict[str, float] = {}
    for r in runs:
        cid = r.candidate_id
        val = r.run_primary_metric_value
        if cid not in metric_by_candidate or val < metric_by_candidate[cid]:
            metric_by_candidate[cid] = val

    enriched = []
    for job in next_jobs:
        d = job.to_dict()
        d["metric_value"] = metric_by_candidate.get(job.parent_candidate_id)
        enriched.append(d)

    # Compute generation-level best
    best_val_bpb_gen: float | None = None
    best_run_id_gen: str | None = None
    for r in runs:
        val = r.run_primary_metric_value
        if best_val_bpb_gen is None or val < best_val_bpb_gen:
            best_val_bpb_gen = val
            best_run_id_gen = r.run_id

    return enriched, best_val_bpb_gen, best_run_id_gen


# ── Per-generation processing ─────────────────────────────────────────────────

def _process_generation(r: redis.Redis, s3, gen_id: str) -> None:
    gen_key = f"generation:{gen_id}"
    gen_data = r.hgetall(gen_key)
    if not gen_data or gen_data.get("status") != "running":
        return

    expected_pods = int(gen_data.get("expected_pods", 1))
    completed = _count_completed_runs(s3, gen_id)
    logger.debug(f"[watcher] gen={gen_id} completed={completed}/{expected_pods}")

    if completed < expected_pods:
        r.hset(gen_key, "pods_done", str(completed))
        return

    logger.info(f"[watcher] gen={gen_id} all {completed} pods done. Starting evaluation.")
    r.hset(gen_key, "status", "evaluating")

    try:
        next_jobs, best_val_bpb, best_run_id = _run_evaluation(s3, gen_id)
    except Exception as exc:
        logger.exception(f"[watcher] Evaluation failed for gen {gen_id}: {exc}")
        r.hset(gen_key, "status", "eval_failed")
        return

    eval_fields: dict = {"pods_done": str(completed)}
    if best_val_bpb is not None:
        eval_fields["best_val_bpb"] = str(best_val_bpb)
    if best_run_id is not None:
        eval_fields["best_run_id"] = best_run_id
    r.hset(gen_key, mapping=eval_fields)
    r.hset(gen_key, "status", "evaluated")
    logger.info(f"[watcher] gen={gen_id} evaluated. next_jobs={len(next_jobs)}")

    gen_num = int(gen_data.get("generation_num", 1))
    total_gens = int(gen_data.get("total_generations", 1))

    if gen_num >= total_gens:
        r.hset(gen_key, "status", "done")
        logger.info(f"[watcher] gen={gen_id} is final generation. Done.")
        return

    # Build next-generation request
    try:
        orig_req = json.loads(gen_data.get("request_json", "{}"))
    except Exception:
        logger.error(f"[watcher] Could not parse request_json for gen {gen_id}")
        r.hset(gen_key, "status", "done")
        return

    # Deduplicate parent candidates preserving order
    seen: set[str] = set()
    parent_ids: List[str] = []
    parent_vals: List[float] = []
    for job in next_jobs:
        cid = job.get("parent_candidate_id")
        val = job.get("metric_value")
        if cid and cid not in seen and val is not None:
            seen.add(cid)
            parent_ids.append(cid)
            parent_vals.append(val)

    parent_train_keys = [f"generations/{gen_id}/{cid}/train.py" for cid in parent_ids]

    next_req = {
        **orig_req,
        "generation_num": gen_num + 1,
        "parent_candidate_ids": parent_ids,
        "parent_metric_values": parent_vals,
        "parent_train_s3_keys": parent_train_keys,
    }

    try:
        resp = requests.post("http://localhost:8000/submit", json=next_req, timeout=30)
        resp.raise_for_status()
        logger.info(f"[watcher] Submitted gen {gen_num + 1} from gen {gen_id}: {resp.json()}")
        r.hset(gen_key, "status", "next_gen_submitted")
    except Exception as exc:
        logger.exception(f"[watcher] Failed to submit next gen from {gen_id}: {exc}")
        r.hset(gen_key, "status", "next_gen_submit_failed")


# ── Main loop ─────────────────────────────────────────────────────────────────

def _watcher_loop(settings) -> None:
    logger.info("[watcher] Starting generation watcher loop.")
    r = redis.Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, decode_responses=True
    )
    s3 = _make_s3(settings)

    while True:
        try:
            gen_keys = list(r.scan_iter("generation:*"))
            for gen_key in gen_keys:
                gen_id = gen_key.split(":", 1)[1]
                try:
                    _process_generation(r, s3, gen_id)
                except Exception as exc:
                    logger.exception(f"[watcher] Error processing gen {gen_id}: {exc}")
        except Exception as exc:
            logger.exception(f"[watcher] Loop error: {exc}")
        time.sleep(POLL_INTERVAL_S)


def start_watcher_thread(settings) -> threading.Thread:
    t = threading.Thread(
        target=_watcher_loop,
        args=(settings,),
        daemon=True,
        name="generation-watcher",
    )
    t.start()
    return t
