"""HDW-502: the daily paid-tier budget, checked before a paid-tier call is made.

``rules.budget.paid_daily_usd`` was declared and never read: the ladder would route to a paid
model however much the day had already cost. The cap is enforced here, on the stored
``outcomes.cost_usd`` of the day so far plus this request's own estimate — priced from the same
catalog rates the router uses, so the two figures cannot drift apart.

The cap is a ceiling, not a target: a request that would take the day *over* it is refused, and
one that lands exactly on it goes ahead. ``paid_daily_usd: 0`` means no paid spend today.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from headwaters.core.features import FeatureRegistry
from headwaters.core.plugins import CURRENT_API_VERSION, BudgetCheck, BudgetPlugin
from headwaters.core.rules import Rules
from headwaters.core.state import Database
from headwaters.features.paid_scopes.spend import Spend, daily_spend


class Budget(BudgetPlugin):
    """The daily cap, read from the live rules and the state database at call time."""

    api_version = CURRENT_API_VERSION

    def __init__(self, registry: FeatureRegistry, clock: Callable[[], float] | None = None) -> None:
        self._registry = registry
        self._clock = clock or time.time

    def check(self, *, model: str, tier: str, estimated_usd: float) -> BudgetCheck:
        services = self._registry.services
        rules = services.get("rules")
        db = services.get("db")
        if not isinstance(rules, Rules) or not isinstance(db, Database):
            # No live rules means no cap was configured; no state database means no spend was
            # ever recorded to measure against. Neither is a refusal.
            return BudgetCheck(True)
        cap = rules.budget.paid_daily_usd
        spend = daily_spend(db, services.get("catalog"), now=self._clock())  # type: ignore[arg-type]
        spent = round(spend.total_usd, 4)
        if spend.total_usd + max(estimated_usd, 0.0) <= cap:
            return BudgetCheck(True, spent, cap)
        return BudgetCheck(False, spent, cap, reason=reason_for(spend, cap, estimated_usd, model))


def reason_for(spend: Spend, cap: float, estimated_usd: float, model: str) -> str:
    """The line recorded in the decision's reasons when the cap withholds the paid tier."""
    parts = [
        f"paid-tier budget: ${spend.total_usd:.2f} of ${cap:.2f} spent today (UTC)",
        f"{model} is estimated at ${max(estimated_usd, 0.0):.2f}",
    ]
    if spend.unpriced:
        parts.append(
            f"{spend.unpriced} paid-tier request(s) today have no catalog rate and are not counted"
        )
    return "; ".join(parts)
