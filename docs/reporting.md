# Reporting

After each generation is evaluated, `evaluator/watcher.py` automatically generates a report and uploads it to S3.

## How it works

1. `watcher.py` calls `_run_evaluation()` which writes all evaluation artifacts to `s3://runs/evaluations/{gen_id}/`.
2. After `status` is set to `"evaluated"` in Redis, `generate_report(s3, gen_id, tmp_dir)` is called.
3. `report.py` downloads the evaluation artifacts, generates charts, renders `single.md`, zips everything, and uploads to `s3://runs/reports/{gen_id}/report.zip`.
4. Failures are non-fatal — evaluation results are already saved; a warning is logged if report generation fails.

## Inputs

`report.py` reads from `s3://runs/evaluations/{gen_id}/`:
- `runs.json` — list of `RunArtifact` dicts
- `aggregates.json` — list of `CandidateAggregate` dicts
- `promotions.json` — list of `PromotionDecision` dicts
- `frontier.json` — list of `FrontierEntry` dicts
- `next_jobs.json` — list of `NextJob` dicts
- `allocation_summary.json` — `AllocationSummary` dict

Missing files are handled gracefully (empty list/dict fallback).

## Output

`s3://runs/reports/{gen_id}/report.zip` containing:
- `single.md` — human-readable markdown summary
- `images/best_so_far_{resource_class}.png` — best delta per candidate (gnuplot)
- `images/promotion_funnel.png` — promotion funnel bar chart (gnuplot)
- `images/lineage.png` — candidate lineage digraph (graphviz dot)

Charts require `gnuplot` and `dot` (graphviz) to be installed. If either tool is missing the chart is skipped silently.

## CLI usage

Run the report CLI standalone (useful for re-generating a report or testing):

```bash
export S3_ENDPOINT_URL=http://minio:9000
export S3_ACCESS_KEY=minioadmin
export S3_SECRET_KEY=minioadmin

# Generate and upload
python3 -m evaluator.report_cli --gen-id <gen_id>

# Generate locally only (no upload)
python3 -m evaluator.report_cli --gen-id <gen_id> --no-upload --output-dir /tmp/myreport
```
