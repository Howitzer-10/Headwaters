"""The mark a deliberate ``headwaters stop`` leaves for the watchdog (HDW-521).

The watchdog (``headwaters watchdog``, run by a repeating scheduled task) starts the daemon again
when it has died. It must not undo a stop the user asked for, so ``stop`` writes this file and the
watchdog refuses to start while it exists. ``start`` deletes it: an explicit start, a tray restart
or the at-logon task all run ``start``, and all of them mean "I want it running".

The file sits next to ``daemon.json`` in the per-user state dir. It is advisory: nothing in the
daemon reads it, and a missing or unwritable file only makes the watchdog less cautious.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from headwaters.core import paths

__all__ = ["HOLD_FILE", "clear", "hold", "hold_path", "is_held"]

HOLD_FILE = "daemon.hold"


def hold_path() -> Path:
    return paths.state_dir() / HOLD_FILE


def is_held() -> bool:
    """True while a deliberate stop is in force. Never raises."""
    try:
        return paths.long_safe(hold_path()).is_file()
    except OSError:
        return False


def hold(outcome: str = "stopped") -> Path | None:
    """Record a deliberate stop. Returns the file, or ``None`` when it cannot be written.

    A read-only state dir must never turn a successful stop into a failure, so this never raises.
    """
    target = paths.long_safe(hold_path())
    payload = {"outcome": outcome, "at": datetime.now(UTC).isoformat(timespec="seconds")}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(json.dumps(payload) + "\n", encoding="utf-8", newline="\n")
        os.replace(temp, target)
    except OSError:
        return None
    return target


def clear() -> bool:
    """Remove the mark; True when a mark was there to remove. Never raises."""
    try:
        paths.long_safe(hold_path()).unlink()
    except OSError:
        return False
    return True
