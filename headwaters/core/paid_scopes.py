"""The core half of the paid-scopes ★ (HDW-502): look its plugins up, never import them.

Core owns the paid-tier switch (``core.scope``) and the policy ladder. The ★ feature owns the
daily paid-tier budget and the handback advice. The two meet here, and only here, through the plugin
registry, so a machine without the feature finds ``None`` and the paid tier behaves exactly as
the ladder built it (the accept-when of HDW-502).
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from headwaters.core.features import FeatureRegistry
from headwaters.core.plugins import AdvicePlugin, BudgetPlugin
from headwaters.core.scope import PAID_TIERS, ScopeDecision

__all__ = ["advice_plugin", "budget_plugin", "withhold_paid"]


def budget_plugin(registry: FeatureRegistry) -> BudgetPlugin | None:
    """The first enabled ``BudgetPlugin``, or ``None`` when no feature provides one."""
    found = registry.plugins_for("BudgetPlugin")
    return cast(BudgetPlugin, found[0]) if found else None


def advice_plugin(registry: FeatureRegistry) -> AdvicePlugin | None:
    """The first enabled ``AdvicePlugin``, or ``None`` when no feature provides one."""
    found = registry.plugins_for("AdvicePlugin")
    return cast(AdvicePlugin, found[0]) if found else None


def withhold_paid(scope: ScopeDecision, reason: str) -> ScopeDecision:
    """``scope`` with the paid tiers removed and ``reason`` recorded for the decision.

    The reason travels into ``RouteDecision.reasons`` (``decide`` copies the scope's), so an
    answer served by a free or local model says why the paid tier it would have used was not.
    """
    return replace(
        scope,
        tiers=frozenset(scope.tiers - PAID_TIERS),
        reasons=(*scope.reasons, reason),
    )
