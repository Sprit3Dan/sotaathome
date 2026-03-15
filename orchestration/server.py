import json
import logging

import redis
from agent import generate_init_container_spec
from fastapi import FastAPI, HTTPException
from k8s_deployer import deploy_research_job
from models import ResearchItem, TaskStatusUpdate
from settings import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="AutoResearch Queue Manager")


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
