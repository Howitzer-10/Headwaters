"""Model discovery: gather live sources, merge with the catalog, persist to SQLite.

Every source is optional and failures degrade gracefully: an unreachable source adds a warning
and its models are simply not refreshed (they are NOT deleted, so a brief Ollama outage does
not wipe the picker). Models from a source that answered but no longer lists them are removed.
Offline modes: ``airplane`` and the local-only lock block every non-loopback source; Ollama
(loopback) still runs. No prompt data is sent anywhere.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from headwaters.core.catalog import updater
from headwaters.core.catalog.http import HttpClient, SourceError, UrllibHttp
from headwaters.core.catalog.merge import ModelRecord, merge_models
from headwaters.core.catalog.schema import Catalog
from headwaters.core.catalog.sources import (
    LiveModel,
    ollama_models,
    openai_compatible_models,
    opencode_config_paths,
    opencode_models,
    openrouter_models,
)
from headwaters.core.rules import Rules
from headwaters.core.state import Database
from headwaters.core.state_misc import kv_get, kv_set
from headwaters.core.state_models import delete_models, list_models, set_score, upsert_model

KV_LAST_REFRESH = "models.last_refresh"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"


@dataclass
class RefreshResult:
    models: int = 0
    unscored: int = 0
    removed: int = 0
    sources_ok: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False


def needs_refresh(db: Database, rules: Rules, now: float | None = None) -> bool:
    last = kv_get(db, KV_LAST_REFRESH)
    if not isinstance(last, int | float):
        return True
    return (time.time() if now is None else now) - float(last) >= rules.models.refresh_ttl_s


def _network_block(rules: Rules) -> str | None:
    if rules.airplane:
        return "airplane mode"
    if rules.private_lock or rules.default_scope == "local":
        return "local-only lock state"
    return None


def _gather(
    rules: Rules, http: HttpClient, env: Mapping[str, str], home: Path, result: RefreshResult
) -> dict[str, list[LiveModel]]:
    """Live models by source name. Only sources that answered appear in the result."""
    found: dict[str, list[LiveModel]] = {}
    blocked = _network_block(rules)

    try:
        models, warnings = ollama_models(http, rules.tiers.local.host)
        found["ollama"] = models
        result.warnings.extend(warnings)
    except SourceError as exc:
        result.warnings.append(f"ollama: {exc}")

    paths_ = opencode_config_paths(rules.models.opencode_config, env, home)
    oc, oc_warn = opencode_models(paths_, rules.tiers.free_cloud.opencode_free)
    found["opencode"] = oc
    result.warnings.extend(oc_warn)

    if blocked:
        result.warnings.append(f"cloud discovery skipped: {blocked}")
        return found

    try:
        found["openrouter"] = openrouter_models(http, env.get(OPENROUTER_KEY_ENV))
    except SourceError as exc:
        result.warnings.append(f"openrouter: {exc}")
    for endpoint in rules.models.openai_compatible:
        key = f"openai_compatible:{endpoint.name}"
        try:
            found[key] = openai_compatible_models(http, endpoint, env)
        except SourceError as exc:
            result.warnings.append(f"{endpoint.name}: {exc}")
    return found


def _persist(db: Database, records: list[ModelRecord], answered: set[str]) -> int:
    existing = list_models(db)
    keep = {r.row.id for r in records}
    # Remove only rows whose own source answered this time and no longer lists them.
    stale = [m.id for m in existing if m.id not in keep and m.source in answered]
    removed = delete_models(db, stale)
    for record in records:
        upsert_model(db, record.row)
        for score in record.score_rows():
            set_score(db, score)
    return removed


def refresh_models(
    db: Database,
    rules: Rules,
    catalog: Catalog,
    *,
    http: HttpClient | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    now: float | None = None,
) -> RefreshResult:
    """Discover, merge and persist. Never raises because a source is down."""
    result = RefreshResult()
    stamp = time.time() if now is None else now
    live = _gather(
        rules,
        http or UrllibHttp(),
        os.environ if env is None else env,
        home or Path.home(),
        result,
    )
    result.sources_ok = sorted(live)
    flat = [m for models in live.values() for m in models]
    records = merge_models(flat, catalog, rules, stamp)
    # Curated "catalog" rows are always rebuilt, so count that source as answered.
    result.removed = _persist(db, records, set(live) | {"catalog"})
    result.models = len(records)
    result.unscored = sum(1 for r in records if r.row.unscored)
    kv_set(db, KV_LAST_REFRESH, stamp)
    return result


def refresh_if_stale(
    db: Database, rules: Rules, *, force: bool = False, now: float | None = None
) -> RefreshResult:
    """Startup / TTL entry point: uses the best catalog available offline."""
    if not force and not needs_refresh(db, rules, now):
        return RefreshResult(skipped=True)
    catalog, _source = updater.load_active()
    return refresh_models(db, rules, catalog, now=now)
