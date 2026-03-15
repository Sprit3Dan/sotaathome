import os
from threading import Thread
from typing import Any

import requests
from rich.table import Table
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Footer, Header, Static


QUEUE_URL = os.getenv("QUEUE_URL", "http://127.0.0.1:8000")
REFRESH_SECONDS = float(os.getenv("TUI_REFRESH_SECONDS", "1"))
MAX_ROWS = int(os.getenv("TUI_MAX_ROWS", "12"))


def fetch_json(path: str) -> dict[str, Any]:
    url = f"{QUEUE_URL.rstrip('/')}{path}"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        return {"status": "error", "detail": str(exc), "path": path}


def crab_status(label: str, value: int, good: str = "🦀 thriving", bad: str = "🦀💀 trouble") -> str:
    mood = good if value == 0 else bad
    return f"[bold]{label}:[/bold] {value} {mood}"


def build_summary_renderable(data: dict[str, Any]):
    queue_length = data.get("queue_length", 0)
    jobs = data.get("jobs", [])
    nodes = data.get("nodes", [])
    tasks = data.get("tasks", [])

    active_jobs = sum(job.get("active", 0) for job in jobs)
    failed_jobs = sum(job.get("failed", 0) for job in jobs)
    succeeded_jobs = sum(job.get("succeeded", 0) for job in jobs)

    row1 = "  ".join(
        [
            crab_status("Queue", queue_length, "🦀 humming", "🦀✨ packed"),
            f"[bold]Tasks:[/bold] {len(tasks)} 🦀",
            f"[bold]Jobs:[/bold] {len(jobs)} 🦀🦀",
            f"[bold]Nodes:[/bold] {len(nodes)} 🦀🖥️",
        ]
    )
    row2 = "  ".join(
        [
            crab_status("Active", active_jobs, "🦀 chill", "🦀⚙️ busy"),
            crab_status("Succeeded", succeeded_jobs, "🦀 awaiting glory", "🦀🏆 winning"),
            crab_status("Failed", failed_jobs, "🦀 immaculate", "🦀🔥 burning"),
        ]
    )
    return f"{row1}\n{row2}"


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


def build_generations_table(generations: list[dict[str, Any]]) -> Table:
    table = Table(expand=True)
    table.add_column("Gen ID", style="cyan", no_wrap=True)
    table.add_column("Num/Total", style="green", no_wrap=True)
    table.add_column("Status", style="magenta", no_wrap=True)
    table.add_column("Pods", style="yellow", no_wrap=True, justify="right")
    table.add_column("Best BPB", style="white", no_wrap=True, justify="right")
    table.add_column("Best Run", style="dim cyan", no_wrap=True)

    for gen in generations[:MAX_ROWS]:
        status = str(gen.get("status", ""))
        if status in ("done", "evaluated", "next_gen_submitted"):
            status_fmt = f"🦀🏆 {status}"
        elif "fail" in status or "error" in status:
            status_fmt = f"🦀💀 {status}"
        else:
            status_fmt = f"🦀 {status}"

        bpb = gen.get("best_val_bpb")
        best_run = gen.get("best_run_id") or "—"
        table.add_row(
            str(gen.get("gen_id", ""))[:8],
            f"{gen.get('generation_num', '?')}/{gen.get('total_generations', '?')}",
            status_fmt,
            f"{gen.get('pods_done', '0')}/{gen.get('expected_pods', '?')}",
            f"{bpb:.4f}" if bpb is not None else "—",
            best_run[:8] if best_run != "—" else "—",
        )
    return table


class SectionView(Static):
    DEFAULT_CSS = """
    SectionView {
        border: round $accent;
        padding: 0 1;
        height: 1fr;
    }
    """

    def __init__(self, title: str, section_id: str) -> None:
        super().__init__("", id=section_id)
        self.section_title = title
        self.section_id = section_id

    def update_content(self, renderable) -> None:
        self.border_title = self.section_title
        self.update(renderable)


class DashboardApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #overview {
        height: 4;
        border: round cyan;
        padding: 0 1;
    }

    #main-grid {
        height: 1fr;
    }

    #left-column, #right-column {
        width: 1fr;
        height: 1fr;
    }

    .hidden {
        display: none;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_now", "Refresh"),
        ("t", "maximize_tasks", "Tasks"),
        ("j", "maximize_jobs", "Jobs"),
        ("g", "maximize_generations", "Generations"),
        ("n", "maximize_nodes", "Nodes"),
        ("o", "maximize_overview", "Overview"),
        ("escape", "restore", "Restore"),
        ("0", "restore", "Restore"),
    ]

    maximized = reactive("")

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, Any] = {}
        self.refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="overview")
        with Horizontal(id="main-grid"):
            with Vertical(id="left-column"):
                yield SectionView("🦀 Tasks", "tasks")
                yield SectionView("🦀 Generations", "generations")
            with Vertical(id="right-column"):
                yield SectionView("🦀 Jobs", "jobs")
                yield SectionView("🦀 Nodes", "nodes")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "🦀 Cluster Crab Control Center"
        self.sub_title = "t/j/g/n maximize • o overview • esc restore • r refresh"
        self.refresh_in_flight = False
        self.refresh_timer = self.set_interval(REFRESH_SECONDS, self.refresh_dashboard)
        self.refresh_dashboard()

    def refresh_dashboard(self) -> None:
        if self.refresh_in_flight:
            return
        self.refresh_in_flight = True
        Thread(target=self._refresh_in_background, daemon=True).start()

    def _refresh_in_background(self) -> None:
        data = fetch_json("/cluster_status")
        self.call_from_thread(self._apply_refreshed_data, data)

    def _apply_refreshed_data(self, data: dict[str, Any]) -> None:
        self.refresh_in_flight = False
        self.data = data
        self.render_dashboard()

    def render_dashboard(self) -> None:
        overview = self.query_one("#overview", Static)
        tasks = self.query_one("#tasks", SectionView)
        jobs = self.query_one("#jobs", SectionView)
        generations = self.query_one("#generations", SectionView)
        nodes = self.query_one("#nodes", SectionView)

        if self.data.get("status") != "success":
            message = self.data.get("detail") or "Unknown error"
            path = self.data.get("path", "")
            overview.update(f"[bold red]Failed to load dashboard data[/bold red]\n{path}\n{message}")
            tasks.update_content("waiting for cluster data")
            jobs.update_content("waiting for cluster data")
            generations.update_content("waiting for cluster data")
            nodes.update_content("waiting for cluster data")
            return

        overview.border_title = "🦀 Overview"
        overview.update(build_summary_renderable(self.data))
        tasks.update_content(build_tasks_table(self.data.get("tasks", [])))
        jobs.update_content(build_jobs_table(self.data.get("jobs", [])))
        generations.update_content(build_generations_table(self.data.get("generations", [])))
        nodes.update_content(build_nodes_table(self.data.get("nodes", [])))
        self.apply_focus_layout()

    def apply_focus_layout(self) -> None:
        overview = self.query_one("#overview", Static)
        main_grid = self.query_one("#main-grid", Horizontal)
        left_column = self.query_one("#left-column", Vertical)
        right_column = self.query_one("#right-column", Vertical)
        sections = {
            "tasks": self.query_one("#tasks", SectionView),
            "jobs": self.query_one("#jobs", SectionView),
            "generations": self.query_one("#generations", SectionView),
            "nodes": self.query_one("#nodes", SectionView),
        }

        overview.styles.height = 4
        main_grid.remove_class("hidden")
        left_column.remove_class("hidden")
        right_column.remove_class("hidden")

        if self.maximized == "overview":
            overview.remove_class("hidden")
            overview.styles.height = "1fr"
            main_grid.add_class("hidden")
            for section in sections.values():
                section.add_class("hidden")
            return

        overview.remove_class("hidden")

        if self.maximized:
            for name, section in sections.items():
                if name == self.maximized:
                    section.remove_class("hidden")
                    section.styles.height = "1fr"
                else:
                    section.add_class("hidden")

            if self.maximized in ("tasks", "generations"):
                right_column.add_class("hidden")
            if self.maximized in ("jobs", "nodes"):
                left_column.add_class("hidden")
            return

        for section in sections.values():
            section.remove_class("hidden")
            section.styles.height = "1fr"

    def action_refresh_now(self) -> None:
        self.refresh_dashboard()

    def action_maximize_tasks(self) -> None:
        self.maximized = "tasks"
        self.apply_focus_layout()

    def action_maximize_jobs(self) -> None:
        self.maximized = "jobs"
        self.apply_focus_layout()

    def action_maximize_generations(self) -> None:
        self.maximized = "generations"
        self.apply_focus_layout()

    def action_maximize_nodes(self) -> None:
        self.maximized = "nodes"
        self.apply_focus_layout()

    def action_maximize_overview(self) -> None:
        self.maximized = "overview"
        self.apply_focus_layout()

    def action_restore(self) -> None:
        self.maximized = ""
        self.apply_focus_layout()


if __name__ == "__main__":
    DashboardApp().run()