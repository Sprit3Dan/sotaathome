import uuid
from typing import Optional
from pydantic import BaseModel, Field

class ResearchItem(BaseModel):
    """
    Represents a single asynchronous, collaborative research task.
    """
    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique identifier for the research task"
    )
    github_repo: str = Field(
        ..., 
        description="The target GitHub repository reference (e.g., 'karpathy/autoresearch')"
    )
    research_direction: str = Field(
        ..., 
        description="The specific hypothesis, hyperparameter sweep, or training task to execute"
    )
    base_branch: str = Field(
        default="main", 
        description="The base branch to branch off from for this research direction"
    )
    discussion_or_pr_ref: Optional[int] = Field(
        default=None, 
        description="An optional GitHub Discussion or PR number that serves as context for this task"
    )
    commit_sha: Optional[str] = Field(
        default=None,
        description="An optional specific commit SHA to run the research against"
    )

class TaskStatusUpdate(BaseModel):
    """
    Represents an update to the status of a scheduled research task.
    """
    task_id: str = Field(..., description="The ID of the task")
    status: str = Field(..., description="The new status (e.g., 'processing', 'success', 'failed')")
    logs: Optional[str] = Field(default=None, description="Optional logs or error messages")
    pod_name: Optional[str] = Field(default=None, description="The name of the Kubernetes pod if scheduled")

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