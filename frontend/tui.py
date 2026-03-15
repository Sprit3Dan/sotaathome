import os
import time
from contextlib import contextmanager
from typing import Any

from dataclasses import dataclass

import requests
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


QUEUE_URL = os.getenv("QUEUE_URL", "http://127.0.0.1:8000")
REFRESH_SECONDS = float(os.getenv("TUI_REFRESH_SECONDS", "2"))
MAX_ROWS = int(os.getenv("TUI_MAX_ROWS", "12"))
DEFAULT_DATASET_REPO = os.getenv("TUI_DATASET_HF_REPO", "HuggingFaceFW/fineweb")
DEFAULT_TEXT_COLUMN = os.getenv("TUI_DATASET_TEXT_COLUMN", "text")
DEFAULT_TRAIN_SPLIT = os.getenv("TUI_DATASET_TRAIN_SPLIT", "train")
DEFAULT_VAL_SPLIT = os.getenv("TUI_DATASET_VAL_SPLIT", "validation")
CRAB_FRAMES = [
    "🦀      🦀🦀      🦀",
    "  🦀  🦀✨🦀  🦀  ",
    "🦀✨   CRAB CTRL   ✨🦀",
    "  🦀  🦀🔥🦀  🦀  ",
]
FOOTER_FRAMES = [
    "🦀 queue crab rave 🦀 jobs crab rave 🦀 node crab rave 🦀",
    "✨🦀 deploy crabs thriving across the cluster 🦀✨",
    "🦀 status: maximum crab energy detected 🦀",
]

console = Console()


@dataclass
class SubmitFormState:
    generation_num: int = 1
    generations: int = 1
    n: int = 1
    m: int = 1
    t: int = 300
    dataset_hf_repo: str = DEFAULT_DATASET_REPO
    dataset_text_column: str = DEFAULT_TEXT_COLUMN
    dataset_train_split: str = DEFAULT_TRAIN_SPLIT
    dataset_val_split: str = DEFAULT_VAL_SPLIT
    research_direction: str = ""
    last_result: str = ""


def fetch_json(path: str) -> dict[str, Any]:
    url = f"{QUEUE_URL.rstrip('/')}{path}"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        return {"status": "error", "detail": str(exc), "path": path}


def post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{QUEUE_URL.rstrip('/')}{path}"
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        return {"status": "error", "detail": str(exc), "path": path}


def build_submit_payload(form: SubmitFormState) -> dict[str, Any]:
    return {
        "generation_num": form.generation_num,
        "generations": form.generations,
        "n": form.n,
        "m": form.m,
        "t": form.t,
        "dataset_hf_repo": form.dataset_hf_repo,
        "dataset_text_column": form.dataset_text_column,
        "dataset_train_split": form.dataset_train_split,
        "dataset_val_split": form.dataset_val_split,
        "research_direction": form.research_direction,
    }


def prompt_int(label: str, current: int) -> int:
    value = console.input(f"{label} [{current}]: ").strip()
    if not value:
        return current
    return int(value)


def prompt_text(label: str, current: str) -> str:
    value = console.input(f"{label} [{current}]: ").strip()
    return value or current


@contextmanager
def paused_live(live: Live):
    live.stop()
    try:
        yield
    finally:
        live.start(refresh=True)


def edit_form(form: SubmitFormState, live: Live) -> None:
    with paused_live(live):
        console.print("[bold cyan]Edit job submission fields[/bold cyan]")
        console.print("Press Enter to keep the current value.")
        try:
            form.generation_num = prompt_int("generation_num", form.generation_num)
            form.generations = prompt_int("generations", form.generations)
            form.n = prompt_int("n", form.n)
            form.m = prompt_int("m", form.m)
            form.t = prompt_int("t", form.t)
            form.dataset_hf_repo = prompt_text("dataset_hf_repo", form.dataset_hf_repo)
            form.dataset_text_column = prompt_text("dataset_text_column", form.dataset_text_column)
            form.dataset_train_split = prompt_text("dataset_train_split", form.dataset_train_split)
            form.dataset_val_split = prompt_text("dataset_val_split", form.dataset_val_split)
            form.research_direction = prompt_text("research_direction", form.research_direction or "(optional)")
            if form.research_direction == "(optional)":
                form.research_direction = ""
            form.last_result = "form updated"
        except ValueError as exc:
            form.last_result = f"invalid input: {exc}"


def crab_status(label: str, value: int, good: str = "🦀 thriving", bad: str = "🦀💀 trouble") -> str:
    mood = good if value == 0 else bad
    return f"[bold]{label}:[/bold] {value}  {mood}"


def build_banner(tick: int) -> Panel:
    frame = CRAB_FRAMES[tick % len(CRAB_FRAMES)]
    title = Text("🦀 CLUSTER CRAB CONTROL CENTER 🦀", style="bold magenta")
    body = Text(frame, style="bold cyan", justify="center")
    return Panel(body, title=title, border_style="bright_magenta")


def build_summary(data: dict[str, Any]) -> Panel:
    queue_length = data.get("queue_length", 0)
    jobs = data.get("jobs", [])
    nodes = data.get("nodes", [])
    tasks = data.get("tasks", [])

    active_jobs = sum(job.get("active", 0) for job in jobs)
    failed_jobs = sum(job.get("failed", 0) for job in jobs)
    succeeded_jobs = sum(job.get("succeeded", 0) for job in jobs)

    lines = [
        crab_status("Queue", queue_length, "🦀 humming", "🦀✨ packed"),
        f"[bold]Tasks tracked:[/bold] {len(tasks)}  🦀",
        f"[bold]Jobs:[/bold] {len(jobs)}  🦀🦀",
        f"[bold]Nodes:[/bold] {len(nodes)}  🦀🖥️",
        crab_status("Active jobs", active_jobs, "🦀 chill", "🦀⚙️ busy"),
        crab_status("Succeeded jobs", succeeded_jobs, "🦀 awaiting glory", "🦀🏆 winning"),
        crab_status("Failed jobs", failed_jobs, "🦀 immaculate", "🦀🔥 burning"),
    ]
    return Panel(Group(*lines), title="🦀 Overview", border_style="cyan")


def build_tasks_table(tasks: list[dict[str, Any]]) -> Table:
    table = Table(expand=True)
    table.add_column("Task ID", style="cyan", no_wrap=True)
    table.add_column("Status", style="magenta", no_wrap=True)
    table.add_column("Repo", style="green")
    table.add_column("Direction", style="white")
    table.add_column("Pod", style="yellow")

    for task in tasks[:MAX_ROWS]:
        status = str(task.get("status", ""))
        if "success" in status:
            status = f"🦀🏆 {status}"
        elif "fail" in status or "error" in status:
            status = f"🦀💀 {status}"
        elif status:
            status = f"🦀 {status}"

        table.add_row(
            str(task.get("task_id", "")),
            status,
            str(task.get("repo_ref", "")),
            str(task.get("research_direction", "")),
            str(task.get("pod_name", "")),
        )
    return table


def build_jobs_table(jobs: list[dict[str, Any]]) -> Table:
    table = Table(expand=True)
    table.add_column("Job", style="cyan")
    table.add_column("Namespace", style="green")
    table.add_column("Active", justify="right")
    table.add_column("Succeeded", justify="right")
    table.add_column("Failed", justify="right")

    for job in jobs[:MAX_ROWS]:
        failed = int(job.get("failed", 0))
        active = int(job.get("active", 0))
        vibe = "🦀🔥" if failed else "🦀⚙️" if active else "🦀"
        table.add_row(
            f"{vibe} {str(job.get('name', ''))}",
            str(job.get("namespace", "")),
            str(active),
            str(job.get("succeeded", 0)),
            str(failed),
        )
    return table


def build_nodes_table(nodes: list[dict[str, Any]]) -> Table:
    table = Table(expand=True)
    table.add_column("Node", style="cyan")
    table.add_column("GPU Runtime", style="green")
    table.add_column("Labels", style="white")

    for node in nodes[:MAX_ROWS]:
        labels = node.get("labels", {}) or {}
        allocatable = node.get("allocatable", {}) or {}
        runtime_handlers = node.get("runtime_handlers", []) or []
        gpu_count = allocatable.get("nvidia.com/gpu", "0")
        has_gpu = str(gpu_count) not in ("0", "", "None")
        has_nvidia_runtime = "nvidia" in runtime_handlers

        if has_gpu and has_nvidia_runtime:
            gpu_runtime = f"🦀 yes ({gpu_count} GPU, nvidia)"
        elif has_gpu:
            gpu_runtime = f"🦀 yes ({gpu_count} GPU)"
        elif has_nvidia_runtime:
            gpu_runtime = "🦀 runtime only"
        else:
            gpu_runtime = "🦀 no"

        label_text = ", ".join(sorted(list(labels.keys())[:6]))
        table.add_row(
            f"🦀 {str(node.get('name', ''))}",
            gpu_runtime,
            label_text,
        )
    return table


def build_error_panel(data: dict[str, Any]) -> Panel:
    message = data.get("detail") or "Unknown error"
    path = data.get("path", "")
    hint = data.get("hint", "")
    body = f"[bold red]Failed to load dashboard data[/bold red]\n{path}\n{message}"
    if hint:
        body = f"{body}\n\n[bold]Hint:[/bold] {hint}"
    return Panel(
        body,
        title="Error",
        border_style="red",
    )


def build_submit_panel(form: SubmitFormState) -> Panel:
    direction = form.research_direction or "(optional)"
    body = "\n".join(
        [
            "[bold]Submit:[/bold] [cyan]e[/cyan]=edit [green]s[/green]=schedule [yellow]q[/yellow]=quit",
            f"generation_num={form.generation_num} generations={form.generations}",
            f"n={form.n} m={form.m} t={form.t}",
            f"dataset={form.dataset_hf_repo}",
            f"text={form.dataset_text_column} train={form.dataset_train_split} val={form.dataset_val_split}",
            f"direction={direction}",
            f"last_result={form.last_result or 'none'}",
        ]
    )
    return Panel(body, title="🦀 Schedule Job", border_style="bright_green")


def build_footer(tick: int) -> Panel:
    frame = FOOTER_FRAMES[tick % len(FOOTER_FRAMES)]
    return Panel(frame, title="🦀 Hype Feed", border_style="bright_blue")


def build_layout(data: dict[str, Any], tick: int, form: SubmitFormState):
    if data.get("status") != "success":
        return build_error_panel(data)

    layout = Layout()
    layout.split_column(
        Layout(name="banner", size=3),
        Layout(name="top", size=9),
        Layout(name="middle", ratio=2),
        Layout(name="bottom", ratio=2),
        Layout(name="submit", size=9),
        Layout(name="footer", size=3),
    )
    layout["middle"].split_row(
        Layout(name="tasks", ratio=2),
        Layout(name="jobs", ratio=1),
    )

    layout["banner"].update(build_banner(tick))
    layout["top"].update(build_summary(data))
    layout["tasks"].update(Panel(build_tasks_table(data.get("tasks", [])), title="🦀 Tasks"))
    layout["jobs"].update(Panel(build_jobs_table(data.get("jobs", [])), title="🦀 Jobs"))
    layout["bottom"].update(Panel(build_nodes_table(data.get("nodes", [])), title="🦀 Nodes"))
    layout["submit"].update(build_submit_panel(form))
    layout["footer"].update(build_footer(tick))

    return layout


def main():
    tick = 0
    form = SubmitFormState()
    startup = fetch_json("/cluster_status")
    if startup.get("status") != "success":
        startup["hint"] = (
            "Set QUEUE_URL to the reachable orchestrator API, "
            "for example QUEUE_URL=http://127.0.0.1:8000 when port-forwarding "
            "or QUEUE_URL=http://orchestrator:8000 inside the cluster."
        )

    data = startup
    with Live(build_layout(data, tick, form), console=console, refresh_per_second=4, auto_refresh=False) as live:
        while True:
            tick += 1
            data = fetch_json("/cluster_status")
            if data.get("status") != "success":
                data["hint"] = (
                    "Confirm the orchestrator API is reachable and /cluster_status is served."
                )
            live.update(build_layout(data, tick, form), refresh=True)

            with paused_live(live):
                command = console.input("[bold cyan]Command[/bold cyan] ([green]e[/green]/[green]s[/green]/[yellow]q[/yellow], Enter=refresh): ").strip().lower()

            if command == "q":
                break
            if command == "e":
                edit_form(form, live)
                live.update(build_layout(data, tick, form), refresh=True)
                continue
            if command == "s":
                with paused_live(live):
                    result = post_json("/submit", build_submit_payload(form))
                if result.get("status") == "success":
                    form.last_result = f"queued generation={result.get('generation_id', '')}"
                else:
                    form.last_result = f"error: {result.get('detail', 'submit failed')}"
                live.update(build_layout(data, tick, form), refresh=True)
                continue

            time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
