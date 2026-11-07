"""The core half of the setup wizard ★ (HDW-505): find its plugin, never import it.

The wizard's eight steps live in ``headwaters/features/wizard``. Core owns the one thing every
surface must agree on — the feature id, the switch, and the fact that the wizard is a *page with
enumerated actions* — so the daemon can serve it without importing the ★ package (the
``core-is-a-leaf`` contract) and the gateway can serve it without importing a feature (the
``web-framework-only-in-gateway`` contract cuts both ways: a feature may not pull in a web
framework, and the gateway must not know a feature).

An absent or switched-off package is described, not hidden: ``wizard_page`` returns ``None`` and
the refusal sentence says which of the two it is, so the person sees why there is nothing to do
rather than a bare 404.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from headwaters.core.features import FeatureRegistry
from headwaters.core.plugins import WizardPlugin

#: The ``features:`` key and ``FeatureSpec.id`` of the wizard. Core reads it; only the ★ package
#: implements it. On by default: the wizard is the first-run surface, and the beta turns every
#: ★ feature on unless the person switches it off.
WIZARD_FLAG = "wizard"

WIZARD_TITLE = "Setup wizard"
WIZARD_INTERFACE = "WizardPlugin"

#: The eight steps, named here so a surface can count them without the package. The package's own
#: titles come from ``status()``; these are only the fallback.
WIZARD_STEP_COUNT = 8

NOT_INSTALLED = (
    "The ★ setup wizard package is not installed, so there is no wizard to show. "
    "Install the feature package, or check `headwaters features`."
)
SWITCHED_OFF = (
    "The setup wizard is switched off. Set `features.wizard: true` in rules.yaml, "
    "or run `headwaters features enable wizard`, and reload the daemon."
)

#: Where the page lives. One path, one GET: everything else is a POST that redirects back here.
WIZARD_PAGE = "/wizard"

#: Every write the wizard has, as path -> action name. Fixed and enumerated, the same way the
#: dashboard's four writes are: the gateway refuses anything not in this table, so the wizard is
#: never a generic "call anything" endpoint. The table lives here rather than in the gateway
#: because the feature renders the forms that post to it — one list, two readers, no drift.
WIZARD_PATHS: dict[str, str] = {
    "/wizard/env/apply": "env_apply",
    "/wizard/env/undo": "env_undo",
    "/wizard/pull": "pull",
    "/wizard/host/apply": "host_apply",
    "/wizard/mode/set": "mode_set",
    "/wizard/autostart/set": "autostart_set",
    "/wizard/report/consent": "report_consent",
    "/wizard/selftest": "selftest",
    "/wizard/mark": "mark",
}

#: The actions a step record may be set to. ``clear`` removes the record instead of setting one.
WIZARD_MARKS = ("done", "skipped", "clear")


def wizard_plugin(registry: FeatureRegistry) -> WizardPlugin | None:
    """The wizard plugin even while it is switched off, or ``None`` when none is installed."""
    return cast(WizardPlugin | None, registry.plugin(WIZARD_FLAG, WIZARD_INTERFACE))


def wizard_enabled(registry: FeatureRegistry) -> bool:
    """The switch: registered and on."""
    return registry.enabled(WIZARD_FLAG)


def wizard_status(registry: FeatureRegistry) -> dict[str, object]:
    """One description of the wizard for any surface, present or absent, on or off.

    ``discovered`` is ``False`` when no ★ package registered the interface; every other key is
    then the honest "there is no wizard" value rather than a missing one, so a caller can render
    the row unconditionally (the same shape ``report_status`` uses).
    """
    enabled = wizard_enabled(registry)
    plugin = wizard_plugin(registry)
    if plugin is None:
        return {
            "feature": WIZARD_FLAG,
            "title": WIZARD_TITLE,
            "discovered": False,
            "enabled": enabled,
            "steps": [],
            "step_count": WIZARD_STEP_COUNT,
            "note": NOT_INSTALLED,
        }
    described = dict(plugin.status())
    described.update({"feature": WIZARD_FLAG, "discovered": True, "enabled": enabled})
    described.setdefault("title", WIZARD_TITLE)
    described.setdefault("step_count", WIZARD_STEP_COUNT)
    if not enabled:
        described["note"] = SWITCHED_OFF
    return described


def wizard_page(
    registry: FeatureRegistry,
    *,
    csrf: str = "",
    step: int | None = None,
    error: str | None = None,
    notice: str | None = None,
) -> str | None:
    """The rendered page, or ``None`` when there is no package or the wizard is switched off.

    The page is built live: it reads hardware, the Ollama answer, the installer's plan and the
    recorded step state on every load. It writes nothing, which is why a reload is always safe.
    """
    plugin = wizard_plugin(registry)
    if plugin is None or not wizard_enabled(registry):
        return None
    return str(plugin.page(csrf=csrf, step=step, error=error, notice=notice))


def wizard_act(
    registry: FeatureRegistry, action: str, form: Mapping[str, str]
) -> dict[str, object]:
    """Run one enumerated action, refusing in words when there is nothing to act on.

    ``action`` is always one of the names the gateway's fixed path table supplies; an unknown
    name is refused rather than forwarded, so this is never a proxy for arbitrary work.
    """
    plugin = wizard_plugin(registry)
    if plugin is None:
        return {"ok": False, "step": 0, "notice": None, "error": NOT_INSTALLED}
    if not wizard_enabled(registry):
        return {"ok": False, "step": 0, "notice": None, "error": SWITCHED_OFF}
    reply = dict(plugin.act(action, form))
    reply.setdefault("ok", False)
    reply.setdefault("step", 0)
    reply.setdefault("notice", None)
    reply.setdefault("error", None)
    return reply


__all__ = [
    "NOT_INSTALLED",
    "SWITCHED_OFF",
    "WIZARD_FLAG",
    "WIZARD_INTERFACE",
    "WIZARD_MARKS",
    "WIZARD_PAGE",
    "WIZARD_PATHS",
    "WIZARD_STEP_COUNT",
    "WIZARD_TITLE",
    "wizard_act",
    "wizard_enabled",
    "wizard_page",
    "wizard_plugin",
    "wizard_status",
]
