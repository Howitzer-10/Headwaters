"""The paid-scopes ★: paid tiers, a daily paid-tier budget and handback advice (HDW-502).

``core.scope`` has always excluded the paid tiers through
``PAID_SCOPES_FEATURE_ID``/``paid_scopes_available``; until this package existed there was no
feature to answer to that id, so the exclusion was unconditional. Registering here is what makes
``paid_scopes`` a real, switchable feature: with it discovered and enabled, ``auto``/``cloud``
may use the paid tiers and the two plugins below police them; with the flag off (or the package
absent) nothing changes at all.

The plugins hold the registry, not a copy of the rules or the database, because the engine
deliberately keeps no rules (the daemon rebuilds them on change) — the same shape as the ledger.
"""

from __future__ import annotations

from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.scope import PAID_SCOPES_FEATURE_ID
from headwaters.features.paid_scopes.advice import Advisory
from headwaters.features.paid_scopes.budget import Budget

FEATURE_ID = PAID_SCOPES_FEATURE_ID  # one id, shared with the core gate that asks for it
TITLE = "Paid scopes, daily budget and handback advice"

FEATURE_SPEC = FeatureSpec(
    id=FEATURE_ID,
    star=True,
    title=TITLE,
    interfaces=("BudgetPlugin", "AdvicePlugin"),
    default_enabled=True,
)


def register(registry: FeatureRegistry) -> None:
    registry.register(
        FEATURE_SPEC,
        {"BudgetPlugin": Budget(registry), "AdvicePlugin": Advisory(registry)},
    )


__all__ = ["FEATURE_ID", "FEATURE_SPEC", "TITLE", "register"]
