# Storage & Upload Reference

All artifacts are stored in a single S3 bucket named `runs` (MinIO in dev).

## S3 key patterns

### Per-run artifacts
Written by `docker/entrypoint.sh` after each pod completes:

| Key | Description |
|---|---|
| `generations/{gen_id}/{run_id}/run.json` | Runtime metadata: status, timing, resource_class, candidate_id |
| `generations/{gen_id}/{run_id}/metrics.json` | `{primary_metric: {name, direction, value}}` |
| `generations/{gen_id}/{run_id}/lineage.json` | Parent linkage + is_seed_run flag |
| `generations/{gen_id}/{run_id}/train.py` | Final mutated training script |

### Generation metadata
Written by `orchestration/server.py` at submission time:

| Key | Description |
|---|---|
| `generations/{gen_id}/manifest.json` | Generation metadata: gen_num, expected_pods, dataset, submitted_at |
| `agents/{gen_id}/agent.py` | Custom agent script (only if provided at submit time) |

### Evaluation artifacts
Written by `evaluator/watcher.py` after all pods complete:

| Key | Description |
|---|---|
| `evaluations/{gen_id}/runs.json` | List of all `RunArtifact` dicts |
| `evaluations/{gen_id}/aggregates.json` | List of `CandidateAggregate` dicts |
| `evaluations/{gen_id}/promotions.json` | List of `PromotionDecision` dicts |
| `evaluations/{gen_id}/frontier.json` | List of `FrontierEntry` dicts |
| `evaluations/{gen_id}/next_jobs.json` | List of `NextJob` dicts |
| `evaluations/{gen_id}/allocation_summary.json` | `AllocationSummary` dict |

### Report artifacts
Written by `evaluator/report.py` after evaluation:

| Key | Description |
|---|---|
| `reports/{gen_id}/report.zip` | Zip of `single.md` + `images/` |

## Upload conventions

- JSON artifacts: `s3.put_object(Bucket="runs", Key=key, Body=json_bytes)` via `_upload_json()` in `watcher.py`
- File artifacts: `s3.upload_file(path, "runs", key)` via `_upload_file()` in `report.py`
- The bucket is created on server startup if it does not exist

## S3 client config

All services read from env vars:
- `S3_ENDPOINT_URL` — MinIO or S3 endpoint
- `S3_ACCESS_KEY` — access key
- `S3_SECRET_KEY` — secret key
