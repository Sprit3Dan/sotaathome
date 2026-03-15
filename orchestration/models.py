import uuid
from typing import Optional, Union, Annotated, Literal
from urllib.parse import urlparse
from pydantic import BaseModel, Field, TypeAdapter


class VolumeMountSpec(BaseModel):
    """
    Specification for mounting a PersistentVolumeClaim (PVC) into the container.
    """
    name: str = Field(
        ...,
        description="The name of the volume to mount"
    )
    mount_path: str = Field(
        ...,
        description="The path within the container at which the volume should be mounted"
    )


class InitContainerSpec(BaseModel):
    """
    Specification for the init container that focuses only on installing
    necessary dependencies for the model repository.
    """
    image: str = Field(
        ...,
        description="The container image to use for the init container (e.g., python:3.11-slim, ubuntu:22.04)"
    )
    command: list[str] = Field(
        default_factory=list,
        description="The command array to run inside the init container"
    )
    args: list[str] = Field(
        default_factory=list,
        description="The arguments to pass to the command"
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables required for initialization"
    )
    volume_mounts: list[VolumeMountSpec] = Field(
        default_factory=list,
        description="Volumes to mount into the init container (e.g., for persisting dependencies to be used by the main pod)"
    )


class ResearchItemBase(BaseModel):
    """
    Common fields shared across all research task types.
    """
    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique identifier for the research task"
    )
    research_direction: str = Field(
        ...,
        description="The specific hypothesis, hyperparameter sweep, or training task to execute"
    )
    discussion_or_pr_ref: Optional[int] = Field(
        default=None,
        description="An optional GitHub Discussion or PR number that serves as context for this task"
    )
    init_container_spec: Optional[InitContainerSpec] = Field(
        default=None,
        description="A pre-built init container spec. When provided, LLM-based analysis is skipped entirely."
    )
    job_count: int = Field(
        default=1,
        ge=1,
        description="Number of Kubernetes jobs or parallel task runs to start for this research item."
    )

    @property
    def repo_ref(self) -> str:
        raise NotImplementedError

    @property
    def repo_mount_name(self) -> str:
        return "repo-volume"

    @property
    def repo_mount_path(self) -> str:
        return "/repo"

    @property
    def repo_path_env_var(self) -> str:
        return "REPO_PATH"


class GitHubResearchItem(ResearchItemBase):
    """
    Research task targeting a GitHub repository.
    """
    repo_type: Literal["github"] = "github"
    github_repo: str = Field(
        ...,
        description="The target GitHub repository. Can be 'owner/repo' or a full URL."
    )
    base_branch: str = Field(
        default="main",
        description="The base branch to branch off from for this research direction"
    )
    commit_sha: Optional[str] = Field(
        default=None,
        description="An optional specific commit SHA to run the research against"
    )

    @property
    def repo_ref(self) -> str:
        return self.github_repo

    @property
    def github_repo_slug(self) -> str:
        value = self.github_repo.strip()
        if value.startswith("http://") or value.startswith("https://"):
            parsed = urlparse(value)
            path = parsed.path.strip("/")
            if path.endswith(".git"):
                path = path[:-4]
            return path
        return value


class HuggingFaceResearchItem(ResearchItemBase):
    """
    Research task targeting a HuggingFace repository (model, dataset, or space).
    """
    repo_type: Literal["huggingface"]
    hf_repo: str = Field(
        ...,
        description="The HuggingFace repository ID in 'namespace/repo-name' format (e.g., 'mistralai/Mistral-7B-v0.1')."
    )
    hf_repo_type: Literal["model", "dataset", "space"] = Field(
        default="model",
        description="The type of HuggingFace repository."
    )
    revision: str = Field(
        default="main",
        description="The branch, tag, or commit revision to use."
    )

    @property
    def repo_ref(self) -> str:
        return self.hf_repo

    @property
    def hf_snapshot_path(self) -> str:
        return f"{self.hf_repo_type}s/{self.hf_repo}@{self.revision}"


ResearchItem = Annotated[
    Union[GitHubResearchItem, HuggingFaceResearchItem],
    Field(discriminator="repo_type")
]

_research_item_adapter = TypeAdapter(ResearchItem)


def parse_research_item(data: dict) -> Union[GitHubResearchItem, HuggingFaceResearchItem]:
    return _research_item_adapter.validate_python(data)


class AutoresearchJobRequest(BaseModel):
    dataset_hf_repo: str = "roneneldan/TinyStories"
    dataset_text_column: str = "text"
    dataset_train_split: str = "train"
    dataset_val_split: str = "validation"
    research_direction: Optional[str] = None
    n: int = Field(default=1, ge=1)
    m: int = Field(default=10, ge=1)
    t: int = Field(default=300, ge=30)
    generations: int = Field(default=1, ge=1)
    agent_script: Optional[str] = None
    # Populated internally on re-submission:
    generation_num: int = 1
    parent_candidate_ids: list[str] = []
    parent_metric_values: list[float] = []


class TaskStatusUpdate(BaseModel):
    """
    Represents an update to the status of a scheduled research task.
    """
    task_id: str = Field(..., description="The ID of the task")
    status: str = Field(..., description="The new status (e.g., 'processing', 'success', 'failed')")
    logs: Optional[str] = Field(default=None, description="Optional logs or error messages")
    pod_name: Optional[str] = Field(default=None, description="The name of the Kubernetes pod if scheduled")
