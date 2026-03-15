import json
import logging
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import boto3
import redis
from agent import generate_init_container_spec
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from k8s_deployer import deploy_research_job, list_jobs, list_nodes
from models import AutoresearchJobRequest, GitHubResearchItem, InitContainerSpec, JobAssignment, ResearchItem, TaskStatusUpdate, parse_research_item
from settings import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="AutoResearch Queue Manager")
worker_started = False
worker_lock = threading.Lock()


def _process_queue_forever():
    logger.info("Background queue worker started.")
    while True:
        try:
            task_str = redis_client.lpop(QUEUE_NAME)
            if not task_str:
                time.sleep(settings.POLL_INTERVAL_SECONDS)
                continue

            task_data = json.loads(task_str)
            task_item = parse_research_item(task_data)
            redis_client.hset(f"task:{task_item.id}", "status", "processing")

            if task_item.init_container_spec:
                spec = task_item.init_container_spec
            else:
                spec = generate_init_container_spec(task_item)

            redis_client.hset(f"task:{task_item.id}", "status", "deploying")
            result = deploy_research_job(task_item, spec)
            redis_client.hset(
                f"task:{task_item.id}",
                mapping={
                    "status": "running",
                    "pod_name": result.get("pod_name", ""),
                    "job_name": result.get("job_name", ""),
                },
            )
        except Exception as exc:
            logger.exception(f"Background queue worker error: {exc}")
            time.sleep(settings.POLL_INTERVAL_SECONDS)


def start_background_worker():
    global worker_started
    with worker_lock:
        if worker_started:
            return
        threading.Thread(
            target=_process_queue_forever,
            name="queue-worker",
            daemon=True,
        ).start()
        worker_started = True


@app.on_event("startup")
def _on_startup():
    # Ensure the single shared S3 bucket exists
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )
        try:
            s3.head_bucket(Bucket="runs")
        except Exception:
            s3.create_bucket(Bucket="runs")
            logger.info("Created S3 bucket 'runs'.")
    except Exception as exc:
        logger.warning(f"Could not ensure S3 bucket exists: {exc}")

    # Start generation watcher thread
    try:
        from evaluator.watcher import start_watcher_thread
        start_watcher_thread(settings)
        logger.info("Generation watcher thread started.")
    except Exception as exc:
        logger.warning(f"Could not start watcher thread: {exc}")

    try:
        start_background_worker()
        logger.info("Background queue worker thread started.")
    except Exception as exc:
        logger.warning(f"Could not start background queue worker: {exc}")


def get_redis_client():
    return redis.Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, decode_responses=True
    )


redis_client = get_redis_client()
QUEUE_NAME = settings.QUEUE_NAME


@app.post("/enqueue")
def enqueue_task(task: ResearchItem):
    """Add a new research task to the queue."""
    try:
        task_data = task.model_dump()
        redis_client.rpush(QUEUE_NAME, task.model_dump_json())

        # Track task metadata
        redis_client.hset(
            f"task:{task.id}",
            mapping={
                "status": "queued",
                "repo_ref": task.repo_ref,
                "research_direction": task.research_direction,
                "logs": "",
                "pod_name": "",
            },
        )

        logger.info(
            f"Enqueued task {task.id} for {task.repo_ref}: {task.research_direction}"
        )
        return {
            "status": "success",
            "message": "Task enqueued successfully",
            "task": task_data,
        }
    except Exception as e:
        logger.error(f"Failed to enqueue task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dequeue")
def dequeue_task():
    """Pop a research task from the queue."""
    try:
        task_str = redis_client.lpop(QUEUE_NAME)
        if task_str:
            task = json.loads(task_str)
            task_id = task.get("id")
            if task_id:
                redis_client.hset(f"task:{task_id}", "status", "processing")
            logger.info(
                f"Dequeued task {task_id} for {task.get('repo_ref')}: {task.get('research_direction')}"
            )
            return {"status": "success", "task": task}
        return {"status": "empty", "message": "No tasks in queue", "task": None}
    except Exception as e:
        logger.error(f"Failed to dequeue task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/status")
def queue_status():
    """Get the current length of the queue."""
    try:
        length = redis_client.llen(QUEUE_NAME)
        return {"status": "success", "queue_length": length}
    except Exception as e:
        logger.error(f"Failed to get queue status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/update_status")
def update_task_status(update: TaskStatusUpdate):
    """Update the status of a specific task."""
    try:
        task_key = f"task:{update.task_id}"
        if not redis_client.exists(task_key):
            raise HTTPException(status_code=404, detail="Task not found")

        mapping = {"status": update.status}
        if update.logs:
            mapping["logs"] = update.logs
        if update.pod_name:
            mapping["pod_name"] = update.pod_name

        redis_client.hset(task_key, mapping=mapping)
        logger.info(f"Updated task {update.task_id} status to {update.status}")
        return {"status": "success", "message": f"Task {update.task_id} updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update task status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/task/{task_id}")
def get_task(task_id: str):
    """Get the full metadata and status of a specific task."""
    try:
        task_data = redis_client.hgetall(f"task:{task_id}")
        if not task_data:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"status": "success", "task": task_data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tasks")
def list_tasks():
    """List all tracked tasks for dashboard and TUI views."""
    try:
        tasks = []
        for key in redis_client.scan_iter("task:*"):
            task_id = key.split(":", 1)[1]
            task_data = redis_client.hgetall(key)
            if task_data:
                tasks.append(
                    {
                        "task_id": task_id,
                        "status": task_data.get("status", ""),
                        "repo_ref": task_data.get("repo_ref", ""),
                        "research_direction": task_data.get("research_direction", ""),
                        "pod_name": task_data.get("pod_name", ""),
                    }
                )
        tasks.sort(key=lambda item: item["task_id"])
        return {"status": "success", "tasks": tasks}
    except Exception as e:
        logger.error(f"Failed to list tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cluster_status")
def cluster_status():
    """Return cluster/job/task status for terminal dashboard views."""
    try:
        queue_length = redis_client.llen(QUEUE_NAME)
        tasks = []
        for key in redis_client.scan_iter("task:*"):
            task_id = key.split(":", 1)[1]
            task_data = redis_client.hgetall(key)
            if task_data:
                tasks.append(
                    {
                        "task_id": task_id,
                        "status": task_data.get("status", ""),
                        "repo_ref": task_data.get("repo_ref", ""),
                        "research_direction": task_data.get("research_direction", ""),
                        "pod_name": task_data.get("pod_name", ""),
                    }
                )

        tasks.sort(key=lambda item: item["task_id"])

        generations = []
        for key in redis_client.scan_iter("generation:*"):
            gen_id_val = key.split(":", 1)[1]
            gen_data = redis_client.hgetall(key)
            if gen_data:
                best_bpb_raw = gen_data.get("best_val_bpb")
                generations.append({
                    "gen_id": gen_id_val,
                    "generation_num": gen_data.get("generation_num", "?"),
                    "total_generations": gen_data.get("total_generations", "?"),
                    "status": gen_data.get("status", ""),
                    "pods_done": gen_data.get("pods_done", "0"),
                    "expected_pods": gen_data.get("expected_pods", "?"),
                    "best_val_bpb": float(best_bpb_raw) if best_bpb_raw else None,
                    "best_run_id": gen_data.get("best_run_id"),
                })
        generations.sort(key=lambda g: g["gen_id"])

        jobs = list_jobs()
        try:
            nodes = list_nodes()
        except Exception as node_err:
            logger.warning(f"Failed to list nodes for cluster status: {node_err}")
            nodes = []

        return {
            "status": "success",
            "queue_length": queue_length,
            "tasks": tasks,
            "jobs": jobs,
            "nodes": nodes,
            "generations": generations,
        }
    except Exception as e:
        logger.error(f"Failed to get cluster status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute")
def execute_task(task: ResearchItem):
    """Directly execute a research task without queueing."""
    try:
        logger.info(
            f"Directly executing task for {task.repo_ref}: {task.research_direction}"
        )

        redis_client.hset(
            f"task:{task.id}",
            mapping={
                "status": "executing_directly",
                "repo_ref": task.repo_ref,
                "research_direction": task.research_direction,
            },
        )

        spec = generate_init_container_spec(task)
        result = deploy_research_job(task, spec)

        redis_client.hset(
            f"task:{task.id}",
            mapping={
                "status": result["status"],
                "logs": result.get("logs", ""),
                "pod_name": result.get("pod_name", ""),
            },
        )

        return {
            "status": "success",
            "message": "Task executed directly",
            "result": result,
        }
    except Exception as e:
        logger.exception(f"Failed to execute task directly: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/submit")
def submit_job(req: AutoresearchJobRequest):
    """Submit an autoresearch job with dataset, parallelism, and generation params."""
    try:
        gen_id = uuid4().hex[:8]
        submitted_at = datetime.now(timezone.utc).isoformat()

        s3 = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )

        # Write manifest (expected_pods determined after building assignments below)
        # Placeholder — will be re-written after assignments are resolved
        expected_pods_placeholder = len(req.job_assignments) if req.job_assignments else req.n
        manifest = {
            "generation_id": gen_id,
            "generation_num": req.generation_num,
            "total_generations": req.generations,
            "expected_pods": expected_pods_placeholder,
            "dataset_hf_repo": req.dataset_hf_repo,
            "submitted_at": submitted_at,
        }
        s3.put_object(
            Bucket="runs",
            Key=f"generations/{gen_id}/manifest.json",
            Body=json.dumps(manifest, indent=2).encode(),
        )

        # Build per-job assignments (one pod per assignment)
        if req.job_assignments:
            assignments = req.job_assignments
        else:
            # First generation or manual submit: replicate n identical pods
            base_assignment = JobAssignment(
                parent_candidate_id=req.parent_candidate_ids[0] if req.parent_candidate_ids else "",
                parent_metric_value=req.parent_metric_values[0] if req.parent_metric_values else None,
                parent_train_s3_key=req.parent_train_s3_keys[0] if req.parent_train_s3_keys else None,
            )
            assignments = [base_assignment for _ in range(req.n)]

        expected_pods = len(assignments)

        # Store generation state in Redis
        redis_client.hset(
            f"generation:{gen_id}",
            mapping={
                "status": "running",
                "generation_num": req.generation_num,
                "total_generations": req.generations,
                "expected_pods": expected_pods,
                "dataset_hf_repo": req.dataset_hf_repo,
                "request_json": req.model_dump_json(),
            },
        )

        # Upload agent script once if provided
        agent_key: str | None = None
        if req.agent_script:
            agent_key = f"agents/{gen_id}/agent.py"
            s3.put_object(Bucket="runs", Key=agent_key, Body=req.agent_script.encode())
            logger.info(f"Uploaded custom agent to s3://runs/{agent_key}")

        research_direction = req.research_direction or f"Minimize val_bpb on {req.dataset_hf_repo}"
        task_ids = []

        for assignment in assignments:
            env: dict[str, str] = {
                "AUTORESEARCH_GENERATION_ID": gen_id,
                "AUTORESEARCH_MAX_ITERATIONS": str(req.m),
                "TIME_BUDGET_SECS": str(req.t),
                "DATASET_HF_REPO": req.dataset_hf_repo,
                "DATASET_TEXT_COLUMN": req.dataset_text_column,
                "DATASET_TRAIN_SPLIT": req.dataset_train_split,
                "DATASET_VAL_SPLIT": req.dataset_val_split,
            }
            if assignment.parent_candidate_id:
                env["AUTORESEARCH_PARENT_CANDIDATE_ID"] = assignment.parent_candidate_id
            if assignment.parent_metric_value is not None:
                env["AUTORESEARCH_PARENT_METRIC_VALUE"] = str(assignment.parent_metric_value)
            if assignment.parent_train_s3_key:
                env["AUTORESEARCH_PARENT_TRAIN_S3_KEY"] = assignment.parent_train_s3_key
            if req.research_direction:
                env["AUTORESEARCH_RESEARCH_DIRECTION"] = req.research_direction
            if agent_key:
                env["AUTORESEARCH_AGENT_S3_KEY"] = agent_key

            init_spec = InitContainerSpec(
                image="ghcr.io/sprit3dan/sotaathome:latest",
                env=env,
            )
            task = GitHubResearchItem(
                github_repo="karpathy/autoresearch",
                research_direction=research_direction,
                init_container_spec=init_spec,
                job_count=1,
            )

            redis_client.rpush(QUEUE_NAME, task.model_dump_json())
            redis_client.hset(
                f"task:{task.id}",
                mapping={
                    "status": "queued",
                    "repo_ref": task.repo_ref,
                    "research_direction": task.research_direction,
                    "logs": "",
                    "pod_name": "",
                    "generation_id": gen_id,
                },
            )
            task_ids.append(task.id)

        logger.info(
            f"Submitted generation {gen_id} (gen {req.generation_num}/{req.generations}, "
            f"pods={expected_pods}, dataset={req.dataset_hf_repo})"
        )
        return {
            "status": "success",
            "generation_id": gen_id,
            "task_ids": task_ids,
            "generation_num": req.generation_num,
            "total_generations": req.generations,
        }
    except Exception as e:
        logger.exception(f"Failed to submit job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/generation/{gen_id}/train/{run_id}")
def get_train_script(gen_id: str, run_id: str):
    """Return the final train.py for a completed run as plain text."""
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )
        key = f"generations/{gen_id}/{run_id}/train.py"
        try:
            obj = s3.get_object(Bucket="runs", Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise HTTPException(status_code=404, detail=f"Not found: {key}")
            raise
        return Response(content=obj["Body"].read(), media_type="text/plain")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
