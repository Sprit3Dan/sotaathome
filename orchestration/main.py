import time
import requests
import logging
from models import parse_research_item
from agent import generate_init_container_spec
from k8s_deployer import deploy_research_job
from settings import settings

logger = logging.getLogger(__name__)

def update_task_status(queue_url: str, task_id: str, status: str, logs: str = None, pod_name: str = None):
    try:
        payload = {"task_id": task_id, "status": status}
        if logs:
            payload["logs"] = logs
        if pod_name:
            payload["pod_name"] = pod_name
        requests.post(f"{queue_url}/update_status", json=payload)
    except Exception as e:
        logger.error(f"Failed to report status for task {task_id}: {e}")

def main():
    queue_url = settings.QUEUE_URL
    logger.info("Agent started, waiting for research tasks...")
    while True:
        try:
            resp = requests.get(f"{queue_url}/dequeue")
            data = resp.json()
            if data.get("status") == "success" and data.get("task"):
                task_data = data["task"]
                logger.info(f"Adopted research direction: {task_data}")
                
                try:
                    # Parse the item from the queue
                    task_item = parse_research_item(task_data)
                        
                    max_attempts = settings.MAX_RETRIES
                    previous_errors = None
                    
                    for attempt in range(1, max_attempts + 1):
                        if task_item.init_container_spec:
                            logger.info(f"Attempt {attempt}/{max_attempts}: Using provided InitContainerSpec, skipping LLM analysis.")
                            spec = task_item.init_container_spec
                        else:
                            logger.info(f"Attempt {attempt}/{max_attempts}: Analyzing repository {task_item.repo_ref} and generating spec...")
                            spec = generate_init_container_spec(task_item, previous_errors=previous_errors)
                        
                        logger.debug(f"\n--- Generated InitContainerSpec ---\n{spec.model_dump_json(indent=2)}")
                        
                        logger.info("Deploying to Kubernetes...")
                        update_task_status(queue_url, task_item.id, "deploying")
                        result = deploy_research_job(task_item, spec)
                        logger.info(f"Pod execution finished with status: {result['status']}")
                        
                        update_task_status(
                            queue_url, 
                            task_item.id, 
                            result['status'], 
                            result.get('logs'), 
                            result.get('pod_name')
                        )
                        
                        if result['status'] == 'success':
                            logger.info("--- Task Complete ---")
                            break
                        else:
                            logger.error(f"Pod failed with logs:\n{result['logs']}")
                            previous_errors = result['logs']
                            if attempt == max_attempts:
                                logger.error("Max attempts reached. Task failed permanently.")
                                update_task_status(queue_url, task_item.id, "failed_permanently", result.get('logs'))
                except Exception as process_err:
                    logger.exception(f"Failed to process research task: {process_err}")
                    if 'task_item' in locals():
                        update_task_status(queue_url, task_item.id, "error", str(process_err))
            else:
                time.sleep(settings.POLL_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Error checking queue: {e}")
            time.sleep(settings.POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()