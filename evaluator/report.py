"""
evaluator/report.py — Post-run report generator.

Loads evaluation JSONs from S3 (written by watcher.py), generates charts
via gnuplot and graphviz, renders single.md, zips everything, and uploads
to s3://runs/reports/{gen_id}/report.zip.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BUCKET = "runs"


# ── S3 helpers ────────────────────────────────────────────────────────────────

def _download_json(s3, key: str) -> Any:
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    return json.loads(obj["Body"].read())


def _upload_file(s3, key: str, path: Path) -> str:
    s3.upload_file(str(path), BUCKET, key)
    return key


# ── Artifact loading ──────────────────────────────────────────────────────────

def _load_eval_artifacts(s3, gen_id: str) -> Dict[str, Any]:
    artifacts: Dict[str, Any] = {}
    names = ["runs", "aggregates", "promotions", "frontier", "next_jobs", "allocation_summary"]
    for name in names:
        key = f"evaluations/{gen_id}/{name}.json"
        try:
            artifacts[name] = _download_json(s3, key)
        except Exception as exc:
            logger.warning(f"[report] Could not load {key}: {exc}")
            artifacts[name] = [] if name != "allocation_summary" else {}
    return artifacts


# ── Chart generation ──────────────────────────────────────────────────────────

def _run_gnuplot(script: str, output_path: Path) -> bool:
    if not shutil.which("gnuplot"):
        logger.warning("[report] gnuplot not found, skipping chart")
        return False
    try:
        result = subprocess.run(
            ["gnuplot"],
            input=script.encode(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"[report] gnuplot error: {result.stderr.decode()[:200]}")
            return False
        return output_path.exists()
    except Exception as exc:
        logger.warning(f"[report] gnuplot failed: {exc}")
        return False


def _run_dot(dot_src: str, output_path: Path) -> bool:
    if not shutil.which("dot"):
        logger.warning("[report] graphviz dot not found, skipping lineage chart")
        return False
    try:
        result = subprocess.run(
            ["dot", "-Tpng", "-o", str(output_path)],
            input=dot_src.encode(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"[report] dot error: {result.stderr.decode()[:200]}")
            return False
        return output_path.exists()
    except Exception as exc:
        logger.warning(f"[report] dot failed: {exc}")
        return False


def _best_so_far_chart(aggregates: List[Dict], images_dir: Path) -> Optional[str]:
    """Generate best_so_far_<resource_class>.png for each resource class."""
    if not aggregates:
        return None

    # Group by resource_class
    by_rc: Dict[str, List[Dict]] = {}
    for agg in aggregates:
        rc = agg.get("resource_class", "unknown")
        by_rc.setdefault(rc, []).append(agg)

    created = []
    for rc, aggs in by_rc.items():
        # Sort by best_delta ascending for "min" metric
        direction = aggs[0].get("primary_metric_direction", "min")
        sorted_aggs = sorted(
            aggs,
            key=lambda a: (a.get("best_delta_primary_metric") or 0),
            reverse=(direction == "max"),
        )

        data_lines = []
        for i, agg in enumerate(sorted_aggs):
            cid = agg.get("candidate_id", "?")[:8]
            val = agg.get("best_delta_primary_metric")
            if val is None:
                continue
            improved = 1 if (agg.get("improved_run_count") or 0) > 0 else 0
            data_lines.append(f"{i} {val} {improved} {cid!r}")

        if not data_lines:
            continue

        fname = f"best_so_far_{rc}.png"
        out_path = images_dir / fname
        data_block = "\n".join(data_lines)

        script = textwrap.dedent(f"""\
            set terminal png size 800,400
            set output '{out_path}'
            set style data histogram
            set style fill solid
            set boxwidth 0.8
            set title 'Best delta per candidate ({rc})'
            set xlabel 'Candidate'
            set ylabel 'Best delta'
            set xtics rotate by -45
            $data << EOD
            {data_block}
            EOD
            plot $data using 2:xtic(4) lc variable title 'best delta'
        """)

        if _run_gnuplot(script, out_path):
            created.append(f"images/{fname}")

    return created if created else None


def _promotion_funnel_chart(runs: List[Dict], promotions: List[Dict], images_dir: Path) -> Optional[str]:
    total = len(runs)
    valid = sum(1 for r in runs if r.get("valid"))
    improved = sum(1 for r in runs if (r.get("normalized_delta") or 0) > 0)
    bronze = sum(1 for p in promotions if p.get("promotion_level") == "bronze")
    silver = sum(1 for p in promotions if p.get("promotion_level") == "silver")
    gold = sum(1 for p in promotions if p.get("promotion_level") == "gold")

    out_path = images_dir / "promotion_funnel.png"
    script = textwrap.dedent(f"""\
        set terminal png size 600,400
        set output '{out_path}'
        set title 'Promotion Funnel'
        set style data histograms
        set style fill solid
        set yrange [0:{max(total, 1)}]
        set boxwidth 0.6
        $data << EOD
        "Total" {total}
        "Valid" {valid}
        "Improved" {improved}
        "Bronze" {bronze}
        "Silver" {silver}
        "Gold" {gold}
        EOD
        plot $data using 2:xtic(1) title '' lc rgb '#4477AA'
    """)

    if _run_gnuplot(script, out_path):
        return "images/promotion_funnel.png"
    return None


def _lineage_chart(runs: List[Dict], promotions: List[Dict], images_dir: Path) -> Optional[str]:
    level_by_candidate = {p.get("candidate_id"): p.get("promotion_level", "none") for p in promotions}

    color_map = {
        "gold": "yellow",
        "silver": "lightgray",
        "bronze": "sandybrown",
        "none": "white",
    }

    nodes: Dict[str, str] = {}
    edges: List[tuple] = []
    seen_cids = set()

    for r in runs:
        cid = r.get("candidate_id")
        pcid = r.get("parent_candidate_id")
        if cid and cid not in seen_cids:
            seen_cids.add(cid)
            level = level_by_candidate.get(cid, "none")
            color = color_map.get(level, "white")
            short = cid[:8]
            nodes[cid] = f'  "{short}" [style=filled, fillcolor={color}]'
        if cid and pcid:
            edges.append((pcid[:8], cid[:8]))

    dot_lines = ["digraph lineage {", "  rankdir=LR;"]
    dot_lines += list(nodes.values())
    for src, dst in edges:
        dot_lines.append(f'  "{src}" -> "{dst}"')
    dot_lines.append("}")
    dot_src = "\n".join(dot_lines)

    out_path = images_dir / "lineage.png"
    if _run_dot(dot_src, out_path):
        return "images/lineage.png"
    return None


def _generate_images(artifacts: Dict[str, Any], images_dir: Path) -> List[str]:
    images_dir.mkdir(parents=True, exist_ok=True)
    image_paths: List[str] = []

    try:
        result = _best_so_far_chart(artifacts.get("aggregates", []), images_dir)
        if result:
            image_paths.extend(result)
    except Exception as exc:
        logger.warning(f"[report] best_so_far chart failed: {exc}")

    try:
        path = _promotion_funnel_chart(
            artifacts.get("runs", []),
            artifacts.get("promotions", []),
            images_dir,
        )
        if path:
            image_paths.append(path)
    except Exception as exc:
        logger.warning(f"[report] promotion_funnel chart failed: {exc}")

    try:
        path = _lineage_chart(
            artifacts.get("runs", []),
            artifacts.get("promotions", []),
            images_dir,
        )
        if path:
            image_paths.append(path)
    except Exception as exc:
        logger.warning(f"[report] lineage chart failed: {exc}")

    return image_paths


# ── Markdown rendering ────────────────────────────────────────────────────────

def _md_table(rows: List[Dict], columns: List[str]) -> str:
    if not rows:
        return "_none_"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, sep]
    for row in rows:
        vals = [str(row.get(c, "")) for c in columns]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def _render_markdown(artifacts: Dict[str, Any], image_paths: List[str], gen_id: str) -> str:
    runs = artifacts.get("runs", [])
    promotions = artifacts.get("promotions", [])
    frontier = artifacts.get("frontier", [])
    next_jobs = artifacts.get("next_jobs", [])

    total = len(runs)
    valid = sum(1 for r in runs if r.get("valid"))
    improved = sum(1 for r in runs if (r.get("normalized_delta") or 0) > 0)
    bronze = sum(1 for p in promotions if p.get("promotion_level") == "bronze")
    silver = sum(1 for p in promotions if p.get("promotion_level") == "silver")
    gold = sum(1 for p in promotions if p.get("promotion_level") == "gold")

    # Best candidate
    best_run = None
    for r in runs:
        if r.get("valid") and (
            best_run is None
            or r.get("run_primary_metric_value", float("inf"))
            < best_run.get("run_primary_metric_value", float("inf"))
        ):
            best_run = r
    best_candidate = best_run.get("candidate_id", "N/A")[:8] if best_run else "N/A"
    best_val = best_run.get("run_primary_metric_value") if best_run else None
    best_val_str = f"{best_val:.4f}" if best_val is not None else "N/A"

    # Per resource class summary
    rc_counts: Dict[str, Dict[str, int]] = {}
    for r in runs:
        rc = r.get("resource_class", "unknown")
        rc_counts.setdefault(rc, {"total": 0, "valid": 0})
        rc_counts[rc]["total"] += 1
        if r.get("valid"):
            rc_counts[rc]["valid"] += 1
    rc_rows = [{"resource_class": rc, **v} for rc, v in sorted(rc_counts.items())]
    rc_table = _md_table(rc_rows, ["resource_class", "total", "valid"])

    # Best so far images
    rc_names = list(rc_counts.keys())
    best_so_far_imgs = "\n".join(
        f"![Best val_bpb {rc}](images/best_so_far_{rc}.png)"
        for rc in rc_names
        if f"images/best_so_far_{rc}.png" in image_paths
    ) or "_Chart not available._"

    # Promotions table
    promo_table = _md_table(
        promotions,
        ["candidate_id", "resource_class", "promotion_level"],
    )

    # Frontier table
    frontier_table = _md_table(
        frontier,
        ["candidate_id", "resource_class", "role", "promotion_level", "score_hint"],
    )

    # Lineage image
    lineage_img = (
        "![Lineage graph](images/lineage.png)"
        if "images/lineage.png" in image_paths
        else "_Chart not available._"
    )

    # Next jobs table
    next_jobs_table = _md_table(
        next_jobs,
        ["resource_class", "parent_candidate_id", "job_type", "rationale"],
    )

    # Failures
    failures = [r for r in runs if not r.get("valid") and r.get("status") not in ("", None)]
    if failures:
        failures_list = "\n".join(
            f"- `{r.get('run_id', '')[:8]}` — {r.get('status', '?')}: {', '.join(r.get('validation_errors', []))}"
            for r in failures[:20]
        )
    else:
        failures_list = "_None._"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return textwrap.dedent(f"""\
        # SoTA@Home Generation Report: {gen_id}

        Generated: {timestamp}

        ## Executive Summary
        - Runs: {total} total, {valid} valid, {improved} improved
        - Promotions: {bronze} bronze, {silver} silver, {gold} gold
        - Best candidate: {best_candidate} — val_bpb={best_val_str}

        ## Resource Class Summary
        {rc_table}

        ## Best So Far
        {best_so_far_imgs}

        ## Promotions
        {promo_table}

        ## Frontier
        {frontier_table}

        ## Lineage
        {lineage_img}

        ## Next Iteration Plan
        {next_jobs_table}

        ## Notable Failures
        {failures_list}

        ## Artifacts
        - S3 report zip: s3://runs/reports/{gen_id}/report.zip
        - S3 eval data: s3://runs/evaluations/{gen_id}/
    """)


# ── Zip and upload ────────────────────────────────────────────────────────────

def _zip_report(report_dir: Path) -> Path:
    zip_path = report_dir / "report.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        md_path = report_dir / "single.md"
        if md_path.exists():
            zf.write(md_path, "single.md")
        images_dir = report_dir / "images"
        if images_dir.exists():
            for img in images_dir.iterdir():
                zf.write(img, f"images/{img.name}")
    return zip_path


def _upload_zip(s3, gen_id: str, zip_path: Path) -> str:
    key = f"reports/{gen_id}/report.zip"
    _upload_file(s3, key, zip_path)
    return key


# ── Public entry point ────────────────────────────────────────────────────────

def generate_report(s3, gen_id: str, output_dir: Path, upload: bool = True) -> Path:
    """
    Load evaluation artifacts from s3://runs/evaluations/{gen_id}/*.json,
    generate images, render single.md, zip to report.zip.
    Uploads to S3 unless upload=False.
    Returns path to local report.zip.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"

    logger.info(f"[report] Generating report for gen {gen_id}")

    artifacts = _load_eval_artifacts(s3, gen_id)
    image_paths = _generate_images(artifacts, images_dir)
    markdown = _render_markdown(artifacts, image_paths, gen_id)

    md_path = output_dir / "single.md"
    md_path.write_text(markdown, encoding="utf-8")

    zip_path = _zip_report(output_dir)
    logger.info(f"[report] Report zip: {zip_path}")

    if upload:
        key = _upload_zip(s3, gen_id, zip_path)
        logger.info(f"[report] Uploaded to s3://runs/{key}")

    return zip_path
