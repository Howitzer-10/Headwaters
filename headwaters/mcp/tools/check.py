"""``headwaters_check``: verify a diff or code with the registered verifiers (HDW-204).

Read-only and model-free. A diff is test-applied to the workspace with ``git apply --check`` when a
workspace is known; with no workspace the apply check is skipped and the result says so. The result
is never ``verified`` when nothing could be checked (F11).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp import types

from headwaters.core.verify_fences import looks_like_diff, normalize_eol
from headwaters.mcp.threads import run_blocking
from headwaters.mcp.tools.context import ToolContext, ToolFailure
from headwaters.mcp.tools.flow import current_engine
from headwaters.mcp.tools.results import check_error, check_result
from headwaters.mcp.tools.schemas import CheckResult, ErrorInfo
from headwaters.mcp.tools.verify import registered_verifiers, verify_text
from headwaters.mcp.tools.workspace import open_workspace

__all__ = ["run_check"]


def _failure(code: str, message: str, fix: str) -> types.CallToolResult:
    return check_error(ErrorInfo(code=code, message=message, fix=fix))


async def run_check(
    tc: ToolContext,
    *,
    diff: str | None,
    code: str | None,
    language: str | None,
    meta: Mapping[str, Any] | None,
) -> types.CallToolResult:
    given = [text for text in (diff, code) if text is not None and text.strip()]
    if len(given) != 1:
        return _failure(
            "bad_arguments",
            "Give exactly one of `diff` or `code`.",
            'Example: {"diff": "--- a/x.py\\n+++ b/x.py\\n..."}',
        )
    text = given[0]
    if len(text) > tc.settings.max_check_chars:
        return _failure(
            "too_large",
            f"The text is over {tc.settings.max_check_chars} characters.",
            "Check one file or one diff at a time.",
        )
    try:
        engine = current_engine(tc)
        workspace = open_workspace(meta, tc.rules(), tc.configured_roots())
    except ToolFailure as exc:
        return _failure(exc.code, exc.message, exc.fix)
    notes: list[str] = []
    is_diff = diff is not None
    if is_diff and not looks_like_diff(normalize_eol(text)):
        return _failure(
            "not_a_diff",
            "`diff` does not look like a unified diff.",
            "Start it with `--- a/path` and `+++ b/path` lines, or pass `code` instead.",
        )
    if is_diff and workspace.repo is None:
        notes.append("No workspace folder: the diff was not test-applied (git apply --check).")
    plugins = registered_verifiers(engine.registry)
    verification = await run_blocking(
        lambda: verify_text(
            text,
            engine.catalog,
            plugins,
            language=None if is_diff else language,
            repo=workspace.repo,
        )
    )
    if verification.status == "unchecked":
        notes.append("Nothing could be checked (no grammar, no git or no patterns); not verified.")
    status = verification.status
    return check_result(
        CheckResult(
            ok=True,
            verified=status == "pass",
            verify_status=status,
            verify_reasons=list(verification.reasons),
            checks=list(verification.checks),
            relay="apply_diff"
            if is_diff and status == "pass" and verification.diff_applied
            else "verbatim",
            notes=notes,
        )
    )
