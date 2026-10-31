"""``git apply --check`` for candidate diffs, with CRLF/LF handling (HDW-116).

Line endings: the candidate is normalized to LF first. The repo's style for the touched files is
read from ``git ls-files --eol`` (which already folds in ``.gitattributes``), falling back to
``core.autocrlf`` for new files. We try that style first and the other one second, so a CRLF
working tree accepts an LF diff (what a model writes) and the reverse. Nothing is written to the
repo: ``git apply --check`` only tests. All git calls use a fixed argv, never a shell.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from headwaters.core import paths
from headwaters.core.verify_types import Collector

_DIFF_PATH = re.compile(r"(?m)^(?:\+\+\+|---) (?:[ab]/)?(\S+)")


def _git(
    repo: Path, args: list[str], timeout: float, data: bytes | None = None
) -> subprocess.CompletedProcess[bytes] | None:
    exe = shutil.which("git")
    if exe is None:
        return None
    try:
        return subprocess.run(
            [exe, "-C", str(paths.long_safe(repo)), *args],
            input=data,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def prefers_crlf(repo: Path, files: list[str], timeout: float) -> bool:
    """True when the working tree uses CRLF for these files (or ``core.autocrlf=true``)."""
    if files:
        listing = _git(repo, ["ls-files", "--eol", "--", *files], timeout)
        if listing is not None and listing.returncode == 0:
            info = listing.stdout.decode("utf-8", "replace")
            if "w/crlf" in info or "eol=crlf" in info:
                return True
            if "w/lf" in info:
                return False
    config = _git(repo, ["config", "--get", "core.autocrlf"], timeout)
    return (
        config is not None
        and config.returncode == 0
        and config.stdout.decode("utf-8", "replace").strip().lower() == "true"
    )


def check_diff(
    out: Collector, diff: str, repo: Path | None, line: int | None, timeout: float = 15.0
) -> None:
    """Record the outcome of ``git apply --check`` for an LF-normalized ``diff``."""
    if repo is None:
        out.skip("diff", "no repository configured for git apply --check")
        return
    if shutil.which("git") is None:
        out.skip("diff", "git is not installed")
        return
    text = diff if diff.endswith("\n") else diff + "\n"
    files = sorted({m for m in _DIFF_PATH.findall(text) if m.lstrip("/") != "dev/null"})
    lf, crlf = text, text.replace("\n", "\r\n")
    variants = [crlf, lf] if prefers_crlf(repo, files, timeout) else [lf, crlf]
    first_error = ""
    for variant in variants:
        result = _git(
            repo, ["apply", "--check", "--whitespace=nowarn", "-"], timeout, variant.encode("utf-8")
        )
        if result is None:
            out.skip("diff", "git apply --check could not run")
            return
        if result.returncode == 0:
            out.ran("diff")
            return
        if not first_error:
            lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
            first_error = lines[0] if lines else "git apply --check failed"
    out.fail("diff", "diff_does_not_apply", first_error, line)
