"""The dashboard's wizard backend (HDW-505): the one page and the one action table.

The gateway knows a protocol, not the ★ package (``web-framework-only-in-gateway``): this module
hands it ``page`` and ``act``, and both go through ``headwaters.core.wizard``, which finds the
plugin without importing a feature. An absent or switched-off package still answers — with the
sentence that says which of the two it is — so the link on the dashboard is never a bare 404.

Nothing here caches: the registry is a callable because the daemon rebuilds the engine behind it
when rules or the catalog change, and a plugin holding the old one would answer from stale rules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from html import escape

from headwaters.core.features import FeatureRegistry
from headwaters.core.wizard import (
    WIZARD_STEP_COUNT,
    WIZARD_TITLE,
    wizard_act,
    wizard_page,
    wizard_status,
)

__all__ = ["WizardBackend"]

_UNAVAILABLE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    "<title>{title}</title></head><body>"
    "<h1>{title}</h1><p>{note}</p>"
    "<p>The {count} steps are described on this page rather than hidden, so a person can see what "
    "setup would do. Nothing was changed.</p>"
    '<p><a href="/">Back to the dashboard</a></p>'
    "</body></html>"
)


class WizardBackend:
    """``page`` and ``act`` over the live registry — what ``build_status_page_app`` asks for."""

    def __init__(self, registry: Callable[[], FeatureRegistry]) -> None:
        self._registry = registry

    def page(
        self,
        *,
        csrf: str = "",
        step: int | None = None,
        error: str | None = None,
        notice: str | None = None,
    ) -> str:
        registry = self._registry()
        rendered = wizard_page(registry, csrf=csrf, step=step, error=error, notice=notice)
        return rendered if rendered is not None else _unavailable(wizard_status(registry))

    def act(self, action: str, form: Mapping[str, str]) -> Mapping[str, object]:
        return wizard_act(self._registry(), action, form)


def _unavailable(status: Mapping[str, object]) -> str:
    """The page for "no package" or "switched off": which one, and where the switch is."""
    return _UNAVAILABLE.format(
        title=escape(str(status.get("title") or WIZARD_TITLE)),
        note=escape(str(status.get("note") or "")),
        count=int(str(status.get("step_count") or WIZARD_STEP_COUNT)),
    )
