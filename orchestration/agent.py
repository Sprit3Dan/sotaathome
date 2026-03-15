import httpx
from openai import OpenAI
from models import ResearchItem, GitHubResearchItem, HuggingFaceResearchItem, InitContainerSpec
from settings import settings

import json
import logging

logger = logging.getLogger(__name__)


class GitHubRepoExplorer:
    def __init__(self, repo: str, branch: str = "main", commit_sha: str = None, token: str = None):
        self.repo = repo
        self.ref = commit_sha or branch
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def list_files(self, directory: str = ".") -> str:
        """List files in a specific directory relative to the repository root."""
        path = "" if directory in (".", "") else directory.strip("/")
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        try:
            resp = httpx.get(url, headers=self.headers, params={"ref": self.ref})
            resp.raise_for_status()
            items = resp.json()
            logger.debug(f"Listed {len(items)} items in '{directory}'")
            return "\n".join(f"{item['type']}: {item['name']}" for item in items)
        except Exception as e:
            logger.error(f"Error listing directory '{directory}': {e}")
            return f"Error listing directory: {e}"

    def read_file(self, file_path: str) -> str:
        """Fetch the contents of a specific file via the raw GitHub URL."""
        path = file_path.strip("/")
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.ref}/{path}"
        try:
            auth = {"Authorization": self.headers["Authorization"]} if "Authorization" in self.headers else {}
            resp = httpx.get(url, headers=auth)
            resp.raise_for_status()
            content = resp.text
            logger.debug(f"Read file '{file_path}' ({len(content)} characters)")
            if len(content) > 10000:
                logger.debug(f"Truncating file '{file_path}' (exceeds 10000 characters)")
                content = content[:10000] + "\n...[TRUNCATED]..."
            return content
        except Exception as e:
            logger.error(f"Error reading file '{file_path}': {e}")
            return f"Error reading file: {e}"


class HuggingFaceRepoExplorer:
    def __init__(self, repo_id: str, repo_type: str = "model", revision: str = "main", token: str = None):
        self.repo_id = repo_id
        self.repo_type = repo_type  # "model", "dataset", or "space"
        self.revision = revision
        self.headers = {}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def list_files(self, directory: str = ".") -> str:
        """List files in a directory via the HuggingFace Hub tree API."""
        path = "" if directory in (".", "") else directory.strip("/")
        url = f"https://huggingface.co/api/{self.repo_type}s/{self.repo_id}/tree/{self.revision}"
        if path:
            url += f"/{path}"
        try:
            resp = httpx.get(url, headers=self.headers)
            resp.raise_for_status()
            items = resp.json()
            logger.debug(f"Listed {len(items)} items in '{directory}'")
            return "\n".join(f"{item['type']}: {item['path']}" for item in items)
        except Exception as e:
            logger.error(f"Error listing directory '{directory}': {e}")
            return f"Error listing directory: {e}"

    def read_file(self, file_path: str) -> str:
        """Fetch the raw contents of a file from HuggingFace."""
        path = file_path.strip("/")
        url = f"https://huggingface.co/{self.repo_id}/resolve/{self.revision}/{path}"
        try:
            resp = httpx.get(url, headers=self.headers, follow_redirects=True)
            resp.raise_for_status()
            content = resp.text
            logger.debug(f"Read file '{file_path}' ({len(content)} characters)")
            if len(content) > 10000:
                logger.debug(f"Truncating file '{file_path}' (exceeds 10000 characters)")
                content = content[:10000] + "\n...[TRUNCATED]..."
            return content
        except Exception as e:
            logger.error(f"Error reading file '{file_path}': {e}")
            return f"Error reading file: {e}"


def _create_explorer(research_item):
    if isinstance(research_item, GitHubResearchItem):
        return GitHubRepoExplorer(
            repo=research_item.github_repo,
            branch=research_item.base_branch,
            commit_sha=research_item.commit_sha,
            token=settings.GITHUB_TOKEN,
        )
    elif isinstance(research_item, HuggingFaceResearchItem):
        return HuggingFaceRepoExplorer(
            repo_id=research_item.hf_repo,
            repo_type=research_item.hf_repo_type,
            revision=research_item.revision,
            token=settings.HF_TOKEN,
        )
    raise ValueError(f"Unsupported research item type: {type(research_item)}")


def generate_init_container_spec(research_item: ResearchItem, previous_errors: str = None) -> InitContainerSpec:
    """
    Analyzes the repository context and generates an InitContainerSpec using OpenAI.
    """
    explorer = _create_explorer(research_item)

    client = OpenAI()

    error_context = ""
    if previous_errors:
        logger.warning("Including previous errors in prompt.")
        logger.debug(f"Previous errors: {previous_errors}")
        error_context = f"\n\nWARNING: A previous attempt failed with the following errors. You MUST fix your spec to address these:\n{previous_errors}\n"

    prompt = f"""
    Research Task Direction: {research_item.research_direction}
    Repository ({research_item.repo_type}): {research_item.repo_ref}{error_context}

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
                "description": "Fetch the contents of a specific file relative to the repository root.",
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
        if message.refusal:
            raise RuntimeError(f"Model refused to generate spec: {message.refusal}")
        if message.tool_calls:
            messages.append(message)
            for tool_call in message.tool_calls:
                args = json.loads(tool_call.function.arguments)
                if tool_call.function.name == "list_files":
                    logger.info(f"LLM called list_files with args: {args}")
                    result = explorer.list_files(args.get("directory", "."))
                elif tool_call.function.name == "read_file":
                    logger.info(f"LLM called read_file with args: {args}")
                    file_path = args.get("file_path")
                    if not file_path:
                        result = "Error: file_path is required"
                    else:
                        result = explorer.read_file(file_path)
                else:
                    logger.warning(f"LLM called unknown tool: {tool_call.function.name}")
                    result = f"Error: unknown tool '{tool_call.function.name}'"
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
        else:
            logger.info("LLM successfully generated InitContainerSpec")
            logger.debug(f"Generated spec: {message.parsed.model_dump_json(indent=2)}")
            return message.parsed
