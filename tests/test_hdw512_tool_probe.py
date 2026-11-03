"""HDW-512 slice A: tool capability is measured, not trusted.

``/api/show`` advertises ``tools`` for ``qwen2.5-coder:14b``, and that model answers a tool call
as a JSON *text* block, which a host then shows to the user. These tests pin the measurement, the
things it refuses to conclude, and the overlay that makes an observation outlive a refresh.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import ExecResult, ToolCall, Usage
from headwaters.core.model_overrides import apply_overrides
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_models import ModelRow, list_models, upsert_model
from headwaters.core.state_probes import CAPABILITY_TOOLS, clear_probes, get_probe, probes
from headwaters.core.tool_probe import (
    DETAIL_CALL_AS_TEXT,
    DETAIL_EMPTY,
    DETAIL_STRUCTURED,
    DETAIL_STRUCTURED_WITH_TEXT,
    DETAIL_TEXT_ANSWER,
    DETAIL_TRUNCATED,
    DETAIL_WRONG_TOOL,
    PROBE_TOOL_NAME,
    classify,
    probe_scope,
    probe_tool_spec,
    verify_models,
)
from tests.cli_support import add_model, gpu_state
from tests.router_support import requirements
from tests.test_candidates import builder, by_id

# What qwen2.5-coder:14b really returned on this Ollama version: the call, as text.
DEFECT_TEXT = '{"name": "headwaters_probe", "arguments": {"reason": "capability check"}}'
CALL = ToolCall(id="call_1", name=PROBE_TOOL_NAME, arguments='{"reason": "check"}')


def result(
    content: str = "",
    *,
    tool_calls: tuple[ToolCall, ...] = (),
    reasoning: str = "",
    finish_reason: str = "stop",
) -> ExecResult:
    return ExecResult(
        model="probed:1b",
        tier="local",
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=Usage(prompt_tokens=20, completion_tokens=8),
        first_token_s=0.05,
    )


class FakeExecutor:
    """The one method a probe uses. ``answers`` is consumed in order, so a run can mix models."""

    def __init__(self, *answers: ExecResult | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, bool, int]] = []

    async def run(
        self,
        decision: Any,
        messages: Any,
        tools: Any,
        *,
        stream: bool = False,
        options: Any = None,
    ) -> ExecResult:
        assert [tool["function"]["name"] for tool in tools] == [PROBE_TOOL_NAME]
        self.calls.append((decision.model, stream, len(messages)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def verify(
    executor: FakeExecutor, db: Database, rules: Rules | None = None, **kw: Any
) -> list[Any]:
    return asyncio.run(verify_models(executor, rules or Rules(), list_models(db), db=db, **kw))


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state.db")


# ------------------------------------------------------------------- what a probe looks like


def test_the_probe_offers_exactly_one_trivial_tool() -> None:
    spec = probe_tool_spec()
    assert spec["function"]["name"] == PROBE_TOOL_NAME
    assert "reason" in str(spec["function"]["parameters"])


@pytest.mark.parametrize(
    ("answer", "ok", "detail"),
    [
        (result(tool_calls=(CALL,)), True, DETAIL_STRUCTURED),
        (result("checking", tool_calls=(CALL,)), True, DETAIL_STRUCTURED_WITH_TEXT),
        (result(tool_calls=(ToolCall("c", "something_else", "{}"),)), False, DETAIL_WRONG_TOOL),
        (result(DEFECT_TEXT), False, DETAIL_CALL_AS_TEXT),
        (result("I cannot call tools."), False, DETAIL_TEXT_ANSWER),
        (result(""), False, DETAIL_EMPTY),
        (result("", reasoning="thinking about it"), False, DETAIL_TEXT_ANSWER),
        (result("", reasoning="half a th", finish_reason="length"), None, DETAIL_TRUNCATED),
        (result("part", finish_reason="length"), None, DETAIL_TRUNCATED),
    ],
)
def test_classify_reads_the_channel_not_the_wording(
    answer: ExecResult, ok: bool | None, detail: str
) -> None:
    assert classify(answer) == (ok, detail)


def test_a_truncated_answer_is_not_recorded_as_a_refusal() -> None:
    """qwen3:8b spends the cap thinking; a cut-off answer says nothing about its tool support."""
    assert classify(result("", reasoning="thinking", finish_reason="length")) == (
        None,
        DETAIL_TRUNCATED,
    )


def test_a_call_delivered_as_text_is_a_failure() -> None:
    """The defect: JSON text in ``content``, nothing in ``tool_calls``."""
    assert classify(result(DEFECT_TEXT)) == (False, DETAIL_CALL_AS_TEXT)
    assert classify(result(f"```json\n{DEFECT_TEXT}\n```")) == (False, DETAIL_CALL_AS_TEXT)


def test_a_probe_never_asks_for_a_paid_upgrade() -> None:
    """The scope is the probed tier plus local; a lock is the executor's business, not ours."""
    rules = Rules.model_validate({"private_lock": True})
    assert probe_scope(rules, "local").tiers == frozenset({"local"})
    assert "paid" not in probe_scope(rules, "free_cloud").tiers


# --------------------------------------------------------------------- one model, one answer


def test_a_text_call_is_recorded_as_not_capable(db: Database) -> None:
    upsert_model(
        db, ModelRow(id="qwen2.5-coder:14b", tier="local", provider="ollama", modalities=["text"])
    )
    executor = FakeExecutor(result(DEFECT_TEXT))
    outcomes = verify(executor, db, now=1_000.0)
    assert [(o.model, o.ok, o.detail) for o in outcomes] == [
        ("qwen2.5-coder:14b", False, DETAIL_CALL_AS_TEXT)
    ]
    stored = get_probe(db, "qwen2.5-coder:14b", CAPABILITY_TOOLS)
    assert stored is not None
    assert stored.ok is False
    assert stored.checked_at == 1_000.0
    assert list_models(db)[0].tools_ok is False  # the column `models list` shows
    assert executor.calls == [("qwen2.5-coder:14b", False, 1)]  # no streaming, one message


def test_a_structured_call_is_recorded_as_capable(db: Database) -> None:
    upsert_model(
        db,
        ModelRow(
            id="qwen3:8b", tier="local", provider="ollama", modalities=["text"], tools_ok=False
        ),
    )
    verify(FakeExecutor(result(tool_calls=(CALL,))), db, now=5.0)
    stored = get_probe(db, "qwen3:8b", CAPABILITY_TOOLS)
    assert stored is not None
    assert stored.ok is True
    assert stored.detail == DETAIL_STRUCTURED
    assert list_models(db)[0].tools_ok is True


# ----------------------------------------------------------- nothing is concluded from a miss


def test_a_failed_probe_records_nothing_and_leaves_the_capability_alone(db: Database) -> None:
    upsert_model(
        db,
        ModelRow(
            id="qwen3:8b", tier="local", provider="ollama", modalities=["text"], tools_ok=True
        ),
    )
    outcomes = verify(FakeExecutor(ProviderError(500, {}, "boom")), db)
    assert outcomes[0].ok is None
    assert outcomes[0].detail == "error:500"
    assert get_probe(db, "qwen3:8b", CAPABILITY_TOOLS) is None
    assert list_models(db)[0].tools_ok is True  # untouched, not "verified"


def test_an_unexpected_crash_is_an_error_not_a_capability(db: Database) -> None:
    upsert_model(db, ModelRow(id="m", tier="free_cloud", provider="vendor", modalities=["text"]))
    outcomes = verify(FakeExecutor(RuntimeError("kaboom")), db)
    assert outcomes[0].ok is None
    assert outcomes[0].detail == "error:RuntimeError"
    assert probes(db) == {}


def test_a_disabled_probe_never_touches_a_model(db: Database) -> None:
    upsert_model(db, ModelRow(id="m", tier="local", provider="ollama", modalities=["text"]))
    rules = Rules.model_validate({"models": {"tool_probe": {"enabled": False}}})
    outcomes = verify(FakeExecutor(), db, rules)
    assert outcomes[0].detail == "skipped:disabled"
    assert probes(db) == {}


# ------------------------------------------------------------------------- bounded, idempotent


def test_a_recent_observation_is_not_repeated_unless_forced(db: Database) -> None:
    upsert_model(db, ModelRow(id="m", tier="local", provider="ollama", modalities=["text"]))
    executor = FakeExecutor(result(tool_calls=(CALL,)), result(DEFECT_TEXT))
    verify(executor, db, now=1_000.0)
    again = verify(executor, db, now=2_000.0)
    assert again[0].detail == "skipped:recent"
    assert again[0].ok is None
    assert len(executor.answers) == 1  # the second answer was never used
    forced = verify(executor, db, now=2_000.0, force=True)
    assert forced[0].ok is False
    assert forced[0].detail == DETAIL_CALL_AS_TEXT
    stored = get_probe(db, "m", CAPABILITY_TOOLS)
    assert stored is not None
    assert stored.ok is False
    assert stored.checked_at == 2_000.0


def test_the_number_of_probes_is_capped(db: Database) -> None:
    for name in ("a", "b", "c"):
        upsert_model(db, ModelRow(id=name, tier="local", provider="ollama", modalities=["text"]))
    executor = FakeExecutor(result(tool_calls=(CALL,)))
    outcomes = verify(executor, db, max_models=1)
    assert [o.detail for o in outcomes] == [DETAIL_STRUCTURED, "skipped:limit", "skipped:limit"]
    assert executor.calls == [("a", False, 1)]


def test_the_configured_cap_applies_when_no_override_is_given(db: Database) -> None:
    for name in ("a", "b"):
        upsert_model(db, ModelRow(id=name, tier="local", provider="ollama", modalities=["text"]))
    rules = Rules.model_validate({"models": {"tool_probe": {"max_models": 1}}})
    outcomes = verify(FakeExecutor(result(tool_calls=(CALL,))), db, rules)
    assert [o.detail for o in outcomes] == [DETAIL_STRUCTURED, "skipped:limit"]


# ------------------------------------------------------------------------ the overlay at routing


def test_an_observation_outlives_a_refresh_that_readvertises_the_capability(db: Database) -> None:
    add_model(db, "qwen2.5-coder:14b", "local", 85.0, tools_ok=True)
    assert by_id(builder(db, gpu_state())(requirements()))["qwen2.5-coder:14b"].row.tools_ok
    verify(FakeExecutor(result(DEFECT_TEXT)), db, now=9.0)
    upsert_model(
        db,
        ModelRow(
            id="qwen2.5-coder:14b",
            tier="local",
            provider="ollama",
            modalities=["text"],
            tools_ok=True,
        ),
    )
    shown = by_id(builder(db, gpu_state())(requirements()))["qwen2.5-coder:14b"]
    assert shown.row.tools_ok is False
    assert list_models(db)[0].tools_ok is True  # the advertisement is intact; the overlay wins
    clear_probes(db, "qwen2.5-coder:14b")
    assert by_id(builder(db, gpu_state())(requirements()))["qwen2.5-coder:14b"].row.tools_ok


def test_a_user_override_still_has_the_last_word_over_a_probe(db: Database) -> None:
    add_model(db, "qwen2.5-coder:14b", "local", 85.0, tools_ok=True)
    verify(FakeExecutor(result(DEFECT_TEXT)), db, now=9.0)
    rules = Rules.model_validate(
        {"models": {"overrides": {"qwen2.5-coder:14b": {"tools_ok": True}}}}
    )
    candidates = apply_overrides(builder(db, gpu_state())(requirements()), rules)
    assert by_id(list(candidates))["qwen2.5-coder:14b"].row.tools_ok is True
