"""HDW-501: rates come only from the active catalog, where a price without a date is invalid.

Nothing here invents a rate: a reference that is missing, unpriced or unnamed is reported as such
and the caller shows no estimate rather than a guess (D7).
"""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.catalog.schema import Catalog, ModelEntry


@dataclass(frozen=True)
class ReferenceRate:
    """USD per million tokens for the model the counterfactual prices against."""

    model: str
    price_in: float
    price_out: float
    price_date: str | None
    source: str  # "rules" when the user named it, "catalog" when it was chosen for them


def has_rate(entry: ModelEntry) -> bool:
    """True when the catalog carries both sides of the price."""
    return entry.price_in is not None and entry.price_out is not None


def find_entry(catalog: Catalog, name: str) -> ModelEntry | None:
    """The catalog entry for an id or one of its aliases, case-insensitively."""
    wanted = name.strip().lower()
    if not wanted:
        return None
    for entry in catalog.models:
        if wanted == entry.id.lower() or wanted in {alias.lower() for alias in entry.aliases}:
            return entry
    return None


def _rate(entry: ModelEntry, source: str) -> ReferenceRate:
    if entry.price_in is None or entry.price_out is None:  # guarded by the caller
        raise ValueError(f"{entry.id!r} has no catalog rate")
    return ReferenceRate(
        model=entry.id,
        price_in=entry.price_in,
        price_out=entry.price_out,
        price_date=entry.price_date,
        source=source,
    )


def _default_entry(catalog: Catalog) -> ModelEntry | None:
    """The catalog's most expensive priced model: the frontier reference, chosen from data.

    A catalog with no priced model (a local-only catalog) has no reference at all, which is the
    honest answer — not a zero rate that would make every request look free.
    """
    priced = [
        entry
        for entry in catalog.models
        if has_rate(entry) and (entry.price_in or 0.0) + (entry.price_out or 0.0) > 0
    ]
    if not priced:
        return None
    return max(priced, key=lambda entry: (entry.price_out or 0.0, entry.price_in or 0.0, entry.id))


def resolve_reference(configured: str, catalog: Catalog) -> tuple[ReferenceRate | None, str]:
    """The reference rate, and when it is ``None`` the reason a reader can act on."""
    name = configured.strip()
    if name:
        entry = find_entry(catalog, name)
        if entry is None:
            return None, f"reference model {name!r} is not in the catalog"
        if not has_rate(entry):
            return None, f"reference model {entry.id!r} has no dated catalog rate"
        return _rate(entry, "rules"), ""
    entry = _default_entry(catalog)
    if entry is None:
        return None, "the catalog has no priced model to measure against"
    return _rate(entry, "catalog"), ""
