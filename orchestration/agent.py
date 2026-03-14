import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from openai import OpenAI
from models import ResearchItem, InitContainerSpec
from settings import settings

import json
import logging

logger = logging.getLogger(__name__)

class RepoExplorer:
    def __init__(self, repo_url: str, branch: str = "main", commit_sha: str = None):
        self.temp_dir = tempfile.mkdtemp(prefix="autoresearch_")
        self.repo_path = Path(self.temp_dir)
        self.clone_repo(repo_url, branch, commit_sha)

    def clone_repo(self, repo_url: str, branch: str, commit_sha: str):
        clone_cmd = ["git", "clone", "--branch", branch]
        if not commit_sha:
            clone_cmd.extend(["--depth", "1"])
        clone_cmd.extend([f"https://github.com/{repo_url}.git", self.temp_dir])

        logger.debug(f"Cloning command: {' '.join(clone_cmd)}")
        logger.info(f"Cloning {repo_url} into {self.temp_dir}...")
        subprocess.run(clone_cmd, check=True, capture_output=True, text=True)

        if commit_sha:
            logger.info(f"Checking out commit {commit_sha}...")
            subprocess.run(["git", "checkout", commit_sha], cwd=self.temp_dir, check=True, capture_output=True, text=True)

    def list_files(self, directory: str = ".") -> str:
        """List files in a specific directory relative to the repo root."""
        target_dir = (self.repo_path / directory).resolve()
        if not str(target_dir).startswith(str(self.repo_path)):
            logger.warning(f"Attempted to access outside of repository: {target_dir}")
            return "Error: Cannot access outside of repository"

        try:
            items = os.listdir(target_dir)
            logger.debug(f"Listed {len(items)} items in {directory}")
            return "\n".join(items)
        except Exception as e:
            logger.error(f"Error listing directory {directory}: {e}")
            return f"Error listing directory: {e}"

    def read_file(self, file_path: str) -> str:
        """Read the contents of a specific file."""
        target_file = (self.repo_path / file_path).resolve()
        if not str(target_file).startswith(str(self.repo_path)):
            logger.warning(f"Attempted to read outside of repository: {target_file}")
            return "Error: Cannot access outside of repository"

        try:
            content = target_file.read_text(encoding="utf-8")
            logger.debug(f"Read file {file_path} ({len(content)} characters)")
            if len(content) > 10000:
                logger.debug(f"Truncating file {file_path} (exceeds 10000 characters)")
                content = content[:10000] + "\n...[TRUNCATED]..."
            return content
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return f"Error reading file: {e}"

    def cleanup(self):
        logger.debug(f"Cleaning up temporary directory {self.temp_dir}")
        shutil.rmtree(self.temp_dir, ignore_errors=True)

def generate_init_container_spec(research_item: ResearchItem, previous_errors: str = None) -> InitContainerSpec:
    """
    Analyzes the repository context and generates an InitContainerSpec using OpenAI.
    """
    explorer = RepoExplorer(
        repo_url=research_item.github_repo,
        branch=research_item.base_branch,
        commit_sha=research_item.commit_sha
    )

    try:
        # Assumes OPENAI_API_KEY is set in the environment
        client = OpenAI()

        error_context = ""
        if previous_errors:
            logger.warning(f"Including previous errors in prompt.")
            logger.debug(f"Previous errors: {previous_errors}")
            error_context = f"\n\nWARNING: A previous attempt failed with the following errors. You MUST fix your spec to address these:\n{previous_errors}\n"

        prompt = f"""
        Research Task Direction: {research_item.research_direction}
        GitHub Repository: {research_item.github_repo}{error_context}

        Determine the best initContainer spec. Explore the repository using the provided tools to understand the dependencies required.
        The initContainer's job is ONLY to install necessary dependencies for the model repository into a shared volume, so they are ready for the main training loop.

        Provide the exact container image (e.g., 'python:3.11-slim', or 'ubuntu:22.04'),
        the command array, args array, and any necessary environment variables as a dictionary.

        Crucially, you MUST configure `volume_mounts` to mount a PersistentVolumeClaim (PVC) into the container (e.g., at '/workspace' or '/app/env').
        This PVC ensures that the dependencies you install are persisted and successfully shared with the actual main training pod.
        """

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files in a specific directory relative to the repository root. Defaults to root '.'",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string"}
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a specific file relative to the repository root.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string"}
                        },
                        "required": ["file_path"]
                    }
                }
            }
        ]

        messages = [
            {"role": "system", "content": "You are an expert MLOps engineer and Kubernetes architect."},
            {"role": "user", "content": prompt}
        ]

        logger.info("Asking OpenAI to generate InitContainerSpec, allowing tool calls...")
        logger.debug(f"Using model: {settings.OPENAI_MODEL}")

        while True:
            logger.debug("Sending request to OpenAI...")
            response = client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=messages,
                tools=tools,
                response_format=InitContainerSpec,
            )

            message = response.choices[0].message
            if message.tool_calls:
                messages.append(message)
                for tool_call in message.tool_calls:
                    if tool_call.function.name == "list_files":
                        args = json.loads(tool_call.function.arguments)
                        logger.info(f"LLM called list_files with args: {args}")
                        result = explorer.list_files(args.get("directory", "."))
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                    elif tool_call.function.name == "read_file":
                        args = json.loads(tool_call.function.arguments)
                        logger.info(f"LLM called read_file with args: {args}")
                        result = explorer.read_file(args.get("file_path"))
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
            else:
                logger.info("LLM successfully generated InitContainerSpec")
                logger.debug(f"Generated spec: {message.parsed.model_dump_json(indent=2)}")
                return message.parsed

    finally:
        explorer.cleanup()
