import pulumi
from pulumi_kubernetes.apps.v1 import Deployment
from pulumi_kubernetes.core.v1 import Service

def create_redis():
    labels = {"app": "redis"}

    Deployment("redis-deploy",
        spec={
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [{"name": "redis", "image": "redis:7-alpine", "ports": [{"containerPort": 6379}]}]
                }
            }
        })

    return Service("redis-svc",
        metadata={"name": "redis"},
        spec={"ports": [{"port": 6379}], "selector": labels})

def create_minio():
    labels = {"app": "minio"}

    Deployment("minio-deploy",
        spec={
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [{
                        "name": "minio",
                        "image": "minio/minio:latest",
                        "command": ["minio", "server", "/data", "--console-address", ":9001"],
                        "env": [
                            {"name": "MINIO_ROOT_USER", "value": "minioadmin"},
                            {"name": "MINIO_ROOT_PASSWORD", "value": "minioadmin"}
                        ],
                        "ports": [{"containerPort": 9000}, {"containerPort": 9001}]
                    }]
                }
            }
        })

    return Service("minio-svc",
        metadata={"name": "minio"},
        spec={
            "ports": [
                {"port": 9000, "targetPort": 9000, "name": "api"},
                {"port": 9001, "targetPort": 9001, "name": "console"}
            ],
            "selector": labels
        })

def create_queue_api():
    labels = {"app": "queue-api"}

    Deployment("queue-api-deploy",
        spec={
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [{
                        "name": "queue-api",
                        "image": "autoresearch-agent:latest",
                        "command": ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"],
                        "imagePullPolicy": "IfNotPresent",
                        "env": [{"name": "REDIS_HOST", "value": "redis"}],
                        "ports": [{"containerPort": 8000}]
                    }]
                }
            }
        })

    return Service("queue-api-svc",
        metadata={"name": "queue-api"},
        spec={
            "ports": [{"port": 8000}],
            "selector": labels
        })

def create_agent():
    labels = {"app": "agent"}

    Deployment("agent-deploy",
        spec={
            "replicas": 5,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [{
                        "name": "agent",
                        "image": "autoresearch-agent:latest",
                        "imagePullPolicy": "IfNotPresent",
                        "env": [
                            {"name": "QUEUE_URL", "value": "http://queue-api:8000"},
                            {"name": "MINIO_ENDPOINT", "value": "minio:9000"},
                            {"name": "MINIO_ACCESS_KEY", "value": "minioadmin"},
                            {"name": "MINIO_SECRET_KEY", "value": "minioadmin"}
                        ]
                    }]
                }
            }
        })

redis_service = create_redis()
minio_service = create_minio()
queue_api_service = create_queue_api()
create_agent()
