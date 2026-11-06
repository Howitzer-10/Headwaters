"""HDW-501: the ledger ★ — what requests cost, and what the reference model would have cost.

The router already stores one ``decisions`` row and one ``outcomes`` row per request: surface,
scope, chosen model, status, token counts and a ``cost_usd`` priced from the catalog. The ledger
reads those two tables and adds the one number they do not carry — the *counterfactual*, priced
from the same token counts at a dated rate from the active catalog (``report.py``).

That figure is an estimate of what a different model would have charged, never money in hand, so
every string this feature emits says so, and anything it cannot price is left out of the total
and counted instead of being costed at zero (D7).

This package registers nothing by import side effect. Core finds it by name through
``discover_features`` and calls ``register``, and reaches it afterwards only through the plugin
registry, so deleting the folder leaves a working system, and ``features: {ledger: false}``
leaves a working system without the figure.
"""

from __future__ import annotations

from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.plugins import CURRENT_API_VERSION, DashboardPlugin
from headwaters.features.ledger.report import render_summary

FEATURE_ID = "ledger"
TITLE = "Ledger and savings"
DEFAULT = True

FEATURE_SPEC = FeatureSpec(
    id=FEATURE_ID,
    star=True,
    title=TITLE,
    interfaces=("DashboardPlugin",),
    default_enabled=DEFAULT,
)


class Ledger(DashboardPlugin):
    """Prices the counterfactual for the routed requests in the state database.

    The plugin keeps the registry, not the services: the engine is rebuilt when rules change, and
    a plugin that cached a database handle, catalog or rules object would keep serving the stale
    one. Services are read at render time, so refreshing them is enough.
    """

    api_version = CURRENT_API_VERSION

    def __init__(self, registry: FeatureRegistry) -> None:
        self._registry = registry

    def render_summary(self) -> dict[str, object]:
        return render_summary(self._registry.services)


def register(registry: FeatureRegistry) -> None:
    registry.register(FEATURE_SPEC, {"DashboardPlugin": Ledger(registry)})


__all__ = ["DEFAULT", "FEATURE_ID", "FEATURE_SPEC", "TITLE", "Ledger", "register"]
