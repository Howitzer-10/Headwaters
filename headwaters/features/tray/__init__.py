"""The tray app (HDW-504): a ★ surface that shows what the daemon is doing.

The tray is its own process. The daemon starts it as a child when the ``tray`` feature flag is
on, so the logon task that already runs ``headwaters start`` controls both and no second
scheduled task is added. ``headwaters tray`` runs the same thing by hand.

It reads only the daemon's internal API (``/v1/status``, ``/v1/keys``, ``/v1/scope``) and shows
provider names exactly as the registry gives them. No key, masked value, key prefix, environment
variable name or URL ever reaches a menu label, a tooltip or a toast.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import IO

from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.plugins import CURRENT_API_VERSION

__all__ = [
    "DEFAULT",
    "DISABLE_ENV",
    "FEATURE_ID",
    "FEATURE_SPEC",
    "TITLE",
    "TraySurface",
    "command",
    "log_line",
    "log_path",
    "log_tail",
    "register",
    "run",
    "spawn_child",
]

FEATURE_ID = "tray"
TITLE = "Tray icon"
# On by default, like every other feature during the beta; a user switches it off in rules.yaml.
DEFAULT = True
# Set this in the environment to keep the tray from starting at all (a headless box, a test).
DISABLE_ENV = "HEADWATERS_NO_TRAY"
# A test process must never leave a real icon behind, and the daemon honours this too.
TEST_ENV = "PYTEST_CURRENT_TEST"
# The child's stderr goes here - a tray that dies at once leaves its traceback where the daemon,
# and a person, can read it instead of vanishing into DEVNULL. Menu actions log their start and
# result here too, because a toast can be dropped and then nothing at all would be left.
TRAY_LOG_NAME = "tray.log"
TRAY_LOG_MAX_BYTES = 8192
TRAY_LOG_TAIL_CHARS = 2000

FEATURE_SPEC = FeatureSpec(
    id=FEATURE_ID,
    star=True,
    title=TITLE,
    interfaces=("SurfacePlugin",),
    default_enabled=DEFAULT,
)


class TraySurface:
    """The ``SurfacePlugin`` the registry holds; the icon itself lives in its own process."""

    api_version = CURRENT_API_VERSION
    surface_id = FEATURE_ID

    def __init__(self) -> None:
        self._child: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._child is None:
            self._child = spawn_child()

    def stop(self) -> None:
        child, self._child = self._child, None
        if child is not None and child.poll() is None:
            child.terminate()


def register(registry: FeatureRegistry) -> None:
    registry.register(FEATURE_SPEC, {"SurfacePlugin": TraySurface()})


def command() -> list[str]:
    """The argv that starts the tray in a process of its own."""
    return [sys.executable, "-m", "headwaters.features.tray"]


def spawn_child() -> subprocess.Popen[bytes]:
    """Start the tray with no console and nobody waiting on it (HDW-504).

    The child's stderr goes to ``state_dir()/tray.log`` instead of DEVNULL: a tray that dies on
    its first tick used to leave no trace at all, and the daemon could only say nothing.
    """
    flags = 0
    if os.name == "nt":
        # The daemon is usually started by the logon task: a child with a console window would
        # flash one on every logon.
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = _open_log()
    try:
        return subprocess.Popen(
            command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            creationflags=flags,
            close_fds=True,
        )
    finally:
        # The child holds its own copy of the handle; this one has done its work.
        log.close()


def log_path() -> Path:
    """Where the tray child's stderr goes: beside ``tray.lock``, in the state dir."""
    from headwaters.core import paths

    return paths.state_dir() / TRAY_LOG_NAME


def log_tail(limit: int = TRAY_LOG_TAIL_CHARS) -> str:
    """The last few KB of that log, for the warning the daemon writes when the tray dies."""
    try:
        text = log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


def log_line(message: str) -> None:
    """Append one line to the tray log, capped exactly like the child's stderr (HDW-504).

    A toast is not durable: Windows 11 drops balloon tips from a process that is not a
    registered notification source. An action writes that it started and how it ended here
    instead, so a click that shows nothing still leaves a trace. A missing started line is
    proof the action never fired; a started line with no finished line is proof it hung.
    """
    line = " ".join(message.splitlines())
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _truncate_if_large(path)
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"{line}\n")
    except OSError:
        return  # a log that cannot be written never breaks the action it describes


def _truncate_if_large(path: Path) -> None:
    """One run is what matters: do not grow a file nobody reads."""
    if path.exists() and path.stat().st_size > TRAY_LOG_MAX_BYTES:
        path.write_bytes(b"")


def _open_log() -> IO[bytes]:
    """The log handle, truncated once it passes a few KB; DEVNULL if it cannot be opened."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _truncate_if_large(path)
        return path.open("ab")
    except OSError:
        return open(os.devnull, "wb")  # closed by the caller's finally


def run() -> int:
    """Run the tray in this process. A second instance, or ``HEADWATERS_NO_TRAY``, exits quietly."""
    if os.environ.get(DISABLE_ENV):
        return 0
    from headwaters.core import paths
    from headwaters.core.instance_lock import InstanceLock

    lock = InstanceLock(paths.state_dir() / "tray.lock")
    if not lock.acquire():
        return 0  # a tray is already running for this user: one icon, not two
    try:
        from headwaters.features.tray.app import TrayApp

        return TrayApp().run()
    finally:
        lock.release()
