"""Today's paid spend, read from the rows the router already writes (HDW-502).

``router_log`` stores one ``decisions`` row and one ``outcomes`` row per request, with
``outcomes.cost_usd`` priced from the catalog prices the call was actually made at. That is the
only spend figure the product keeps, so the daily cap is enforced on it and on nothing else.

A row whose model the catalog does not price is *counted*, never costed at zero (D7): a paid
request the catalog cannot price is a gap the cap cannot see, and the verdict says so instead of
pretending the request was free.
"""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.catalog.schema import Catalog
from headwaters.core.scope import PAID_TIERS
from headwaters.core.state import Database

DAY_SECONDS = 86_400.0

_REQUESTS_SQL = """
SELECT d.chosen AS model, m.tier AS tier, o.cost_usd AS cost_usd
FROM decisions AS d
JOIN outcomes AS o ON o.decision_id = d.id
LEFT JOIN models AS m ON m.id = d.chosen
WHERE d.ts >= ?
"""


@dataclass(frozen=True)
class Spend:
    """What the routed requests of the current day have cost so far."""

    total_usd: float = 0.0
    requests: int = 0
    unpriced: int = 0  # paid-tier requests with no catalog rate, so the cap cannot price them


def day_start(now: float) -> float:
    """The UTC midnight before ``now`` (the ledger reports on the same day basis)."""
    return now - (now % DAY_SECONDS)


def priced_ids(catalog: Catalog | None) -> frozenset[str]:
    """The model ids this catalog can price, using both rates the router prices with."""
    if catalog is None:
        return frozenset()
    return frozenset(
        entry.id
        for entry in catalog.models
        if entry.price_in is not None and entry.price_out is not None
    )


def daily_spend(db: Database, catalog: Catalog | None, *, now: float) -> Spend:
    """Every request since UTC midnight, split into priced spend and unpriced paid-tier requests."""
    with db.read() as c:
        rows = [dict(row) for row in c.execute(_REQUESTS_SQL, (day_start(now),)).fetchall()]
    priced = priced_ids(catalog)
    total = 0.0
    unpriced = 0
    for row in rows:
        model = row["model"]
        cost = 0.0 if row["cost_usd"] is None else float(row["cost_usd"])
        if model in priced:
            total += cost
        elif cost <= 0.0 and (row["tier"] is None or row["tier"] in PAID_TIERS):
            unpriced += 1
    return Spend(total, len(rows), unpriced)
