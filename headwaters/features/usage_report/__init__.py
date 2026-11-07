"""HDW-508: the opt-in usage report ★ — a preview, a switch, and a weekly aggregate.

A person can only consent to what they can see, so the order is fixed: ``payload`` builds the
exact bytes, ``preview`` shows them, and only then can the switch be turned on. Nothing here sends
while the feature is off (``default_enabled=False``), and ``send_now`` is a one-shot that works
with the switch off without turning it on — because "send this once" is not "send this weekly".

What leaves is counts. ``payload.FIELD_RULES`` is the allow-list, and ``check_payload`` enforces
it, so there is no field that could carry a prompt, an output, a file name, a path, a key or an
address. Core never imports this package: it reaches it through ``ReportPlugin``
(``headwaters/core/usage_report.py``), which is why deleting this folder leaves a working system
that says so in ``headwaters doctor``.
"""

from __future__ import annotations

import json
import time
from typing import Any

from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.plugins import CURRENT_API_VERSION, ReportPlugin
from headwaters.core.rules import ReportRules, Rules
from headwaters.core.state import Database
from headwaters.features.usage_report import send as sender
from headwaters.features.usage_report import state as report_state
from headwaters.features.usage_report.payload import (
    FIELD_RULES,
    PAYLOAD_VERSION,
    build,
    describe_shape,
)

FEATURE_ID = "usage_report"
TITLE = "Usage report (opt-in, off by default)"
DEFAULT = False

FEATURE_SPEC = FeatureSpec(
    id=FEATURE_ID,
    star=True,
    title=TITLE,
    interfaces=("ReportPlugin",),
    default_enabled=DEFAULT,
)


class UsageReport(ReportPlugin):
    """Builds the payload, describes the switch and sends only when every gate allows it.

    The plugin keeps the registry, not the services, for the reason the ledger does: rules are
    re-read while the process runs, and a cached endpoint would keep sending to the old one.
    """

    api_version = CURRENT_API_VERSION

    def __init__(self, registry: FeatureRegistry) -> None:
        self._registry = registry

    def _db(self) -> Database | None:
        db = self._registry.services.get("db")
        return db if isinstance(db, Database) else None

    def _settings(self) -> ReportRules:
        rules = self._registry.services.get("rules")
        return rules.report if isinstance(rules, Rules) else ReportRules()

    def _services(self) -> dict[str, Any]:
        services = dict(self._registry.services)
        services["registry"] = self._registry
        return services

    def payload(self) -> dict[str, Any]:
        return build(self._services())

    def status(self) -> dict[str, object]:
        db = self._db()
        settings = self._settings()
        enabled = self._registry.enabled(FEATURE_ID)
        consented = report_state.consent_version(db) if db is not None else None
        last_sent = report_state.last_sent(db) if db is not None else None
        sent_at = time.time()
        interval = settings.interval_days
        next_due = (
            None
            if last_sent is None
            else max(0.0, last_sent + interval * report_state.DAY - sent_at)
        )
        return {
            "feature": FEATURE_ID,
            "title": TITLE,
            "enabled": enabled,
            "discovered": True,
            "endpoint": settings.endpoint,
            "endpoint_set": bool(settings.endpoint),
            "interval_days": interval,
            "payload_version": PAYLOAD_VERSION,
            "consented_version": consented,
            "consent_current": consented == PAYLOAD_VERSION,
            "fields": len(FIELD_RULES),
            "last_sent": last_sent,
            "last_result": report_state.last_result(db) if db is not None else None,
            "next_due": next_due,
            "survey": self.survey(),
        }

    def preview(self) -> dict[str, object]:
        payload = self.payload()
        settings = self._settings()
        db = self._db()
        consented = report_state.consent_version(db) if db is not None else None
        gate = sender.refusal(
            settings.endpoint, payload_version=PAYLOAD_VERSION, consented_version=consented
        )
        return {
            "payload": payload,
            "json": json.dumps(payload, indent=2, sort_keys=True),
            "shape": describe_shape(),
            "endpoint": settings.endpoint,
            "endpoint_set": bool(settings.endpoint),
            "payload_version": PAYLOAD_VERSION,
            "consented_version": consented,
            "gate": gate,
        }

    def send_due(self, *, now: float | None = None) -> dict[str, object]:
        if not self._registry.enabled(FEATURE_ID):
            return sender.result(sender.OFF, detail="the usage report is switched off")
        db = self._db()
        if db is None:
            return sender.result(sender.UNAVAILABLE, detail="no state database")
        if not report_state.due(db, interval_days=self._settings().interval_days, now=now):
            return sender.result(sender.NOT_DUE, detail="the interval has not elapsed")
        return self._send(now)

    def send_now(self, *, now: float | None = None) -> dict[str, object]:
        return self._send(now)

    def survey(self) -> dict[str, object] | None:
        db = self._db()
        if db is None:
            return None
        from headwaters.features.usage_report.survey import prompt

        return prompt(db)

    def _send(self, now: float | None) -> dict[str, object]:
        db = self._db()
        settings = self._settings()
        moment = time.time() if now is None else float(now)
        payload = self.payload()
        outcome = sender.send(
            payload,
            endpoint=settings.endpoint,
            consented_version=report_state.consent_version(db) if db is not None else None,
            timeout_s=settings.timeout_s,
        )
        if db is not None:
            report_state.record_send(db, reason=str(outcome["reason"]), now=moment)
        return outcome


def register(registry: FeatureRegistry) -> None:
    registry.register(FEATURE_SPEC, {"ReportPlugin": UsageReport(registry)})


__all__ = ["DEFAULT", "FEATURE_ID", "FEATURE_SPEC", "TITLE", "UsageReport", "register"]
