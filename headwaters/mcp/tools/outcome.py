"""Turn a routed answer into a compact, verified outcome (HDW-204, v3.4 B1).

Splits the model's answer into prose, diffs and commands, verifies it (the router's own gate plus
this tool's check, with ``git apply --check`` in the workspace), picks the ``relay`` value, keeps
the full detail at ``headwaters://result/<id>`` and cuts the inline answer. The detail record holds
the model's answer and codes only: never the task text or file contents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from headwaters.core.advice import BELOW_FLOOR_WARNING
from headwaters.core.engine import Engine
from headwaters.core.router_types import RouteResult
from headwaters.mcp.threads import run_blocking
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.extract import Extracted, extract
from headwaters.mcp.tools.relay import choose_relay, compact, has_code
from headwaters.mcp.tools.schemas import FileNote, Relay, SuggestionOut, VerifyStatus
from headwaters.mcp.tools.store import result_uri
from headwaters.mcp.tools.verify import (
    Verification,
    combine,
    registered_verifiers,
    verify_text,
)
from headwaters.mcp.tools.workspace import Workspace

__all__ = ["Outcome", "build_outcome"]


@dataclass(frozen=True)
class Outcome:
    """Everything a tool needs to build its structured result."""

    result: RouteResult
    answer: str | None
    answer_truncated: bool
    diffs: tuple[str, ...]
    commands: tuple[str, ...]
    status: VerifyStatus
    reasons: tuple[str, ...]
    relay: Relay
    uri: str
    files: tuple[FileNote, ...]
    suggestions: tuple[SuggestionOut, ...]
    notes: tuple[str, ...]


def _verify_answer(engine: Engine, text: str, workspace: Workspace) -> Verification:
    plugins = registered_verifiers(engine.registry)
    return verify_text(text, engine.catalog, plugins, repo=workspace.repo)


def _detail(
    tool: str, result: RouteResult, extracted: Extracted, files: list[FileNote]
) -> dict[str, Any]:
    d = result.decision
    return {
        "tool": tool,
        "decision_id": result.decision_id,
        "answer": result.result.content,
        "diffs": list(extracted.diffs),
        "commands": list(extracted.commands),
        "model": d.model,
        "tier": d.tier,
        "scope": d.scope.scope,
        "locks": list(d.scope.locks),
        "score": round(d.score, 2),
        "floor": round(d.floor, 2),
        "below_floor": d.below_floor,
        "rung": d.rung,
        "reasons": list(result.reasons),
        "attempts": [
            {"model": a.model, "tier": a.tier, "status": a.status} for a in result.attempts
        ],
        "files": [f.model_dump() for f in files],
    }


async def build_outcome(
    tc: ToolContext,
    engine: Engine,
    tool: str,
    result: RouteResult,
    workspace: Workspace,
    files: list[FileNote],
    notes: list[str],
    want: str,
) -> Outcome:
    text = result.result.content
    if tool == "do":
        extracted = extract(text, engine.catalog.analyzer_hints.fence_aliases)
    else:  # headwaters_ask is read-only: it never hands back a diff or a command to run
        extracted = Extracted((), (), text)
    mine: Verification | None = None
    if text.strip():
        mine = await run_blocking(lambda: _verify_answer(engine, text, workspace))
    status, reasons = combine(result.verify, mine)
    d = result.decision
    if d.below_floor:
        notes.insert(0, BELOW_FLOOR_WARNING)
    # HDW-502: the ★ feature's handback line names a way forward, so it leads the notes.
    if d.advisory:
        notes.insert(0, d.advisory)
    if want == "diff" and not extracted.diffs:
        notes.append("No diff was found in the model's answer.")
    uri = result_uri(tc.store.put(_detail(tool, result, extracted, files)))
    shown = extracted.prose if (extracted.diffs and want != "plan") else text
    answer, cut = compact(shown, tc.settings.answer_max_chars, uri)
    relay = choose_relay(
        status=status,
        diffs=bool(extracted.diffs),
        code=has_code(shown),
        below_floor=d.below_floor,
        diff_applied=mine is not None and mine.diff_applied,
    )
    tips = tuple(SuggestionOut(code=s.code, text=s.text) for s in d.suggestions)
    return Outcome(
        result,
        answer or None,
        cut,
        extracted.diffs,
        extracted.commands,
        status,
        reasons,
        relay,
        uri,
        tuple(files),
        tips,
        tuple(notes),
    )
