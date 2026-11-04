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
    """Start the tray with no console, no pipes and nobody waiting on it (HDW-504)."""
    flags = 0
    if os.name == "nt":
        # The daemon is usually started by the logon task: a child with a console window would
        # flash one on every logon.
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.Popen(
        command(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


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
