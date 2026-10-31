"""Verification step of the router (HDW-117, v3 section 4.6).

Runs every registered ``VerifierPlugin`` on the answer. ``BasicVerifier`` is called through its
richer ``verify_report`` so JSON schemas and tool-call arguments are checked too; other plugins
get the plain ``verify(candidate)``. A verifier that crashes is skipped, never fatal. Problems are
rendered reason codes, never the candidate text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from headwaters.core.executor.types import ExecResult
from headwaters.core.plugins import VerifierPlugin
from headwaters.core.router_types import VerifyOutcome
from headwaters.core.scope import VerifyMode
from headwaters.core.verify_basic import BasicVerifier


def wants_verify(mode: VerifyMode, *, agentic: bool, required: bool) -> bool:
    """``required`` (below the floor or an optimistic pick) beats ``off``: the gate is mandatory."""
    if required or mode == "always":
        return True
    return mode == "agentic" and agentic


def schema_of(response_format: Mapping[str, Any] | str | None) -> Mapping[str, Any] | None:
    """The JSON Schema inside an OpenAI ``response_format``, if it carries one."""
    if not isinstance(response_format, Mapping):
        return None
    inner = response_format.get("json_schema")
    schema = inner.get("schema") if isinstance(inner, Mapping) else None
    return schema if isinstance(schema, Mapping) else None


def _tool_calls(result: ExecResult) -> list[dict[str, Any]] | None:
    if not result.tool_calls:
        return None
    return [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in result.tool_calls]


def _one(
    verifier: VerifierPlugin,
    result: ExecResult,
    tools: Sequence[Mapping[str, Any]] | None,
    schema: Mapping[str, Any] | None,
) -> tuple[list[str], int]:
    """Rendered problems and how many checks really ran. A plain plugin counts as one check."""
    if isinstance(verifier, BasicVerifier):
        report = verifier.verify_report(
            result.content, json_schema=schema, tool_calls=_tool_calls(result), tools=tools
        )
        return [reason.render() for reason in report.reasons], report.checked
    return list(verifier.verify(result.content)), 1


def run_verifiers(
    verifiers: Sequence[VerifierPlugin],
    result: ExecResult,
    *,
    tools: Sequence[Mapping[str, Any]] | None = None,
    response_format: Mapping[str, Any] | str | None = None,
) -> VerifyOutcome:
    schema = schema_of(response_format)
    problems: list[str] = []
    ran = 0
    checked = 0
    for verifier in verifiers:
        try:
            found, count = _one(verifier, result, tools, schema)
        except Exception:  # a broken verifier must not fail the user's answer
            continue
        ran += 1
        checked += count
        problems.extend(p for p in found if p not in problems)
    if ran == 0:
        return VerifyOutcome(False, None)
    if problems:
        return VerifyOutcome(True, False, tuple(problems), ran, checked)
    # F11: no problem found and nothing checked establishes nothing, so it is not a pass.
    return VerifyOutcome(True, True if checked else None, (), ran, checked)
