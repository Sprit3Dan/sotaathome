import pulumi
from pulumi_kubernetes.apps.v1 import Deployment
from pulumi_kubernetes.core.v1 import PersistentVolumeClaim, Secret, Service

config = pulumi.Config()
minio_user = config.get("minioUser") or "minioadmin"
minio_password = config.get_secret("minioPassword") or "minioadmin"

# Standalone secret so both MinIO and the orchestrator can depend on it explicitly.
minio_secret = Secret(
    "minio-secret",
    metadata={"name": "minio-credentials"},
    string_data={
        "MINIO_ACCESS_KEY": minio_user,
        "MINIO_SECRET_KEY": minio_password,
    },
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

    pvc = PersistentVolumeClaim(
        "minio-pvc",
        metadata={"name": "minio-data"},
        spec={
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "20Gi"}},
        },
    )

    Deployment(
        "minio-deploy",
        opts=pulumi.ResourceOptions(depends_on=[minio_secret, pvc]),
        spec={
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "volumes": [
                        {
                            "name": "data",
                            "persistentVolumeClaim": {"claimName": "minio-data"},
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
        opts=pulumi.ResourceOptions(depends_on=[minio_secret]),
        spec={
            "replicas": 2,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [
                        {
                            "name": "orchestrator",
                            "image": "autoresearch-orchestrator:latest",
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
                                {"name": "REDIS_HOST", "value": "redis"},
                                {"name": "MINIO_ENDPOINT", "value": "minio:9000"},
                            ],
                            "envFrom": [{"secretRef": {"name": "minio-credentials"}}],
                        }
                    ]
                },
            },
        },
    )


redis_service = create_redis()
minio_service = create_minio()
create_orchestrator()
