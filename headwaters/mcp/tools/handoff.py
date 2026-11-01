"""``headwaters_handoff`` (basic): a minimal packet to continue work elsewhere (HDW-205).

Goal, files (path and sha256, never the content), last error and the recent turns. The host passes
the text; Headwaters does not keep a transcript. Nothing is summarized by a model: the bridge that
adds older-turn summaries is the ★ BridgePlugin (HDW-607), which can replace this packet. Every
text field is cut to a bound and scanned for secret-shaped strings first (they are replaced by a
marker, and counted), so a packet pasted into another host or stored never carries a key. The
stored packet has no ``text``-like keys (``state.scrub``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from headwaters.core.engine import Engine
from headwaters.core.paths import long_safe
from headwaters.core.secrets_detect import SecretDetector
from headwaters.core.state_misc import add_handoff
from headwaters.core.workspace_files import read_text
from headwaters.core.workspace_paths import WorkspaceError
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.schemas import FileNote
from headwaters.mcp.tools.schemas_more import HandoffPacket, HandoffResult
from headwaters.mcp.tools.workspace import Workspace

__all__ = ["REDACTED", "build_handoff", "redact"]

REDACTED = "[redacted: looked like a secret]"
_FILE_BYTES = 1024 * 1024


def redact(text: str, detector: SecretDetector) -> tuple[str, int]:
    """``text`` with every secret-shaped span replaced; the count of spans replaced."""
    found = detector.scan(text)
    if not found:
        return text, 0
    out: list[str] = []
    cursor = 0
    count = 0
    for match in found:  # ordered by start; overlapping spans are merged
        if match.start < cursor:
            cursor = max(cursor, match.end)
            continue
        out.append(text[cursor : match.start])
        out.append(REDACTED)
        cursor = match.end
        count += 1
    out.append(text[cursor:])
    return "".join(out), count


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " [cut]"


def _hash_file(workspace: Workspace, requested: str) -> tuple[FileNote, str | None]:
    """The note for one file and its sha256 (None when it cannot be read)."""
    roots = workspace.roots
    if roots is None:
        return FileNote(path=requested, status="missing"), None
    try:
        resolved = roots.resolve(requested)
    except WorkspaceError as exc:
        status = "outside" if exc.code == "outside_workspace" else "invalid"
        return FileNote(path=requested, status=status), None
    _text, problem = read_text(resolved, _FILE_BYTES)
    if problem is not None:
        return FileNote(path=resolved.display, status=problem), None
    digest = hashlib.sha256(long_safe(resolved.path).read_bytes()).hexdigest()
    return FileNote(path=resolved.display, status="included"), digest


def build_handoff(
    tc: ToolContext,
    engine: Engine,
    workspace: Workspace,
    *,
    goal: str,
    paths: Sequence[str],
    last_error: str,
    recent_turns: Sequence[str],
    session: str,
    scope: str | None,
    model: str | None,
) -> HandoffResult:
    """Build, scrub and store the packet. Blocking (file reads); callers run it off the loop."""
    detector = SecretDetector.from_catalog(engine.catalog)
    bound = tc.settings.max_handoff_chars
    redacted = 0

    def clean(text: str, limit: int = bound) -> str:
        nonlocal redacted
        text, n = redact(_cut(text, limit), detector)
        redacted += n
        return text

    notes: list[str] = []
    files: list[dict[str, str]] = []
    file_notes: list[FileNote] = []
    for requested in paths[: tc.settings.max_paths]:
        note, digest = _hash_file(workspace, requested)
        file_notes.append(note)
        if digest is not None:
            files.append({"path": note.path, "sha256": digest, "role": "editing"})
    if len(paths) > tc.settings.max_paths:
        notes.append(f"Only the first {tc.settings.max_paths} paths were listed.")
    kept = list(recent_turns)[-tc.settings.max_handoff_turns :]
    if len(recent_turns) > len(kept):
        notes.append(f"Only the last {len(kept)} turns were kept.")
    packet = HandoffPacket(
        goal=clean(goal),
        files=files,
        last_error=clean(last_error),
        recent_turns=[clean(turn) for turn in kept],
        scope=scope,
        model=model,
    )
    notes.append(
        "Files are listed by path and hash only: the next model must re-read them. "
        "Older turns are not summarized in this basic packet."
    )
    if redacted:
        notes.append(f"{redacted} secret-shaped string(s) were removed from the packet.")
    packet_id = add_handoff(tc.state(), session, packet.model_dump(mode="json"))
    return HandoffResult(
        ok=True,
        packet_id=packet_id,
        packet=packet,
        files=file_notes,
        redacted=redacted,
        notes=notes,
    )
