"""HDW-117: the router's verification helpers (BasicVerifier wiring, broken verifiers)."""

from __future__ import annotations

import pytest

from headwaters.core import verify_basic
from headwaters.core.executor.types import ExecResult, ToolCall, Usage
from headwaters.core.router_verify import run_verifiers, schema_of, wants_verify
from headwaters.core.verify_basic import BasicVerifier
from tests.router_support import Verifier


def answer(content: str, tool_calls: tuple[ToolCall, ...] = ()) -> ExecResult:
    return ExecResult("m", "local", content, "", tool_calls, "stop", Usage(), 0.1)


class Crashing:
    api_version = 1

    def verify(self, candidate: str) -> list[str]:
        raise RuntimeError("verifier bug")


def test_wants_verify_follows_the_mode_and_the_mandatory_flag() -> None:
    assert wants_verify("always", agentic=False, required=False)
    assert not wants_verify("off", agentic=True, required=False)
    assert wants_verify("agentic", agentic=True, required=False)
    assert not wants_verify("agentic", agentic=False, required=False)
    assert wants_verify("off", agentic=False, required=True)  # below the floor: mandatory


def test_schema_of_reads_an_openai_response_format() -> None:
    schema = {"type": "object", "required": ["a"]}
    fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": schema}}
    assert schema_of(fmt) == schema
    assert schema_of({"type": "json_object"}) is None
    assert schema_of("json") is None
    assert schema_of(None) is None


def test_basic_verifier_checks_the_schema_and_tool_calls() -> None:
    schema = {"type": "object", "required": ["a"]}
    fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": schema}}
    outcome = run_verifiers([BasicVerifier()], answer('{"b": 1}'), response_format=fmt)
    assert outcome.ran
    assert outcome.passed is False
    assert any(p.startswith("json_schema_violation") for p in outcome.problems)

    tools = [{"type": "function", "function": {"name": "run", "parameters": {"type": "object"}}}]
    bad_call = (ToolCall("1", "missing_tool", "{}"),)
    outcome = run_verifiers([BasicVerifier()], answer("", bad_call), tools=tools)
    assert outcome.passed is False
    assert any(p.startswith("tool_unknown") for p in outcome.problems)


def test_a_crashing_verifier_is_skipped_not_fatal() -> None:
    outcome = run_verifiers([Crashing(), Verifier()], answer("fine"))
    assert outcome.ran
    assert outcome.passed is True
    assert outcome.verifiers == 1


def test_only_crashing_verifiers_means_not_run() -> None:
    outcome = run_verifiers([Crashing()], answer("fine"))
    assert outcome.ran is False
    assert outcome.passed is None
    assert not outcome.failed


def test_duplicate_problems_are_reported_once() -> None:
    outcome = run_verifiers([Verifier(), Verifier()], answer("bad"))
    assert outcome.problems == ("bad_output: contains the word bad",)
    assert outcome.verifiers == 2


def test_prose_checks_nothing_and_is_not_a_pass() -> None:
    outcome = run_verifiers([BasicVerifier()], answer("Sure, here is the explanation."))
    assert outcome.ran
    assert outcome.checked == 0
    assert outcome.passed is None
    assert outcome.status == "skipped"
    assert not outcome.failed


def test_a_real_check_counts_and_passes() -> None:
    outcome = run_verifiers([BasicVerifier()], answer("```python\nx = 1\n```"))
    assert outcome.checked >= 1
    assert outcome.passed is True
    assert outcome.status == "pass"


def test_a_missing_grammar_is_a_skip_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_name: str) -> object:
        raise RuntimeError("no grammar")

    monkeypatch.setattr(verify_basic, "_parser", broken)
    report = BasicVerifier().verify_report("let x = 1;", language="javascript")
    assert [c.status for c in report.checks if c.name == "parse"] == ["skipped"]
    assert report.checked == 0
    outcome = run_verifiers([BasicVerifier()], answer("let x = 1;"))
    assert outcome.passed is not False
