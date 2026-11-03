"""The private Python environment (HDW-405): plan the uv commands, never run them unasked.

The installer ships ``uv.exe``, ``uv.lock``, ``pyproject.toml`` and the package source, and the
venv is built on the user's machine with ``uv sync --frozen``. Nothing is bundled: no embedded
Python, no frozen model weights, no PyInstaller (v3.1 C7). Deleting and recreating the runtime is
always safe, because all user data lives outside it in the install root.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headwaters.packaging.layout import CATALOG_DIR_NAME, Install

#: A fixed interpreter keeps the frozen lockfile and the venv in agreement.
PINNED_PYTHON = "3.11"

#: Everything the payload hands to ``uv sync``; ``catalog`` is needed by the seed bootstrap.
PAYLOAD_ENTRIES = (
    "pyproject.toml",
    "uv.lock",
    "uv.exe",
    "headwaters",
    CATALOG_DIR_NAME,
)

SYNC_FLAGS = ("sync", "--frozen", "--no-dev", "--python", PINNED_PYTHON)
RUN_FLAGS = ("run", "--frozen", "--no-dev")


@dataclass(frozen=True)
class Command:
    """One command to run, with the reason a human reading the log cares about."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    why: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "cwd": None if self.cwd is None else str(self.cwd),
            "why": self.why,
        }


def sync_command(install: Install) -> Command:
    """Build (or repair) the venv. Takes a few minutes on a cold machine."""
    return Command(
        argv=(
            str(install.uv),
            *SYNC_FLAGS,
            "--project",
            str(install.runtime),
        ),
        cwd=install.runtime,
        why="create the private virtualenv from the frozen lockfile",
    )


def run_command(
    install: Install,
    *args: str,
    project: Path | str | None = None,
) -> Command:
    """Run a Headwaters command with the private runtime.

    The itch manifest and the shortcuts use this rather than the venv shims directly: a venv
    cannot be relocated, so ``uv run`` resolves it at launch time from the payload root.
    """
    return Command(
        argv=(
            str(install.uv),
            *RUN_FLAGS,
            "--project",
            str(project if project is not None else install.runtime),
            "headwaters",
            *args,
        ),
        cwd=install.local,
        why="run a Headwaters command inside the private runtime",
    )


@dataclass(frozen=True)
class RuntimeCheck:
    """What ``headwaters doctor`` and the installer verify after ``uv sync``."""

    ok: bool
    missing: tuple[Path, ...]

    def detail(self) -> str:
        if self.ok:
            return "the private runtime and both launchers are present"
        return "missing: " + ", ".join(str(path) for path in self.missing)


def verify(install: Install) -> RuntimeCheck:
    """Check the pieces ``uv sync`` must have produced; touches nothing."""
    wanted = (
        install.uv,
        install.lock,
        install.project,
        install.catalog,
        install.cli,
        install.mcp,
        install.python,
    )
    missing = tuple(path for path in wanted if not path.exists())
    return RuntimeCheck(ok=not missing, missing=missing)


def apply(command: Command, *, runner: Any = None) -> int:
    """Run ``command`` and return its exit code. Tests inject ``runner`` and never spawn uv."""
    if runner is None:
        completed = subprocess.run(list(command.argv), cwd=command.cwd, check=False)
        return int(completed.returncode)
    completed = runner(list(command.argv), cwd=command.cwd)
    return int(getattr(completed, "returncode", 0))


def missing_entries(
    runtime_dir: Path | str, entries: Sequence[str] = PAYLOAD_ENTRIES
) -> tuple[str, ...]:
    """Payload entries absent from ``runtime_dir``; the build refuses to sign an incomplete set."""
    base = Path(runtime_dir)
    return tuple(name for name in entries if not (base / name).exists())


def command_line(command: Command) -> str:
    """A copy-pasteable one-liner for the installer log and the release checklist."""
    return subprocess.list2cmdline(list(command.argv))
