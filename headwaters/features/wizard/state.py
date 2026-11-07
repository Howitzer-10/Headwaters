"""Where a wizard run leaves a trace: one ``kv`` row per step, and the env diff it applied.

The records are deliberately *not* the source of truth. Every step recomputes the world when it is
shown or re-run, so a stale record can never claim a change that is not there, and skipping leaves
nothing but a sentence saying it was skipped. The one thing a record is needed for is undo: the
values the wizard overwrote have to survive a daemon restart to be restorable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from headwaters.core.state import Database
from headwaters.core.state_misc import kv_delete, kv_get, kv_set

__all__ = [
    "ENV_BACKUP_KEY",
    "ENV_FINGERPRINT_KEY",
    "PULL_LAST_KEY",
    "STATES",
    "STEP_IDS",
    "STEP_KEY",
    "StepRecord",
    "clear_env_backup",
    "env_backup",
    "record_env_backup",
    "record_step",
    "step_records",
]

STEP_KEY = "wizard.step.{n}"
ENV_BACKUP_KEY = "wizard.env.backup"
ENV_FINGERPRINT_KEY = "wizard.env.fingerprint"
PULL_LAST_KEY = "wizard.pull.last"

#: The eight steps, in order, by stable id (the ticket's numbering is 1-based).
STEP_IDS = (
    "detect",
    "models",
    "ides",
    "keys",
    "mode",
    "autostart",
    "report",
    "selftest",
)

STATES = ("done", "skipped")


@dataclass(frozen=True)
class StepRecord:
    """What the last run of one step said: ``done`` or ``skipped``, when, and one line why."""

    step: int
    state: str
    at: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "state": self.state, "at": self.at, "detail": self.detail}


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def record_step(db: Database, step: int, state: str, detail: str = "") -> StepRecord:
    """Record one step. ``state`` is ``done`` or ``skipped``; anything else is a bug."""
    if state not in STATES:
        raise ValueError(f"a step is done or skipped, not {state!r}")
    if not 1 <= step <= len(STEP_IDS):
        raise ValueError(f"there is no step {step}")
    record = StepRecord(step=step, state=state, at=_now(), detail=str(detail)[:400])
    kv_set(db, STEP_KEY.format(n=step), record.as_dict())
    return record


def step_records(db: Database) -> dict[int, StepRecord]:
    """Every recorded step, keyed by number. A malformed row is ignored, never trusted."""
    found: dict[int, StepRecord] = {}
    for n in range(1, len(STEP_IDS) + 1):
        raw = kv_get(db, STEP_KEY.format(n=n))
        if not isinstance(raw, dict):
            continue
        state = raw.get("state")
        if state not in STATES:
            continue
        found[n] = StepRecord(
            step=n,
            state=str(state),
            at=str(raw.get("at") or ""),
            detail=str(raw.get("detail") or ""),
        )
    return found


def record_env_backup(db: Database, previous: dict[str, str], fingerprint: str) -> None:
    """Remember the values that were overwritten so ``undo`` can put them back exactly."""
    kv_set(db, ENV_BACKUP_KEY, {"values": dict(previous), "at": _now()})
    kv_set(db, ENV_FINGERPRINT_KEY, fingerprint)


def env_backup(db: Database) -> dict[str, str] | None:
    """The overwritten values, or ``None`` when nothing is recorded (nothing to undo)."""
    raw = kv_get(db, ENV_BACKUP_KEY)
    if not isinstance(raw, dict):
        return None
    values = raw.get("values")
    if not isinstance(values, dict):
        return None
    return {str(k): str(v) for k, v in values.items()}


def clear_env_backup(db: Database) -> None:
    kv_delete(db, ENV_BACKUP_KEY)
    kv_delete(db, ENV_FINGERPRINT_KEY)
