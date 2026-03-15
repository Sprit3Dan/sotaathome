import uuid
import time
import logging
from kubernetes import client, config
from models import ResearchItem, InitContainerSpec
from settings import settings

TRAINING_IMAGE = "ghcr.io/sprit3dan/sotaathome:latest"
IMAGE_PULL_SECRET_NAME = "ghcr-secret"
CACHE_VOLUME_NAME = "cache"
OUTPUT_VOLUME_NAME = "output"
CACHE_MOUNT_PATH = "/artifacts/cache"
OUTPUT_MOUNT_PATH = "/artifacts/output"
CACHE_HOST_PATH = "/root/hackathon/cache"
OUTPUT_HOST_PATH = "/root/hackathon/output"

MINIO_SECRET_NAME = "minio-credentials"

logger = logging.getLogger(__name__)


def get_k8s_client():
    """
    Initializes the Kubernetes client. Attempts to load in-cluster config first,
    falling back to local kubeconfig for local development.
    """
    try:
        config.load_incluster_config()
        logger.debug("Loaded in-cluster Kubernetes config.")
    except config.config_exception.ConfigException:
        kubeconfig_path = settings.KUBECONFIG_PATH
        if kubeconfig_path:
            logger.debug(f"Loading Kubernetes config from KUBECONFIG_PATH: {kubeconfig_path}")
            config.load_kube_config(config_file=kubeconfig_path)
        else:
            logger.debug("Loading default local Kubernetes config (~/.kube/config).")
            config.load_kube_config()
    return client.BatchV1Api(), client.CoreV1Api()


def list_jobs() -> list[dict]:
    batch_api, _ = get_k8s_client()
    jobs = batch_api.list_namespaced_job(settings.K8S_NAMESPACE).items
    return [
        {
            "name": job.metadata.name,
            "namespace": job.metadata.namespace,
            "succeeded": job.status.succeeded or 0,
            "failed": job.status.failed or 0,
            "active": job.status.active or 0,
        }
        for job in jobs
    ]


def list_nodes() -> list[dict]:
    _, core_api = get_k8s_client()
    nodes = core_api.list_node().items
    return [
        {
            "name": node.metadata.name,
            "labels": node.metadata.labels or {},
            "capacity": dict(node.status.capacity or {}),
            "allocatable": dict(node.status.allocatable or {}),
            "runtime_handlers": [
                handler.name for handler in (node.status.runtime_handlers or [])
            ],
        }
        for node in nodes
    ]


def deploy_research_job(task: ResearchItem, init_spec: InitContainerSpec) -> dict:
    """
    Deploys a Kubernetes Pod with the generated init container and a persistent volume
    to run the research task.
    """
    batch_api, core_api = get_k8s_client()
    namespace = settings.K8S_NAMESPACE

    # Generate a unique ID for this task run
    run_id = f"research-{uuid.uuid4().hex[:8]}"

    env_vars = [
        client.V1EnvVar(name="CUDA_VISIBLE_DEVICES", value="0"),
        client.V1EnvVar(name="AUTORESEARCH_RUN_ID", value=run_id),
        client.V1EnvVar(name="AUTORESEARCH_CACHE_DIR", value=CACHE_MOUNT_PATH),
        client.V1EnvVar(name="AUTORESEARCH_OUTPUT_DIR", value=OUTPUT_MOUNT_PATH),
        client.V1EnvVar(name="AUTORESEARCH_NUM_SHARDS", value="2"),
        client.V1EnvVar(name="DEPTH", value="4"),
        client.V1EnvVar(name="DEVICE_BATCH_SIZE", value="8"),
        client.V1EnvVar(name="S3_ENDPOINT_URL", value=settings.S3_ENDPOINT_URL),
        client.V1EnvVar(
            name="S3_ACCESS_KEY",
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(
                    name=MINIO_SECRET_NAME,
                    key="MINIO_ACCESS_KEY",
                )
            ),
        ),
        client.V1EnvVar(
            name="S3_SECRET_KEY",
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(
                    name=MINIO_SECRET_NAME,
                    key="MINIO_SECRET_KEY",
                )
            ),
        ),
        client.V1EnvVar(
            name="OPENAI_API_KEY",
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(
                    name="orchestrator-secrets",
                    key="OPENAI_API_KEY",
                )
            ),
        ),
        client.V1EnvVar(name="OPENAI_MODEL", value=settings.OPENAI_MODEL),
    ]
    for k, v in init_spec.env.items():
        env_vars.append(client.V1EnvVar(name=k, value=str(v)))

    main_container = client.V1Container(
        name="train",
        image=TRAINING_IMAGE,
        env=env_vars,
        volume_mounts=[
            client.V1VolumeMount(
                name=CACHE_VOLUME_NAME,
                mount_path=CACHE_MOUNT_PATH,
            ),
            client.V1VolumeMount(
                name=OUTPUT_VOLUME_NAME,
                mount_path=OUTPUT_MOUNT_PATH,
            ),
        ],
    )

    # 5. Define the Job
    pod_name = f"pod-{run_id}"
    job_name = f"job-{run_id}"
    job_count = task.job_count
    pod_template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(
            labels={
                "app": "autoresearch",
                "run_id": run_id,
                "repo_ref": task.repo_ref.replace("/", "-")
            }
        ),
        spec=client.V1PodSpec(
            node_name="turtle",
            runtime_class_name="nvidia",
            restart_policy="Never",
            image_pull_secrets=[
                client.V1LocalObjectReference(name=IMAGE_PULL_SECRET_NAME)
            ],
            volumes=[
                client.V1Volume(
                    name=CACHE_VOLUME_NAME,
                    host_path=client.V1HostPathVolumeSource(
                        path=CACHE_HOST_PATH,
                        type="DirectoryOrCreate",
                    )
                ),
                client.V1Volume(
                    name=OUTPUT_VOLUME_NAME,
                    host_path=client.V1HostPathVolumeSource(
                        path=OUTPUT_HOST_PATH,
                        type="DirectoryOrCreate",
                    )
                ),
            ],
            containers=[main_container]
        )
    )
    job = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            labels={
                "app": "autoresearch",
                "run_id": run_id,
                "repo_ref": task.repo_ref.replace("/", "-")
            }
        ),
        spec=client.V1JobSpec(
            ttl_seconds_after_finished=3600,
            backoff_limit=0,
            completions=job_count,
            parallelism=job_count,
            template=pod_template
        )
    )

    logger.info(f"Creating Job: {job_name}...")
    batch_api.create_namespaced_job(namespace=namespace, body=job)

    logger.info(f"Monitoring job {job_name}...")
    status = "running"
    logs = ""

    try:
        while True:
            current_job = batch_api.read_namespaced_job(name=job_name, namespace=namespace)
            if current_job.status.succeeded:
                logger.info(f"Job {job_name} completed successfully.")
                status = "success"
                break
            if current_job.status.failed:
                logger.error(f"Job {job_name} failed. Fetching logs...")
                status = "failed"
                pods = core_api.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=f"job-name={job_name}"
                )
                if pods.items:
                    pod_name = pods.items[0].metadata.name
                    try:
                        main_logs = core_api.read_namespaced_pod_log(
                            name=pod_name, namespace=namespace, container="train"
                        )
                        logs += f"--- Main Container Logs ---\n{main_logs}\n"
                    except Exception as e:
                        logs += f"Could not fetch main logs: {e}\n"
                break

            pods = core_api.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}"
            )
            if pods.items:
                pod_name = pods.items[0].metadata.name

            time.sleep(settings.POLL_INTERVAL_SECONDS)

    except Exception as e:
        status = "error"
        logs = str(e)
        logger.exception(f"Error while monitoring job {job_name}: {e}")

    if status == "success":
        logger.info(f"Cleaning up successful job {job_name} and pod {pod_name}...")
        try:
            batch_api.delete_namespaced_job(
                name=job_name,
                namespace=namespace,
                propagation_policy="Background"
            )
        except Exception:
            pass
    else:
        logger.warning(f"Preserving failed job {job_name} and pod {pod_name} for inspection.")

    return {"status": status, "logs": logs, "pod_name": pod_name, "job_name": job_name}
