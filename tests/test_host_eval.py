"""HDW-208: the host-eval harness runs against the real in-process MCP server with mock dispatchers.

Accept when: the harness produces a JSON/Markdown report using a mocked dispatcher.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from bench.host_eval import (
    HostProfile,
    ToolCall,
    build_report,
    load_tasks,
    render_markdown,
    run_matrix,
    run_profile,
    write_reports,
)
from bench.host_eval.mocks import ClientBackend, ScriptedDispatcher
from bench.host_eval.models import Task, ToolSpec
from tests.frontdoor_support import DIFF_ANSWER, Rig, make_repo

TASKS = load_tasks()
HOST = HostProfile("host-a", "Host A")
CODE_REPLY = "Here:\n```python\nx = 1\n```\n"


def _run(tmp_path: Path, dispatchers: list[ScriptedDispatcher], *, profiles: Any = None) -> Any:
    repo = make_repo(tmp_path / "proj", {"app.py": "def run():\n    return 1\n"})
    rig = Rig(tmp_path, reply=DIFF_ANSWER)

    async def go() -> Any:
        async with rig.client() as client:
            backend = ClientBackend(client, str(repo))
            return await run_matrix(profiles or [HOST], dispatchers, backend, TASKS)

    return asyncio.run(go())


def test_there_are_ten_starter_tasks_covering_every_front_door_tool() -> None:
    assert len(TASKS) == 10
    assert {t.expected_tool for t in TASKS} == {
        "headwaters_do",
        "headwaters_ask",
        "headwaters_check",
        "headwaters_think",
        "headwaters_status",
        "headwaters_scope",
        "headwaters_handoff",
        "headwaters_more",
    }


def test_a_gold_dispatcher_scores_full_marks_on_every_metric(tmp_path: Path) -> None:
    (summary,) = _run(tmp_path, [ScriptedDispatcher("gold")])
    problems = [(r.task_id, r.problems) for r in summary.results if r.problems]
    assert summary.tool_selection == 1.0
    assert summary.argument_validity == 1.0
    assert summary.relay_compliance == 1.0, problems


def test_a_dispatcher_that_picks_the_wrong_tool_is_scored_down(tmp_path: Path) -> None:
    def wrong(task: Task, call: ToolCall) -> ToolCall | None:
        return ToolCall("headwaters_ask", {"question": "x"}) if task.id == "do-write" else call

    (summary,) = _run(tmp_path, [ScriptedDispatcher("sloppy", mutate=wrong)])
    assert summary.tool_selection == pytest.approx(0.9)
    bad = next(r for r in summary.results if r.task_id == "do-write")
    assert bad.problems == ("wrong_tool",)


def test_missing_and_ill_typed_arguments_are_invalid(tmp_path: Path) -> None:
    def break_args(task: Task, call: ToolCall) -> ToolCall | None:
        if task.id == "do-diff":
            return ToolCall(call.tool, {"task": "x", "paths": "app.py", "want": "diff"})
        if task.id == "ask-explain":
            return ToolCall(call.tool, {"paths": ["app.py"]})
        return call

    (summary,) = _run(tmp_path, [ScriptedDispatcher("weak", mutate=break_args)])
    assert summary.tool_selection == 1.0
    assert summary.argument_validity == pytest.approx(0.8)
    by_id = {r.task_id: r for r in summary.results}
    assert any(p.startswith("schema:") for p in by_id["do-diff"].problems)
    assert "missing:question" in by_id["ask-explain"].problems


def test_no_call_counts_as_a_missed_selection(tmp_path: Path) -> None:
    (summary,) = _run(tmp_path, [ScriptedDispatcher("mute", mutate=lambda t, c: None)])
    assert summary.tool_selection == 0.0
    assert summary.argument_validity is None  # nothing to score
    assert summary.relay_compliance is None


def test_a_host_that_summarizes_code_breaks_relay_compliance(tmp_path: Path) -> None:
    lazy = ScriptedDispatcher("lazy", relay_fn=lambda result: "summarize")
    (summary,) = _run(tmp_path, [lazy])
    assert summary.relay_compliance is not None
    assert summary.relay_compliance < 1.0
    assert any(r.relay_ok is False for r in summary.results)


def test_the_report_is_written_as_json_and_markdown(tmp_path: Path) -> None:
    gold = ScriptedDispatcher("gold")
    lazy = ScriptedDispatcher("lazy", relay_fn=lambda result: "summarize")
    summaries = _run(tmp_path, [gold, lazy], profiles=[HOST, HostProfile("host-b", "Host B", True)])
    assert [(s.host, s.dispatcher) for s in summaries] == [
        ("host-a", "gold"),
        ("host-a", "lazy"),
        ("host-b", "gold"),
        ("host-b", "lazy"),
    ]
    report = build_report(summaries)
    json_path, md_path = write_reports(report, tmp_path / "out")
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["dispatcher_kind"] == "mock"
    assert len(loaded["runs"]) == 4
    assert loaded["runs"][0]["tool_selection"] == 1.0
    markdown = md_path.read_text(encoding="utf-8")
    assert markdown == render_markdown(loaded)
    assert "| host-a | gold | 10 | 100.0% | 100.0% | 100.0% |" in markdown
    assert "## Problems" in markdown  # the lazy dispatcher left relay problems
    assert "\r" not in markdown


def test_a_report_never_contains_prompt_or_answer_text(tmp_path: Path) -> None:
    summaries = _run(tmp_path, [ScriptedDispatcher("gold")])
    blob = json.dumps(build_report(summaries))
    assert all(t.prompt not in blob for t in TASKS)
    assert "Plain answer" not in blob


def test_a_profile_that_asked_for_more_sees_the_advanced_tools(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def go() -> tuple[int, int]:
        async with rig.client() as client:
            backend = ClientBackend(client)
            plain = await backend.list_tools(HostProfile("p", "P"))
            more = await backend.list_tools(HostProfile("q", "Q", True))
            return len(plain), len(more)

    assert asyncio.run(go()) == (8, 16)


def test_run_profile_works_against_a_stub_backend() -> None:
    class Stub:
        async def list_tools(self, profile: HostProfile) -> list[ToolSpec]:
            return [ToolSpec("headwaters_status", "s", {"type": "object"})]

        async def call(self, profile: HostProfile, call: ToolCall) -> dict[str, Any]:
            return {"ok": True}  # no relay field: not scored

    only = [t for t in TASKS if t.id == "status-why"]
    summary = asyncio.run(run_profile(HOST, ScriptedDispatcher("gold"), Stub(), only))
    assert summary.tool_selection == 1.0
    assert summary.argument_validity == 1.0
    assert summary.relay_compliance is None
