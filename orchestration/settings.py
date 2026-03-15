import logging
import os


class Settings:
    """
    Centralized configuration for the orchestration and agent components.
    """

    # OpenAI Settings
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-2024-08-06")

    # Queue & Redis Settings
    QUEUE_URL = os.getenv("QUEUE_URL", "http://localhost:8000")
    REDIS_HOST = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    QUEUE_NAME = os.getenv("QUEUE_NAME", "training_queue")

    # Kubernetes Settings
    K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "default")
    KUBECONFIG_PATH = os.getenv("KUBECONFIG")

    # Worker & Orchestration Settings
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "5"))

    # API Tokens for repository access
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    HF_TOKEN = os.getenv("HF_TOKEN")
    S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
    S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
    S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
    REPO_MOUNT_PATH = os.getenv("REPO_MOUNT_PATH", "/repo")
    REPO_PATH_ENV_VAR = os.getenv("REPO_PATH_ENV_VAR", "REPO_PATH")

    # Logging Settings
    LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")


settings = Settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.DEBUG),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
