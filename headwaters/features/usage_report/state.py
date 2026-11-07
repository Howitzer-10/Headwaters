"""HDW-508: what the usage report remembers about itself, in the ``kv`` table.

No migration: ``kv`` already exists for exactly this kind of small, schema-free setting
(``core.state_misc.kv_get``/``kv_set``), and a report's consent stamp, last-send time and survey
counters are not worth a table version.

Consent is stored with the **payload version the user previewed**, not as a bare boolean. If a
future release adds a field, the version changes and scheduled sending stops until the user
previews the new shape — the alternative is quietly sending bytes nobody agreed to.
"""

from __future__ import annotations

import time
from typing import Any

from headwaters.core.state import Database
from headwaters.core.state_misc import kv_delete, kv_get, kv_set

CONSENT_KEY = "report.consent"
LAST_SENT_KEY = "report.last_sent"
LAST_RESULT_KEY = "report.last_result"
SURVEY_KEY = "report.survey"
DAY = 86_400.0


def record_consent(db: Database, *, payload_version: int, now: float | None = None) -> None:
    kv_set(
        db,
        CONSENT_KEY,
        {
            "payload_version": int(payload_version),
            "granted_at": time.time() if now is None else float(now),
        },
    )


def consent(db: Database) -> dict[str, Any] | None:
    stored = kv_get(db, CONSENT_KEY)
    return stored if isinstance(stored, dict) else None


def consent_version(db: Database) -> int | None:
    stored = consent(db) or {}
    version = stored.get("payload_version")
    return int(version) if isinstance(version, int) and not isinstance(version, bool) else None


def clear(db: Database) -> None:
    """Forget consent. Called when the feature is switched off, so on means consented afresh."""
    kv_delete(db, CONSENT_KEY)


def last_sent(db: Database) -> float | None:
    stamp = kv_get(db, LAST_SENT_KEY)
    return float(stamp) if isinstance(stamp, int | float) and not isinstance(stamp, bool) else None


def record_send(db: Database, *, reason: str, now: float | None = None) -> None:
    moment = time.time() if now is None else float(now)
    kv_set(db, LAST_RESULT_KEY, {"reason": reason, "at": moment})
    if reason == "delivered":
        kv_set(db, LAST_SENT_KEY, moment)


def last_result(db: Database) -> dict[str, Any] | None:
    stored = kv_get(db, LAST_RESULT_KEY)
    return stored if isinstance(stored, dict) else None


def due(db: Database, *, interval_days: int, now: float | None = None) -> bool:
    """Whether a weekly report is owed: never sent, or the last success is too old."""
    sent = last_sent(db)
    if sent is None:
        return True
    moment = time.time() if now is None else float(now)
    return moment - sent >= max(1, int(interval_days)) * DAY


def survey_state(db: Database) -> dict[str, Any]:
    stored = kv_get(db, SURVEY_KEY)
    return stored if isinstance(stored, dict) else {}


def set_survey_state(db: Database, state: dict[str, Any]) -> None:
    kv_set(db, SURVEY_KEY, state)


__all__ = [
    "CONSENT_KEY",
    "LAST_RESULT_KEY",
    "LAST_SENT_KEY",
    "SURVEY_KEY",
    "clear",
    "consent",
    "consent_version",
    "due",
    "last_result",
    "last_sent",
    "record_consent",
    "record_send",
    "set_survey_state",
    "survey_state",
]
