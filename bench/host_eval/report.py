"""JSON and Markdown reports for a host-eval run. Identifiers and rates only; no text."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bench.host_eval.runner import Summary

__all__ = ["REPORT_VERSION", "build_report", "render_markdown", "write_reports"]

REPORT_VERSION = 1


def build_report(summaries: Sequence[Summary], *, dispatcher_kind: str = "mock") -> dict[str, Any]:
    """A JSON-ready report. ``dispatcher_kind`` says whether a real model was involved."""
    return {
        "version": REPORT_VERSION,
        "dispatcher_kind": dispatcher_kind,
        "runs": [asdict(s) for s in summaries],
    }


def _pct(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Host eval report",
        "",
        f"Dispatcher kind: {report['dispatcher_kind']}",
        "",
        "| Host | Dispatcher | Tasks | Tool selection | Argument validity | Relay compliance |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for run in report["runs"]:
        lines.append(
            f"| {run['host']} | {run['dispatcher']} | {run['tasks']} | "
            f"{_pct(run['tool_selection'])} | {_pct(run['argument_validity'])} | "
            f"{_pct(run['relay_compliance'])} |"
        )
    failures = [
        (run["host"], run["dispatcher"], r["task_id"], ", ".join(r["problems"]))
        for run in report["runs"]
        for r in run["results"]
        if r["problems"]
    ]
    if failures:
        lines += ["", "## Problems", ""]
        lines += [f"- {h} / {d} / {t}: {p}" for h, d, t, p in failures]
    return "\n".join(lines) + "\n"


def write_reports(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    """Write ``host_eval.json`` and ``host_eval.md`` (UTF-8, LF) into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "host_eval.json"
    md_path = directory / "host_eval.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return json_path, md_path
