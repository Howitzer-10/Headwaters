"""One watchdog tick: start the daemon when it has died (HDW-521).

Nothing inside the daemon can bring the daemon back, and the tray is its child, so a crash leaves
the user with no icon, no message and no reason to suspect anything. The repeating
``Headwaters Watchdog`` scheduled task runs ``headwaters watchdog`` every five minutes; this
module decides what that tick does. It is pure orchestration over injected callables, so a test
needs no task, no daemon and no waiting.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from headwaters.core import daemon_hold
from headwaters.core.daemon_client import DaemonClient, find_running
from headwaters.core.daemon_control import ControlResult, start_daemon

__all__ = ["HELD_MESSAGE", "Action", "WatchdogResult", "tick"]

Action = Literal["held", "running", "started", "failed"]

HELD_MESSAGE = (
    "The daemon was stopped on purpose (headwaters stop); the watchdog is leaving it stopped. "
    "Run 'headwaters start' to bring it back."
)

Finder = Callable[[], DaemonClient | None]
Starter = Callable[..., ControlResult]
Held = Callable[[], bool]


@dataclass(frozen=True)
class WatchdogResult:
    """What one tick did, in the words the command prints and the task records."""

    action: Action
    message: str

    @property
    def ok(self) -> bool:
        """A held tick is a success: the task did exactly what the user asked."""
        return self.action != "failed"


def tick(
    *, held: Held = daemon_hold.is_held, find: Finder = find_running, start: Starter = start_daemon
) -> WatchdogResult:
    """Start the daemon unless it is running or the user stopped it on purpose."""
    if held():
        return WatchdogResult("held", HELD_MESSAGE)
    if find() is not None:
        return WatchdogResult("running", "The daemon is running; nothing to start.")
    started = start()
    if started.ok:
        return WatchdogResult("started", started.message)
    return WatchdogResult("failed", started.message)
