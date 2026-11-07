"""HDW-508: the product-feedback prompt, shown at most twice and never again after a dismissal.

"Active day" is a day the router actually served a request — a distinct UTC date in
``decisions.ts`` — not a day the process happened to be running, because ``headwaters`` is a daemon
that runs every day whether or not the user is working.

The prompt asks nothing and stores no answer: it points at ``headwaters feedback``, which prints a
prefilled message for the user to send themselves. There is no collector to post to (the ticket
has no endpoint), so a survey that silently posted answers would be a network call this project
has promised not to make.
"""

from __future__ import annotations

from typing import Any

from headwaters.core.state import Database
from headwaters.features.usage_report.state import set_survey_state, survey_state

#: The two thresholds, in active days. Two prompts, ever.
STAGES: tuple[int, ...] = (7, 30)
MAX_PROMPTS = len(STAGES)

DISMISSED = "dismissed"
ANSWERED = "answered"
SHOWN = "shown"


def active_days(db: Database) -> int:
    """Distinct UTC dates with at least one routed request, over all recorded history."""
    with db.read() as conn:
        row = conn.execute("SELECT COUNT(DISTINCT date(ts, 'unixepoch')) FROM decisions").fetchone()
    return int(row[0] or 0)


def _asked(state: dict[str, Any]) -> list[int]:
    asked: list[int] = []
    for key in (SHOWN, ANSWERED):
        values = state.get(key)
        if isinstance(values, list):
            asked.extend(int(v) for v in values if isinstance(v, int) and not isinstance(v, bool))
    return asked


def prompt(db: Database) -> dict[str, Any] | None:
    """The prompt that is owed now, or ``None``. Never creates state; a caller records the show."""
    state = survey_state(db)
    if state.get(DISMISSED):
        return None
    asked = _asked(state)
    if len(asked) >= MAX_PROMPTS:
        return None
    days = active_days(db)
    for stage in STAGES:
        if days >= stage and stage not in asked:
            return {
                "stage": stage,
                "active_days": days,
                "remaining": MAX_PROMPTS - len(asked),
                "question": (
                    f"You have used the router on {days} active days. "
                    "Would you tell us what is working and what is not?"
                ),
                "how": "Run `headwaters feedback` — it prints a short form you send yourself.",
            }
    return None


def _append(state: dict[str, Any], key: str, stage: int) -> None:
    values = state.get(key)
    listed = list(values) if isinstance(values, list) else []
    if stage not in listed:
        listed.append(stage)
    state[key] = listed


def record_shown(db: Database, stage: int) -> None:
    """Count a prompt as one of the two. Called when the prompt is actually displayed."""
    state = survey_state(db)
    _append(state, SHOWN, stage)
    set_survey_state(db, state)


def record_answered(db: Database, stage: int) -> None:
    state = survey_state(db)
    _append(state, ANSWERED, stage)
    set_survey_state(db, state)


def dismiss(db: Database) -> None:
    """Never ask again, on this install, for any stage."""
    state = survey_state(db)
    state[DISMISSED] = True
    set_survey_state(db, state)


def reset(db: Database) -> None:
    set_survey_state(db, {})


__all__ = [
    "ANSWERED",
    "DISMISSED",
    "MAX_PROMPTS",
    "SHOWN",
    "STAGES",
    "active_days",
    "dismiss",
    "prompt",
    "record_answered",
    "record_shown",
    "reset",
]
