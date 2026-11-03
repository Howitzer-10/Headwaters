"""Model discovery: gather live sources, merge with the catalog, persist to SQLite.

Every source is optional and failures degrade gracefully: an unreachable source adds a warning
and its models are simply not refreshed (they are NOT deleted, so a brief Ollama outage does
not wipe the picker). Models from a source that answered but no longer lists them are removed.
Offline modes: ``airplane`` and the local-only lock block every non-loopback source; Ollama
(loopback) still runs. No prompt data is sent anywhere.

Discovery has one overall wall-clock budget (:data:`REFRESH_BUDGET_S`), so a blackholed endpoint
costs a warning and a partial result instead of minutes of silence; a caller may pass ``progress``
to hear which source is being read while it runs.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit, urlunsplit

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
from headwaters.core.ollama_remote import RemotePatterns, remote_patterns_from
from headwaters.core.rules import Rules
from headwaters.core.state import Database
from headwaters.core.state_misc import kv_get, kv_set
from headwaters.core.state_models import delete_models, list_models, set_score, upsert_model

KV_LAST_REFRESH = "models.last_refresh"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
REFRESH_BUDGET_S = 20.0
_T = TypeVar("_T")


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


def network_block(rules: Rules) -> str | None:
    if rules.airplane:
        return "airplane mode"
    if rules.private_lock or rules.default_scope == "local":
        return "local-only lock state"
    return None


def _display_url(url: str) -> str:
    """A URL that is safe to print: any ``user:password@`` part is dropped."""
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[1]))


@dataclass
class _Budget:
    """Wall-clock budget shared by every source in one refresh (``None`` = unbounded)."""

    seconds: float | None = REFRESH_BUDGET_S
    started: float = field(default_factory=time.monotonic)

    def deadline(self) -> float | None:
        return None if self.seconds is None else self.started + self.seconds

    def remaining(self) -> float | None:
        if self.seconds is None:
            return None
        return max(0.0, self.seconds - (time.monotonic() - self.started))


def _read_source(
    name: str,
    description: str,
    read: Callable[[], _T],
    budget: _Budget,
    report: Callable[[str], None],
) -> _T:
    """Run one source, bounded by what is left of the budget.

    The read runs on a daemon thread so a blocked connect (or anything else that ignores the
    request timeout) cannot hold the command: when the budget runs out the caller gets
    ``SourceError`` and a warning, and the thread is left to die with the process.
    """
    report(f"checking {description}")
    box: list[_T] = []
    failed: list[BaseException] = []
    done = threading.Event()

    def work() -> None:
        try:
            box.append(read())
        except BaseException as exc:  # re-raised in the caller, so it is caught there
            failed.append(exc)
        finally:
            done.set()

    threading.Thread(target=work, name=f"headwaters-discovery-{name}", daemon=True).start()
    remaining = budget.remaining()
    if remaining is None:
        done.wait()
    elif not done.wait(remaining):
        raise SourceError(f"no answer within the {budget.seconds:g}s refresh budget")
    if failed:
        raise failed[0]
    return box[0]


def _gather(
    rules: Rules,
    http: HttpClient,
    env: Mapping[str, str],
    home: Path,
    result: RefreshResult,
    remote_patterns: RemotePatterns = (),
    openrouter_base_url: str | None = None,
    *,
    budget_s: float | None = REFRESH_BUDGET_S,
    progress: Callable[[str], None] | None = None,
) -> dict[str, list[LiveModel]]:
    """Live models by source name. Only sources that answered appear in the result."""
    found: dict[str, list[LiveModel]] = {}
    blocked = network_block(rules)
    budget = _Budget(budget_s)

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    try:
        models, warnings = _read_source(
            "ollama",
            f"ollama at {rules.tiers.local.host}",
            lambda: ollama_models(
                http, rules.tiers.local.host, remote_patterns, deadline=budget.deadline()
            ),
            budget,
            report,
        )
        found["ollama"] = models
        result.warnings.extend(warnings)
    except SourceError as exc:
        result.warnings.append(f"ollama: {exc}")

    report("checking opencode config")
    paths_ = opencode_config_paths(rules.models.opencode_config, env, home)
    oc, oc_warn = opencode_models(paths_, rules.tiers.free_cloud.opencode_free)
    # OpenCode's free-tier models answer only inside OpenCode (HTTP 403 for other clients, A-OC-3,
    # HDW-150), so they are never offered. The source still "answers" with no models, which
    # removes rows saved by an earlier version.
    found["opencode"] = []
    result.warnings.extend(oc_warn)
    if oc:
        result.warnings.append(
            f"opencode: {len(oc)} free model(s) not offered; OpenCode serves its free models "
            "only inside OpenCode, so Headwaters cannot call them. Use them from OpenCode itself."
        )

    if blocked:
        report(f"skipping cloud sources ({blocked})")
        result.warnings.append(f"cloud discovery skipped: {blocked}")
        return found

    try:
        found["openrouter"] = _read_source(
            "openrouter",
            "openrouter",
            lambda: openrouter_models(http, env.get(OPENROUTER_KEY_ENV), openrouter_base_url),
            budget,
            report,
        )
    except SourceError as exc:
        result.warnings.append(f"openrouter: {exc}")
    for endpoint in rules.models.openai_compatible:
        key = f"openai_compatible:{endpoint.name}"
        try:
            found[key] = _read_source(
                key,
                f"{endpoint.name} at {_display_url(endpoint.base_url)}",
                partial(openai_compatible_models, http, endpoint, env),
                budget,
                report,
            )
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
    budget_s: float | None = REFRESH_BUDGET_S,
    progress: Callable[[str], None] | None = None,
) -> RefreshResult:
    """Discover, merge and persist. Never raises because a source is down or out of budget.

    ``progress`` receives one short line as each source starts (no keys, no prompt text), so a
    slow refresh is never silent; ``budget_s`` caps the whole gather.
    """
    result = RefreshResult()
    stamp = time.time() if now is None else now
    live = _gather(
        rules,
        http or UrllibHttp(),
        os.environ if env is None else env,
        home or Path.home(),
        result,
        remote_patterns_from(catalog),
        catalog.endpoints.openrouter_base_url,
        budget_s=budget_s,
        progress=progress,
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
