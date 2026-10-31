"""Lexical path checks for files-by-path (HDW-203, v3.1 C4).

``check_path_text`` refuses unsafe path strings before any filesystem access: traversal, NUL and
control characters, device namespaces and, with Windows rules, drive-relative paths, alternate
data streams, reserved device names and trailing dots or spaces. ``is_within`` compares two
resolved absolute paths (case-insensitive and drive/UNC-aware with Windows rules).

The Windows rules are selectable (``windows=``) so tests cover them on every platform.
"""

from __future__ import annotations

import re
import sys
from pathlib import PurePosixPath, PureWindowsPath

_DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\", "//?/", "//./")
_DRIVE_RELATIVE = re.compile(r"^[A-Za-z]:(?![\\/])")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_SEPARATORS = re.compile(r"[\\/]+")
_WIN_BAD_CHARS = frozenset('*?"<>|')
_WIN_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class WorkspaceError(ValueError):
    """A path or root was refused. ``code`` is stable and safe to show to a host."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def default_windows() -> bool:
    return sys.platform == "win32"


def check_path_text(text: str, *, windows: bool | None = None) -> None:
    """Raise ``WorkspaceError`` for a path string that is unsafe whatever the filesystem holds.

    Traversal (``..`` after either separator), control characters and device namespaces are
    refused on every platform; the Windows-only rules run when ``windows`` is true (default:
    the current platform).
    """
    win = default_windows() if windows is None else windows
    if not text.strip():
        raise WorkspaceError("empty_path", "the path is empty")
    if any(ord(ch) < 32 for ch in text):
        raise WorkspaceError("invalid_chars", f"control characters in path {text!r}")
    if text.startswith(_DEVICE_PREFIXES):
        raise WorkspaceError("device_path", f"device or extended paths are refused: {text!r}")
    if any(part == ".." for part in _SEPARATORS.split(text)):
        raise WorkspaceError("traversal", f"'..' is not allowed in {text!r}")
    if win:
        _check_windows(text)


def _check_windows(text: str) -> None:
    if _DRIVE_RELATIVE.match(text):
        raise WorkspaceError("drive_relative", f"drive-relative path refused: {text!r}")
    body = text[2:] if _DRIVE_PREFIX.match(text) else text
    for part in _SEPARATORS.split(body):
        if part in ("", "."):
            continue
        if ":" in part:
            raise WorkspaceError("stream", f"alternate data streams are refused: {text!r}")
        if part != part.rstrip(" .") or any(ch in _WIN_BAD_CHARS for ch in part):
            raise WorkspaceError("invalid_name", f"invalid Windows file name in {text!r}")
        if part.split(".", 1)[0].rstrip(" ").upper() in _WIN_RESERVED:
            raise WorkspaceError("reserved_name", f"reserved device name in {text!r}")


def strip_extended_prefix(text: str) -> str:
    """``\\\\?\\C:\\x`` -> ``C:\\x`` and ``\\\\?\\UNC\\s\\sh`` -> ``\\\\s\\sh`` (lexical only)."""
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def is_within(candidate: str, root: str, *, windows: bool) -> bool:
    """Containment of two absolute, already-resolved paths (``root`` itself counts as inside).

    Windows rules: case-insensitive; drive letters and UNC ``\\\\server\\share`` anchors must
    match. POSIX rules: case-sensitive.
    """
    if windows:
        return PureWindowsPath(strip_extended_prefix(candidate)).is_relative_to(
            PureWindowsPath(strip_extended_prefix(root))
        )
    return PurePosixPath(candidate).is_relative_to(PurePosixPath(root))


def relative_posix(candidate: str, root: str, *, windows: bool) -> str:
    """``candidate`` relative to ``root`` with ``/`` separators (``.`` for the root itself)."""
    if windows:
        return (
            PureWindowsPath(strip_extended_prefix(candidate))
            .relative_to(PureWindowsPath(strip_extended_prefix(root)))
            .as_posix()
        )
    return PurePosixPath(candidate).relative_to(PurePosixPath(root)).as_posix()
