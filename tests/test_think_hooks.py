"""HDW-140 (D12): router hooks for ``headwaters_think``.

``task_hint`` forces the task, ``reasoning`` reaches ``ExecOptions`` and filters to
reasoning-capable models (``ModelRow.reasoning_ok``), and reasoning text never leaves the router.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from headwaters.core.executor.types import Delta
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Candidate
from headwaters.core.state_decisions import recent_decisions
from tests.router_rig import Rig
from tests.router_support import Ledger, candidate, registry_with

THINKER = "qwen3-think"
PLAIN = "llama-plain"
CLOUD = "openrouter/deep:free"
SECRET = "SECRET-CHAIN-OF-THOUGHT"
THINKING_ANSWER = [
    Delta(reasoning=SECRET),
    Delta(content="the final answer"),
    Delta(finish_reason="stop"),
]


def pool() -> list[Candidate]:
    return [
        # The non-thinking local model is the better reasoner on paper: only the flag excludes it.
        candidate(PLAIN, "local", 90.0, reasoning=95.0, reasoning_ok=False),
        candidate(THINKER, "local", 90.0, reasoning=88.0, reasoning_ok=True),
        candidate(CLOUD, "free_cloud", 90.0, reasoning=99.0, reasoning_ok=True),
    ]


def think_request(**kw: Any) -> dict[str, Any]:
    return {"scope": "local", "task_hint": "reasoning", "reasoning": True, **kw}


def test_local_think_request_picks_a_thinking_model_with_think_on(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {THINKER: THINKING_ANSWER, PLAIN: THINKING_ANSWER}, pool())
    result = rig.run(**think_request())
    assert result.decision.model == THINKER
    assert result.decision.tier == "local"
    assert rig.cloud_clients == 0
    sent = rig.dispatch.backends[THINKER].requests[0]
    assert sent.options.reasoning is True  # the Ollama converter turns this into think=true
    assert rig.analyzer.reqs.soft.task == "code"  # the analyzer said code; the hint overrode it
    assert result.decision.breakdown.quality == 88.0  # scored on the reasoning task


def test_reasoning_text_is_absent_from_result_rows_and_events(tmp_path: Path) -> None:
    ledger = Ledger()
    rig = Rig(tmp_path, {THINKER: THINKING_ANSWER}, pool(), registry=registry_with(ledger=ledger))
    result = rig.run(**think_request())
    assert result.result.content == "the final answer"
    assert result.result.reasoning == ""
    assert SECRET not in repr(result)
    assert SECRET not in repr(recent_decisions(rig.db))
    assert SECRET not in repr(ledger.events)
    with rig.db.read() as c:
        rows = [
            tuple(r) for t in ("decisions", "outcomes") for r in c.execute(f"SELECT * FROM {t}")
        ]
    assert rows
    assert SECRET not in repr(rows)


def test_stream_drops_reasoning_events_and_the_final_result_text(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {THINKER: THINKING_ANSWER}, pool())
    events = rig.stream(**think_request())
    inner = [e.event for e in events if e.kind == "stream" and e.event is not None]
    assert "reasoning" not in [e.kind for e in inner]
    assert any(e.kind == "content" for e in inner)
    final = events[-1]
    assert final.kind == "final"
    assert final.result is not None
    assert final.result.result.reasoning == ""
    assert SECRET not in repr(events)


def test_reasoning_never_leaves_even_when_not_requested(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {PLAIN: THINKING_ANSWER}, pool())  # a model that thinks by default
    result = rig.run(scope="local", force=PLAIN)
    assert result.result.reasoning == ""


def test_local_scope_without_a_thinking_model_fails_closed(tmp_path: Path) -> None:
    only_plain = [pool()[0], pool()[2]]
    rig = Rig(tmp_path, {PLAIN: THINKING_ANSWER}, only_plain)
    with pytest.raises(NoRouteError):
        rig.run(**think_request())
    assert rig.cloud_clients == 0
    assert rig.dispatch.calls == []  # no cloud fallback, and the non-thinking model was not tried


def test_free_scope_picks_the_free_reasoning_model(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {CLOUD: THINKING_ANSWER}, pool())
    result = rig.run(**think_request(scope="free"))
    assert result.decision.model == CLOUD


def test_hint_without_reasoning_does_not_filter_on_the_flag(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {PLAIN: THINKING_ANSWER}, pool())
    result = rig.run(scope="local", task_hint="reasoning")  # hint only: PLAIN scores best
    assert result.decision.model == PLAIN
    assert rig.dispatch.backends[PLAIN].requests[0].options.reasoning is None
