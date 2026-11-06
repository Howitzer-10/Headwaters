"""HDW-501: what requests cost, and what the reference model would have cost for the same tokens.

The actual spend is already durable (``outcomes.cost_usd``, priced by the router). This module
reads it together with ``decisions`` and adds the counterfactual, using the one token-cost formula
in ``router_log`` so the two numbers can never disagree about arithmetic.

Three rules the figures obey (D7):

* every figure is labelled an estimate of what another model *would* have charged;
* a request with no comparable rate (unknown model, missing outcome, missing token counts, or a
  paid model the catalog does not rate — whose stored spend is indistinguishable from free) is left
  out of the money total and counted, and the label names the models, so a total reads
  "across N of M requests";
* nothing is rounded finer than a micro-dollar, and no rate is ever defaulted.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from headwaters.core.catalog.schema import Catalog
from headwaters.core.router_log import tokens_cost
from headwaters.core.rules import LedgerRules, Rules
from headwaters.core.state import Database
from headwaters.features.ledger.rates import ReferenceRate, priced_models, resolve_reference

FEATURE = "ledger"
DAY_BASIS = "utc"
DAY = 86_400.0
WEEK_DAYS = 7
MODEL_UNROUTED = "unrouted"
# A request the user paid nothing for: served on their own hardware or on a free-cloud model.
# ``free_cloud`` is a runtime tier, not a catalog one, so both spellings are counted.
FREE_TIERS = frozenset({"local", "free", "free_cloud"})
# Micro-dollars: finer than any catalog rate (USD per million tokens) can justify.
_USD_PLACES = 6

_REQUESTS_SQL = """
SELECT d.id AS decision_id, d.ts AS ts, d.chosen AS model,
       COALESCE(m.tier, 'unknown') AS tier,
       o.status AS status, o.tokens_in AS tokens_in, o.tokens_out AS tokens_out,
       o.cost_usd AS cost_usd
FROM decisions d
LEFT JOIN outcomes o ON o.decision_id = d.id
LEFT JOIN models m ON m.id = d.chosen
WHERE d.ts >= ?
ORDER BY d.ts, d.id
"""


@dataclass(frozen=True)
class RequestRow:
    """One routed request as the two tables remember it."""

    decision_id: int
    ts: float
    model: str | None
    tier: str
    status: str | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None


def read_requests(db: Database, *, since: float) -> list[RequestRow]:
    """The requests routed at or after ``since`` (epoch seconds), oldest first."""
    with db.read() as conn:
        return [
            RequestRow(
                decision_id=int(row["decision_id"]),
                ts=float(row["ts"]),
                model=row["model"],
                tier=str(row["tier"]),
                status=row["status"],
                tokens_in=row["tokens_in"],
                tokens_out=row["tokens_out"],
                cost_usd=row["cost_usd"],
            )
            for row in conn.execute(_REQUESTS_SQL, (since,))
        ]


def _rateable(row: RequestRow, rated: Collection[str]) -> bool:
    """Whether a stored spend means what it says.

    ``outcomes.cost_usd`` is ``0.0`` both for a model priced at zero and for a model with no rate
    at all (``router_log.cost_usd`` substitutes 0 for a missing price), so the column alone cannot
    answer this. A free tier is zero for real and counts — that is the product's whole win and its
    requests must still produce savings. Anything else counts only when the catalog actually rates
    the model, which is why the catalog's rated set is consulted rather than assumed.
    """
    if row.tier in FREE_TIERS:
        return True
    model = (row.model or "").strip().lower()
    return bool(model) and model in rated


def _priceable(row: RequestRow, rated: Collection[str]) -> bool:
    """Both sides must be known: a spend the router could not price and a counterfactual with no
    token counts are equally unusable, so both are reported as unpriced rather than as zero."""
    return (
        row.cost_usd is not None
        and row.tokens_in is not None
        and row.tokens_out is not None
        and _rateable(row, rated)
    )


def _counterfactual(row: RequestRow, rate: ReferenceRate) -> float:
    return tokens_cost(rate.price_in, rate.price_out, row.tokens_in or 0, row.tokens_out or 0)


def _usd(value: float) -> float:
    return round(value, _USD_PLACES)


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(100.0 * part / whole, 1)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


def _by_tier(rows: Sequence[RequestRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.tier] = counts.get(row.tier, 0) + 1
    return dict(sorted(counts.items()))


def bucket(
    rows: Sequence[RequestRow], rate: ReferenceRate, rated: Collection[str]
) -> dict[str, object]:
    """Totals for one group of requests, with unpriced ones counted and excluded from the money."""
    priced = [row for row in rows if _priceable(row, rated)]
    actual = sum(row.cost_usd or 0.0 for row in priced)
    counterfactual = sum(_counterfactual(row, rate) for row in priced)
    free = sum(1 for row in rows if row.tier in FREE_TIERS)
    return {
        "requests": len(rows),
        "priced_requests": len(priced),
        "unpriced_requests": len(rows) - len(priced),
        "free_or_local_requests": free,
        "free_or_local_pct": _share(free, len(rows)),
        "actual_usd": _usd(actual),
        "counterfactual_usd": _usd(counterfactual),
        "estimated_savings_usd": _usd(counterfactual - actual),
        "by_tier": _by_tier(rows),
    }


def _unpriced_models(rows: Sequence[RequestRow], rated: Collection[str]) -> list[str]:
    """Which models were left out, so a reader can see the gap rather than trust the total."""
    return sorted({row.model or MODEL_UNROUTED for row in rows if not _priceable(row, rated)})


def _label(rate: ReferenceRate, totals: Mapping[str, object], unpriced: Sequence[str] = ()) -> str:
    """The sentence a person reads, which must carry the estimate's basis and its coverage."""
    when = f"catalog rates dated {rate.price_date}" if rate.price_date else "undated catalog rates"
    priced = int(totals["priced_requests"])  # type: ignore[arg-type]
    requests = int(totals["requests"])  # type: ignore[arg-type]
    if requests == 0:
        return f"No routed request in the window; nothing to price against {rate.model} ({when})."
    missing = requests - priced
    tail = ""
    if missing:
        tail = f"; {missing} had no comparable rate and are left out of the total"
        if unpriced:
            tail += f" (no catalog rate for {', '.join(unpriced)})"
    return (
        f"Estimate, not money in hand: what {rate.model} would have charged for the same tokens "
        f"at {when}. The estimate covers {priced} of {requests} requests{tail}."
    )


def unavailable(reason: str) -> dict[str, object]:
    """No estimate: say why, and never substitute a zero."""
    return {
        "available": False,
        "feature": FEATURE,
        "kind": "estimate",
        "reference": None,
        "reason": reason,
        "label": f"Savings: {reason}. No estimate is shown rather than a guess.",
    }


def build_report(
    rows: Sequence[RequestRow],
    rate: ReferenceRate,
    *,
    now: float,
    window_days: int,
    rated: Collection[str],
    week_days: int = WEEK_DAYS,
) -> dict[str, object]:
    """The whole summary: totals for the window, the last week, and a breakdown by day and model.

    ``rows`` are already limited to the window by ``read_requests``; ``rated`` is the catalog's
    rated ids and aliases (``rates.priced_models``), which the money rules need.
    """
    week_rows = [row for row in rows if row.ts >= now - week_days * DAY]
    by_day: dict[str, list[RequestRow]] = {}
    by_model: dict[str, list[RequestRow]] = {}
    for row in rows:
        by_day.setdefault(_day(row.ts), []).append(row)
        by_model.setdefault(row.model or MODEL_UNROUTED, []).append(row)
    totals = bucket(rows, rate, rated)
    models = [{"model": model, **bucket(group, rate, rated)} for model, group in by_model.items()]
    models.sort(key=lambda entry: (-float(entry["estimated_savings_usd"]), str(entry["model"])))
    return {
        "available": True,
        "feature": FEATURE,
        "kind": "estimate",
        "reference": {
            "model": rate.model,
            "price_in_usd_per_mtok": rate.price_in,
            "price_out_usd_per_mtok": rate.price_out,
            "price_date": rate.price_date,
            "source": rate.source,
        },
        "window_days": window_days,
        "week_days": week_days,
        "day_basis": DAY_BASIS,
        "totals": totals,
        "week": bucket(week_rows, rate, rated),
        "by_day": [
            {"day": day, **bucket(group, rate, rated)} for day, group in sorted(by_day.items())
        ],
        "by_model": models,
        "label": _label(rate, totals, _unpriced_models(rows, rated)),
    }


def render_summary(
    services: Mapping[str, object], *, now: float | None = None
) -> dict[str, object]:
    """``DashboardPlugin.render_summary``: the summary, or a stated reason there is none.

    Everything it needs comes from the registry's services, and a missing one is reported rather
    than worked around: a plugin that silently opened its own state database would answer from a
    different install than the one the caller is looking at.
    """
    db = services.get("db")
    catalog = services.get("catalog")
    rules = services.get("rules")
    settings = rules.ledger if isinstance(rules, Rules) else LedgerRules()
    if not isinstance(db, Database):
        return unavailable("the state database is not wired to the feature registry")
    if not isinstance(catalog, Catalog):
        return unavailable("the catalog is not wired to the feature registry")
    rate, reason = resolve_reference(settings.reference_model, catalog)
    if rate is None:
        return unavailable(reason)
    moment = time.time() if now is None else now
    try:
        rows = read_requests(db, since=moment - settings.window_days * DAY)
    except sqlite3.Error as exc:
        return unavailable(f"the state database could not be read ({exc})")
    return build_report(
        rows,
        rate,
        now=moment,
        window_days=settings.window_days,
        rated=priced_models(catalog),
    )
