"""Merge live discovery with the curated catalog and user overrides into ``ModelRecord``s.

Order (v3 §4.2): live -> curated catalog (matched by id, alias or source-supplied names) ->
user overrides from ``rules.yaml``. Live facts (context, tools, JSON, prices, size, arch) win over
the catalog; the catalog fills gaps and supplies scores, deprecations and ``retains_data``.
Models the catalog does not know get the conservative defaults and ``unscored=True``.
Scores are stored on a 0..100 scale (catalog 0..1 x 100), like the floors in ``rules.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from headwaters.core.catalog.schema import Catalog, ModelEntry
from headwaters.core.catalog.sources import LiveModel
from headwaters.core.rules import ModelOverride, Rules
from headwaters.core.state_models import ModelRow, ScoreRow

_TIER_FROM_HINT = {"paid": "paid", "free": "free_cloud"}


@dataclass
class ModelRecord:
    row: ModelRow
    seed_scores: dict[str, float] = field(default_factory=dict)
    override_scores: dict[str, float] = field(default_factory=dict)
    catalog_id: str | None = None

    def score_rows(self) -> list[ScoreRow]:
        tasks = sorted(set(self.seed_scores) | set(self.override_scores))
        return [
            ScoreRow(
                model_id=self.row.id,
                task=t,
                seed=self.seed_scores.get(t),
                override=self.override_scores.get(t),
            )
            for t in tasks
        ]


class CatalogLookup:
    """Case-insensitive lookup by id/alias; Ollama ``:latest`` is treated as the bare name."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._by_name: dict[str, ModelEntry] = {}
        for entry in catalog.models:
            for name in (entry.id, *entry.aliases):
                self._by_name.setdefault(name.lower(), entry)
        self._by_id = {m.id: m for m in catalog.models}

    def find(self, names: list[str]) -> ModelEntry | None:
        for name in names:
            key = name.lower()
            for candidate in (key, key.removesuffix(":latest")):
                if candidate in self._by_name:
                    return self._by_name[candidate]
        return None

    def get(self, model_id: str) -> ModelEntry | None:
        return self._by_id.get(model_id)


def _scores_100(entry_scores: dict[str, float | None]) -> dict[str, float]:
    return {t: round(v * 100, 3) for t, v in entry_scores.items() if v is not None}


def _find_override(rules: Rules, names: list[str]) -> ModelOverride | None:
    wanted = {n.lower() for n in names}
    for key, override in rules.models.overrides.items():
        if key.lower() in wanted:
            return override
    return None


def _apply_override(record: ModelRecord, override: ModelOverride) -> None:
    changes: dict[str, object] = {}
    if override.tier is not None:
        changes["tier"] = override.tier
    if override.tools_ok is not None:
        changes["tools_ok"] = override.tools_ok
    if override.json_ok is not None:
        changes["json_ok"] = override.json_ok
    if override.ctx is not None:
        changes["ctx"] = override.ctx
    if changes:
        record.row = replace(record.row, **changes)  # type: ignore[arg-type]
    record.override_scores.update({t: float(s) for t, s in override.scores.items()})


def _from_live(
    item: LiveModel, entry: ModelEntry | None, catalog: Catalog, now: float
) -> ModelRecord:
    defaults = catalog.unknown_model_defaults
    if entry is not None:
        return ModelRecord(
            ModelRow(
                id=item.id,
                tier=item.tier,
                provider=item.provider,
                modalities=item.modalities or entry.modalities,
                tools_ok=item.tools_ok if item.tools_ok is not None else entry.tools_ok,
                json_ok=item.json_ok if item.json_ok is not None else entry.json_ok,
                reasoning_ok=(
                    item.reasoning_ok if item.reasoning_ok is not None else entry.reasoning_ok
                ),
                ctx=item.ctx or entry.ctx,
                price_in=item.price_in if item.price_in is not None else entry.price_in,
                price_out=item.price_out if item.price_out is not None else entry.price_out,
                size_gb=item.size_gb or entry.size_gb,
                arch=item.arch or (entry.arch.model_dump() if entry.arch else None),
                source=item.source,
                last_seen=now,
                deprecated_by=entry.deprecated_by,
                retains_data=entry.retains_data or bool(item.retains_data),
                warm=item.warm,
                size_vram_gb=item.size_vram_gb,
            ),
            _scores_100(entry.scores.model_dump()),
            catalog_id=entry.id,
        )
    return ModelRecord(
        ModelRow(
            id=item.id,
            tier=item.tier,
            provider=item.provider,
            modalities=item.modalities,
            tools_ok=item.tools_ok if item.tools_ok is not None else defaults.tools_ok,
            json_ok=item.json_ok if item.json_ok is not None else defaults.json_ok,
            reasoning_ok=item.reasoning_ok,
            ctx=item.ctx,  # left unknown: CandidateBuilder flags the default as assumed (F18)
            price_in=item.price_in,
            price_out=item.price_out,
            size_gb=item.size_gb,
            arch=item.arch,
            source=item.source,
            last_seen=now,
            unscored=True,
            retains_data=bool(item.retains_data),
            warm=item.warm,
            size_vram_gb=item.size_vram_gb,
        ),
        _scores_100(defaults.scores.model_dump()),
    )


def _from_catalog_only(entry: ModelEntry, tier: str, now: float) -> ModelRecord:
    return ModelRecord(
        ModelRow(
            id=entry.id,
            tier=tier,
            provider=entry.provider,
            modalities=entry.modalities,
            tools_ok=entry.tools_ok,
            json_ok=entry.json_ok,
            reasoning_ok=entry.reasoning_ok,
            ctx=entry.ctx,
            price_in=entry.price_in,
            price_out=entry.price_out,
            size_gb=entry.size_gb,
            arch=entry.arch.model_dump() if entry.arch else None,
            source="catalog",
            last_seen=now,
            deprecated_by=entry.deprecated_by,
            retains_data=entry.retains_data,
        ),
        _scores_100(entry.scores.model_dump()),
        catalog_id=entry.id,
    )


def merge_models(
    live: list[LiveModel], catalog: Catalog, rules: Rules, now: float
) -> list[ModelRecord]:
    lookup = CatalogLookup(catalog)
    records: dict[str, ModelRecord] = {}
    matched: set[str] = set()
    for item in live:
        entry = lookup.find([item.id, *item.match_names])
        if entry is not None:
            matched.add(entry.id)
        records.setdefault(item.id, _from_live(item, entry, catalog, now))
    # Curated cloud entries with no live counterpart (e.g. the user's own paid keys).
    for entry in catalog.models:
        if entry.id in matched or entry.id in records:
            continue
        tier = next((_TIER_FROM_HINT[h] for h in entry.tier_hints if h in _TIER_FROM_HINT), None)
        if tier is not None:  # local models only count once installed
            records[entry.id] = _from_catalog_only(entry, tier, now)
    for record in records.values():
        names = [record.row.id]
        if record.catalog_id:
            names.append(record.catalog_id)
            curated = lookup.get(record.catalog_id)
            names.extend(curated.aliases if curated else [])
        override = _find_override(rules, names)
        if override is not None:
            _apply_override(record, override)
    return sorted(records.values(), key=lambda r: r.row.id)


__all__ = ["CatalogLookup", "ModelRecord", "merge_models"]
