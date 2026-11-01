"""The relay contract and compact answers (HDW-204, v3.4 B1).

``relay`` tells a (possibly weak) host model what it may do with a result:

* ``apply_diff``: the diffs were checked and apply to the user's files; apply them as given.
* ``verbatim``: show the answer unchanged (code, a failed or unchecked result, a below-floor pick).
* ``summarize_ok``: plain prose; the host may summarize it.
"""

from __future__ import annotations

from headwaters.mcp.tools.extract import split_segments
from headwaters.mcp.tools.schemas import Relay, VerifyStatus

__all__ = ["choose_relay", "compact", "has_code"]


def has_code(text: str) -> bool:
    return any(s.is_code and s.closed for s in split_segments(text))


def choose_relay(
    *,
    status: VerifyStatus,
    diffs: bool,
    code: bool,
    below_floor: bool,
    diff_applied: bool,
) -> Relay:
    """``apply_diff`` only for a diff that passed ``git apply --check`` on the user's files.

    ``diff_applied`` is that proof. Without it (no workspace, no git) a diff is ``verbatim``. A
    failed check or a below-floor pick is always ``verbatim``: the host must show the problem, not
    paraphrase it. Plain prose is ``summarize_ok`` even when nothing could be checked.
    """
    if status == "fail" or below_floor:
        return "verbatim"
    if diffs:
        return "apply_diff" if status == "pass" and diff_applied else "verbatim"
    return "verbatim" if code else "summarize_ok"


def compact(text: str, limit: int, pointer: str) -> tuple[str, bool]:
    """``text`` cut at a line boundary to ``limit`` characters, with a pointer to the full text."""
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    newline = cut.rfind("\n")
    if newline > limit // 2:
        cut = cut[:newline]
    return f"{cut.rstrip()}\n[... cut: full text at {pointer}]", True
