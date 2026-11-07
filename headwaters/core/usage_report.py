"""The core half of the opt-in usage report ★ (HDW-508): look its plugin up, never import it.

Core owns nothing about the report's contents. It owns the one thing every surface must agree on
— the feature id and the switch — so the CLI, the status page, ``headwaters doctor`` and the
daemon can describe the report without importing ``headwaters.features.usage_report`` (the
``core-is-a-leaf`` contract) and without a second copy of the flag name.

``report_status`` answers even when the ★ package is missing, which is the point: PRIVACY.md
promises the toggle is visible in ``headwaters doctor``, and a person cannot consent to a report
they cannot see. An absent package is reported as absent, never as "off" with no explanation.
"""

from __future__ import annotations

from typing import cast

from headwaters.core.features import FeatureRegistry
from headwaters.core.plugins import ReportPlugin

#: The ``features:`` key and ``FeatureSpec.id`` of the report. Core reads it; only the ★ package
#: implements it.
REPORT_FLAG = "usage_report"

#: Shown when the package is not discovered, so the row never disappears (HDW-508).
REPORT_TITLE = "Usage report (opt-in, off by default)"

REPORT_INTERFACE = "ReportPlugin"


def report_plugin(registry: FeatureRegistry) -> ReportPlugin | None:
    """The report plugin even while it is switched off, or ``None`` when none is installed."""
    return cast(ReportPlugin | None, registry.plugin(REPORT_FLAG, REPORT_INTERFACE))


def report_enabled(registry: FeatureRegistry) -> bool:
    """The switch: registered and on. Off by default in the ★ spec, whatever the user has set."""
    return registry.enabled(REPORT_FLAG)


def report_status(registry: FeatureRegistry) -> dict[str, object]:
    """One description of the report for any surface, present or absent, on or off.

    ``discovered`` is ``False`` when no ★ package registered the interface; every other key is
    then the honest "nothing can be built or sent" value rather than a missing one, so a caller
    can render the row unconditionally.
    """
    enabled = report_enabled(registry)
    plugin = report_plugin(registry)
    if plugin is None:
        return {
            "feature": REPORT_FLAG,
            "title": REPORT_TITLE,
            "discovered": False,
            "enabled": enabled,
            "endpoint_set": False,
            "sends": 0,
            "last_sent": None,
            "next_due": None,
            "payload_version": None,
            "note": "the ★ report package is not installed, so no payload can be built or sent",
        }
    described = dict(plugin.status())
    described.update({"feature": REPORT_FLAG, "discovered": True, "enabled": enabled})
    described.setdefault("title", REPORT_TITLE)
    return described


def preview_report(registry: FeatureRegistry) -> dict[str, object] | None:
    """The exact payload a send would post, or ``None`` when the package is absent."""
    plugin = report_plugin(registry)
    return None if plugin is None else dict(plugin.preview())


def send_due_report(
    registry: FeatureRegistry, *, now: float | None = None
) -> dict[str, object] | None:
    """Periodic hook for the daemon: the plugin decides whether anything is due.

    ``None`` means there is nothing to do (no package at all); the plugin itself refuses to send
    unless the user turned the report on.
    """
    plugin = report_plugin(registry)
    if plugin is None:
        return None
    return dict(plugin.send_due(now=now))


def send_report_now(registry: FeatureRegistry, *, now: float | None = None) -> dict[str, object]:
    """One-shot send for ``headwaters report send``: does not depend on the flag being on."""
    plugin = report_plugin(registry)
    if plugin is None:
        return {"sent": False, "reason": "not_installed"}
    return dict(plugin.send_now(now=now))


__all__ = [
    "REPORT_FLAG",
    "REPORT_INTERFACE",
    "REPORT_TITLE",
    "preview_report",
    "report_enabled",
    "report_plugin",
    "report_status",
    "send_due_report",
    "send_report_now",
]
