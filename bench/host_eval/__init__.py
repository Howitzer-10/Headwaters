"""Host-model eval harness (HDW-208, v3.4 B2): tool selection, argument validity, relay."""

from bench.host_eval.models import (
    Backend,
    Dispatcher,
    HostProfile,
    Task,
    ToolCall,
    ToolSpec,
    load_tasks,
)
from bench.host_eval.report import build_report, render_markdown, write_reports
from bench.host_eval.runner import run_matrix, run_profile, summarize

__all__ = [
    "Backend",
    "Dispatcher",
    "HostProfile",
    "Task",
    "ToolCall",
    "ToolSpec",
    "build_report",
    "load_tasks",
    "render_markdown",
    "run_matrix",
    "run_profile",
    "summarize",
    "write_reports",
]
