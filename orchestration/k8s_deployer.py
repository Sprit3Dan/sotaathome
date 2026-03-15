import os
import uuid
from kubernetes import client, config
from models import ResearchItem, InitContainerSpec
from settings import settings
import time
import logging

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
    return client.CoreV1Api()


def deploy_research_job(task: ResearchItem, init_spec: InitContainerSpec) -> dict:
    """
    Deploys a Kubernetes Pod with the generated init container and a persistent volume
    to run the research task.
    """
    api = get_k8s_client()
    namespace = settings.K8S_NAMESPACE

    # Generate a unique ID for this task run
    run_id = f"research-{uuid.uuid4().hex[:8]}"
    pvc_name = f"pvc-{run_id}"

    # 1. Create PersistentVolumeClaim (PVC)
    # This acts as the shared storage between the init container (which installs deps)
    # and the main container (which runs the training loop).
    pvc = client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(name=pvc_name),
        spec=client.V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOnce"],
            resources=client.V1ResourceRequirements(
                requests={"storage": "20Gi"}
            )
        )
    )
    logger.info(f"Creating PVC: {pvc_name} in namespace '{namespace}'...")
    api.create_namespaced_persistent_volume_claim(namespace=namespace, body=pvc)

    # 2. Prepare Volume Mounts for the init container
    init_volume_mounts = []
    for mount in init_spec.volume_mounts:
        init_volume_mounts.append(
            client.V1VolumeMount(
                name="workspace-volume",
                mount_path=mount.mount_path
            )
        )

    # Prepare Environment Variables
    env_vars = []
    for k, v in init_spec.env.items():
        env_vars.append(client.V1EnvVar(name=k, value=str(v)))

    # 3. Define the Init Container
    init_container = client.V1Container(
        name="setup-dependencies",
        image=init_spec.image,
        command=init_spec.command if init_spec.command else None,
        args=init_spec.args if init_spec.args else None,
        env=env_vars,
        volume_mounts=init_volume_mounts
    )

    # 4. Define the Main Training Container
    # For now, this is a placeholder that will be replaced by the actual PyTorch training image.
    # It must mount the exact same volume to access the dependencies installed by the initContainer.
    main_mount_path = init_volume_mounts[0].mount_path if init_volume_mounts else "/workspace"
    main_container = client.V1Container(
        name="training-loop",
        image="pytorch/pytorch:latest", # Can be parameterized later
        command=["/bin/sh", "-c"],
        args=[f"echo 'Running research task: {task.research_direction}'; ls -la {main_mount_path}"],
        volume_mounts=[
            client.V1VolumeMount(
                name="workspace-volume",
                mount_path=main_mount_path
            )
        ],
        # Example of requesting GPU resources for heterogeneous massive execution
        # resources=client.V1ResourceRequirements(
        #     limits={"nvidia.com/gpu": "1"}
        # )
    )

    # 5. Define the Pod
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=f"pod-{run_id}",
            labels={
                "app": "autoresearch",
                "run_id": run_id,
                "repo_ref": task.repo_ref.replace("/", "-")
            }
        ),
        spec=client.V1PodSpec(
            restart_policy="Never",
            volumes=[
                client.V1Volume(
                    name="workspace-volume",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=pvc_name
                    )
                )
            ],
            init_containers=[init_container],
            containers=[main_container]
        )
    )

    pod_name = f"pod-{run_id}"
    logger.info(f"Creating Pod: {pod_name}...")
    api.create_namespaced_pod(namespace=namespace, body=pod)

    logger.info(f"Monitoring pod {pod_name}...")
    status = "running"
    logs = ""

    try:
        while True:
            current_pod = api.read_namespaced_pod(name=pod_name, namespace=namespace)
            phase = current_pod.status.phase

            if phase == "Succeeded":
                logger.info(f"Pod {pod_name} completed successfully.")
                status = "success"
                break
            elif phase == "Failed":
                logger.error(f"Pod {pod_name} failed. Fetching logs...")
                status = "failed"

                try:
                    init_logs = api.read_namespaced_pod_log(
                        name=pod_name, namespace=namespace, container="setup-dependencies"
                    )
                    logs += f"--- Init Container Logs ---\n{init_logs}\n"
                except Exception as e:
                    logs += f"Could not fetch init logs: {e}\n"

                try:
                    main_logs = api.read_namespaced_pod_log(
                        name=pod_name, namespace=namespace, container="training-loop"
                    )
                    logs += f"--- Main Container Logs ---\n{main_logs}\n"
                except Exception as e:
                    logs += f"Could not fetch main logs: {e}\n"

                break

            time.sleep(settings.POLL_INTERVAL_SECONDS)

    except Exception as e:
        status = "error"
        logs = str(e)
        logger.exception(f"Error while monitoring pod {pod_name}: {e}")

    logger.info(f"Cleaning up pod {pod_name} and pvc {pvc_name}...")
    try:
        api.delete_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception:
        pass

    try:
        api.delete_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
    except Exception:
        pass

    return {"status": status, "logs": logs, "pod_name": pod_name}
