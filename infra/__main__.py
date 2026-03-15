import pulumi
from pulumi_kubernetes.apps.v1 import Deployment
from pulumi_kubernetes.core.v1 import Secret, Service, ServiceAccount
from pulumi_kubernetes.rbac.v1 import ClusterRole, ClusterRoleBinding, Role, RoleBinding

config = pulumi.Config()
minio_user = config.get("minioUser") or "minioadmin"
minio_password = config.get_secret("minioPassword") or "minioadmin"
openai_model = config.get("openaiModel") or "gpt-4o-2024-08-06"
queue_name = config.get("queueName") or "training_queue"
k8s_namespace = config.get("k8sNamespace") or "default"
max_retries = config.get("maxRetries") or "3"
poll_interval = config.get("pollInterval") or "5"
log_level = config.get("logLevel") or "DEBUG"
openai_api_key = config.get_secret("openaiApiKey")
github_token = config.get_secret("githubToken")
hf_token = config.get_secret("hfToken")

# Standalone secret so both MinIO and the orchestrator can depend on it explicitly.
minio_secret = Secret(
    "minio-secret",
    metadata={"name": "minio-credentials"},
    string_data={
        "MINIO_ACCESS_KEY": minio_user,
        "MINIO_SECRET_KEY": minio_password,
    },
)

orchestrator_secret = Secret(
    "orchestrator-secret",
    metadata={"name": "orchestrator-secrets"},
    string_data={
        "OPENAI_API_KEY": openai_api_key or "",
        "GITHUB_TOKEN": github_token or "",
        "HF_TOKEN": hf_token or "",
    },
)


orchestrator_service_account = ServiceAccount(
    "orchestrator-sa",
    metadata={"name": "orchestrator-sa"},
)

orchestrator_role = Role(
    "orchestrator-role",
    metadata={"name": "orchestrator-role", "namespace": k8s_namespace},
    rules=[
        {
            "apiGroups": [""],
            "resources": ["pods"],
            "verbs": ["get", "list"],
        },
        {
            "apiGroups": ["batch"],
            "resources": ["jobs"],
            "verbs": ["get", "list", "create", "delete"],
        },
    ],
)

orchestrator_role_binding = RoleBinding(
    "orchestrator-rolebinding",
    metadata={"name": "orchestrator-rolebinding", "namespace": k8s_namespace},
    role_ref={
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": "orchestrator-role",
    },
    subjects=[
        {
            "kind": "ServiceAccount",
            "name": "orchestrator-sa",
            "namespace": k8s_namespace,
        }
    ],
)

orchestrator_cluster_role = ClusterRole(
    "orchestrator-cluster-role",
    metadata={"name": "orchestrator-cluster-role"},
    rules=[
        {
            "apiGroups": [""],
            "resources": ["nodes"],
            "verbs": ["get", "list"],
        }
    ],
)

orchestrator_cluster_role_binding = ClusterRoleBinding(
    "orchestrator-cluster-rolebinding",
    metadata={"name": "orchestrator-cluster-rolebinding"},
    role_ref={
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "orchestrator-cluster-role",
    },
    subjects=[
        {
            "kind": "ServiceAccount",
            "name": "orchestrator-sa",
            "namespace": k8s_namespace,
        }
    ],
)


def create_redis():
    labels = {"app": "redis"}

    Deployment(
        "redis-deploy",
        spec={
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "nodeName": "smashing-kittiwake",
                    "containers": [
                        {
                            "name": "redis",
                            "image": "redis:7-alpine",
                            "ports": [{"containerPort": 6379}],
                        }
                    ]
                },
            },
        },
    )

    return Service(
        "redis-svc",
        metadata={"name": "redis"},
        spec={"ports": [{"port": 6379}], "selector": labels},
    )


def create_minio():
    labels = {"app": "minio"}

    Deployment(
        "minio-deploy",
        opts=pulumi.ResourceOptions(depends_on=[minio_secret]),
        spec={
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "nodeName": "smashing-kittiwake",
                    "volumes": [
                        {
                            "name": "data",
                            "emptyDir": {},
                        }
                    ],
                    "containers": [
                        {
                            "name": "minio",
                            "image": "minio/minio:latest",
                            "command": [
                                "minio",
                                "server",
                                "/data",
                                "--console-address",
                                ":9001",
                            ],
                            "env": [
                                {
                                    "name": "MINIO_ROOT_USER",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "minio-credentials",
                                            "key": "MINIO_ACCESS_KEY",
                                        }
                                    },
                                },
                                {
                                    "name": "MINIO_ROOT_PASSWORD",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "minio-credentials",
                                            "key": "MINIO_SECRET_KEY",
                                        }
                                    },
                                },
                            ],
                            "ports": [{"containerPort": 9000}, {"containerPort": 9001}],
                            "volumeMounts": [{"name": "data", "mountPath": "/data"}],
                            "readinessProbe": {
                                "httpGet": {
                                    "path": "/minio/health/ready",
                                    "port": 9000,
                                },
                                "initialDelaySeconds": 10,
                                "periodSeconds": 10,
                            },
                            "livenessProbe": {
                                "httpGet": {"path": "/minio/health/live", "port": 9000},
                                "initialDelaySeconds": 30,
                                "periodSeconds": 20,
                            },
                        }
                    ],
                },
            },
        },
    )

    return Service(
        "minio-svc",
        metadata={"name": "minio"},
        spec={
            "ports": [
                {"port": 9000, "targetPort": 9000, "name": "api"},
                {"port": 9001, "targetPort": 9001, "name": "console"},
            ],
            "selector": labels,
        },
    )


def create_orchestrator():
    labels = {"app": "orchestrator"}

    Deployment(
        "orchestrator-deploy",
        opts=pulumi.ResourceOptions(
            depends_on=[
                minio_secret,
                orchestrator_secret,
                orchestrator_service_account,
                orchestrator_role,
                orchestrator_role_binding,
                orchestrator_cluster_role,
                orchestrator_cluster_role_binding,
            ]
        ),
        spec={
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "serviceAccountName": "orchestrator-sa",
                    "nodeName": "smashing-kittiwake",
                    "imagePullSecrets": [{"name": "ghcr-secret"}],
                    "containers": [
                        {
                            "name": "orchestrator",
                            "image": "ghcr.io/sprit3dan/sotaathome-orchestrator",
                            "imagePullPolicy": "Always",
                            "env": [
                                {"name": "OPENAI_MODEL", "value": openai_model},
                                {"name": "QUEUE_URL", "value": "http://orchestrator:8000"},
                                {"name": "REDIS_HOST", "value": "redis"},
                                {"name": "REDIS_PORT", "value": "6379"},
                                {"name": "QUEUE_NAME", "value": queue_name},
                                {"name": "K8S_NAMESPACE", "value": k8s_namespace},
                                {"name": "MAX_RETRIES", "value": max_retries},
                                {"name": "POLL_INTERVAL", "value": poll_interval},
                                {"name": "MINIO_ENDPOINT", "value": "minio:9000"},
                                {"name": "LOG_LEVEL", "value": log_level},
                            ],
                            "envFrom": [
                                {"secretRef": {"name": "minio-credentials"}},
                                {"secretRef": {"name": "orchestrator-secrets"}},
                            ],
                            "ports": [{"containerPort": 8000}],
                        }
                    ]
                },
            },
        },
    )

    return Service(
        "orchestrator-svc",
        metadata={"name": "orchestrator"},
        spec={"ports": [{"port": 8000, "targetPort": 8000}], "selector": labels},
    )


redis_service = create_redis()
minio_service = create_minio()
create_orchestrator()
