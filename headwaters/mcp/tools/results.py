"""Build the structured results and the tool-result envelope (HDW-204).

A tool returns a ``CallToolResult`` with ``structured_content`` (validated against its
``outputSchema``) and one short text block. A refusal is an error result (``is_error``), still
shaped like the schema, so a host can read ``error.code`` and ``error.fix``.
"""

from __future__ import annotations

from typing import Any

from mcp import types
from pydantic import BaseModel

from headwaters.mcp.tools.failures import Failure
from headwaters.mcp.tools.outcome import Outcome
from headwaters.mcp.tools.render import render_ask, render_check, render_do, render_error
from headwaters.mcp.tools.schemas import AskResult, CheckResult, DoResult, ErrorInfo

__all__ = [
    "ask_envelope",
    "ask_error",
    "ask_result",
    "check_error",
    "check_result",
    "do_envelope",
    "do_error",
    "do_result",
    "envelope",
]


def envelope(model: BaseModel, text: str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structured_content=model.model_dump(mode="json"),
        is_error=is_error,
    )


def _common(o: Outcome) -> dict[str, Any]:
    d = o.result.decision
    return {
        "ok": True,
        "answer": o.answer,
        "answer_truncated": o.answer_truncated,
        "model": d.model,
        "tier": d.tier,
        "scope": d.scope.scope,
        "verified": o.status == "pass",
        "verify_status": o.status,
        "verify_reasons": list(o.reasons),
        "below_floor": d.below_floor,
        "reasons": list(o.result.reasons),
        "suggestions": list(o.suggestions),
        "relay": o.relay,
        "result_uri": o.uri,
        "files": list(o.files),
        "notes": list(o.notes),
    }


def do_result(o: Outcome) -> DoResult:
    return DoResult(**_common(o), diffs=list(o.diffs), commands=list(o.commands))


def ask_result(o: Outcome) -> AskResult:
    common = _common(o)
    if common["relay"] == "apply_diff":  # ask never returns diffs
        common["relay"] = "verbatim"
    return AskResult(**common)


def _error_fields(failure: Failure) -> dict[str, Any]:
    return {
        "ok": False,
        "error": failure.error,
        "suggestions": list(failure.suggestions),
        "files": list(failure.files),
        "relay": "verbatim",
    }


def _error_text(failure: Failure) -> str:
    return render_error(failure.error, [s.text for s in failure.suggestions])


def do_error(failure: Failure) -> types.CallToolResult:
    return envelope(DoResult(**_error_fields(failure)), _error_text(failure), is_error=True)


def ask_error(failure: Failure) -> types.CallToolResult:
    return envelope(AskResult(**_error_fields(failure)), _error_text(failure), is_error=True)


def check_error(error: ErrorInfo) -> types.CallToolResult:
    result = CheckResult(ok=False, error=error)
    return envelope(result, render_check(result), is_error=True)


def check_result(result: CheckResult) -> types.CallToolResult:
    return envelope(result, render_check(result))


def do_envelope(o: Outcome) -> types.CallToolResult:
    result = do_result(o)
    return envelope(result, render_do(result))


def ask_envelope(o: Outcome) -> types.CallToolResult:
    result = ask_result(o)
    return envelope(result, render_ask(result))
