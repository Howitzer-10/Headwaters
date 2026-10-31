# Core source, part A: scope, policy, executor, router, network edges

Read this first. It holds the review priorities: fail-closed local scope, the policy ladder, the executor and every module that can touch the network.

40 files.

| File | Lines | Purpose |
| --- | ---: | --- |
| `headwaters/core/analyzer/ollama_client.py` | 65 | Minimal Ollama native-API client for the classifier layer (HDW-107). |
| `headwaters/core/candidates.py` | 130 | Candidate source: turns discovered models into policy candidates (HDW-118). |
| `headwaters/core/catalog/discovery.py` | 152 | Model discovery: gather live sources, merge with the catalog, persist to SQLite. |
| `headwaters/core/catalog/http.py` | 91 | Tiny JSON-over-HTTP client behind a Protocol, so discovery can be tested with mocks. |
| `headwaters/core/catalog/sources.py` | 316 | Live discovery sources. Each returns ``LiveModel`` items and raises ``SourceError`` on trouble. |
| `headwaters/core/executor/__init__.py` | 1 |  |
| `headwaters/core/executor/accumulate.py` | 66 | Accumulates normalised deltas into one result (HDW-115). Reasoning stays apart from content. |
| `headwaters/core/executor/cloud.py` | 81 | Cloud backend: the ``litellm`` SDK behind the executor interface (HDW-115, D7). |
| `headwaters/core/executor/cloud_map.py` | 114 | litellm <-> executor mapping helpers (HDW-115): headers, reasoning params, errors, chunks. |
| `headwaters/core/executor/errors.py` | 82 | Executor errors (HDW-115). They never carry prompt or output text. |
| `headwaters/core/executor/executor.py` | 217 | The executor (HDW-115, v3 section 4.8, v3.3 section D). |
| `headwaters/core/executor/guard.py` | 56 | Local-scope guard (HDW-115, v3.3 section D). |
| `headwaters/core/executor/ollama.py` | 76 | Local backend: Ollama NATIVE ``/api/chat`` over loopback only (HDW-115). |
| `headwaters/core/executor/ollama_convert.py` | 152 | Ollama native-API conversion helpers (HDW-115): request payload and stream-line parsing. |
| `headwaters/core/executor/types.py` | 131 | Executor data types and the backend protocol (HDW-115). |
| `headwaters/core/features.py` | 158 | Feature registry. |
| `headwaters/core/logging.py` | 185 | Structured, redacted logging. |
| `headwaters/core/plugins.py` | 129 | Versioned plugin interfaces. |
| `headwaters/core/policy/__init__.py` | 1 |  |
| `headwaters/core/policy/decide.py` | 111 | Policy engine B: the decision ladder (HDW-114, v3 section 4.5, v3.3 section B4). |
| `headwaters/core/policy/errors.py` | 48 | Structured routing errors (HDW-114). Never carries prompt text. |
| `headwaters/core/policy/filters.py` | 134 | Policy engine A, part 1: candidates and the hard filter (HDW-113, v3 section 4.5). |
| `headwaters/core/policy/ladder.py` | 111 | Ladder construction and error/suggestion helpers (HDW-114). See ``decide`` for the rungs. |
| `headwaters/core/policy/scoring.py` | 155 | Policy engine A, part 2: quality floor and score (HDW-113, v3 section 4.5). |
| `headwaters/core/policy/types.py` | 55 | Request, context and result types of the decision ladder (HDW-114). |
| `headwaters/core/project_config.py` | 66 | Per-project config (``.headwaters.yaml``), discovered by walking up from the workspace. |
| `headwaters/core/router.py` | 579 | Router façade: the one core API every engine calls (HDW-117, v3 section 2, v3.4 section A). |
| `headwaters/core/router_log.py` | 148 | Decision and outcome logging for the router (HDW-117). Metadata only. |
| `headwaters/core/router_plan.py` | 33 | Result of a routing dry run (HDW-118): what ``Router.handle`` would do, without doing it. |
| `headwaters/core/router_prep.py` | 86 | Request preparation for the router (HDW-117): messages, options, secrets, candidates. |
| `headwaters/core/router_run.py` | 35 | Per-request working state of the router (HDW-117). Holds the request and routing metadata. |
| `headwaters/core/router_session.py` | 78 | Session keying and text helpers for the router (HDW-117). |
| `headwaters/core/router_types.py` | 174 | Request, result and error types of the router façade (HDW-117). |
| `headwaters/core/router_verify.py` | 77 | Verification step of the router (HDW-117, v3 section 4.6). |
| `headwaters/core/rules.py` | 263 | User rules model (``rules.yaml``). |
| `headwaters/core/rules_edit.py` | 106 | Targeted edits to ``rules.yaml`` that keep the user's comments and layout (HDW-118). |
| `headwaters/core/scope.py` | 276 | Scope resolver: which tiers may serve a request. |
| `headwaters/core/secrets_detect.py` | 76 | Secret detection for routing (API keys, tokens, private keys). |
| `headwaters/core/state.py` | 133 | Shared SQLite state (gateway, MCP daemon and CLI all open the same database). |
| `headwaters/core/state_decisions.py` | 145 | Repositories for ``decisions`` and ``outcomes`` (metadata and token counts only). |

## headwaters/core/analyzer/ollama_client.py

````python
"""Minimal Ollama native-API client for the classifier layer (HDW-107).

Only ``POST /api/chat`` on a loopback host. Stdlib ``urllib`` with proxies disabled, so a
local-scope prompt can never leave the machine through this path. HDW-115's executor may
replace it; layer 3 only depends on the ``OllamaChatClient`` protocol.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlparse

_MAX_RESPONSE_BYTES = 1_000_000


class ClassifierUnavailableError(RuntimeError):
    """The classifier model could not answer (down, timed out, bad reply)."""


class OllamaChatClient(Protocol):
    def chat(self, payload: Mapping[str, Any], timeout_s: float) -> Mapping[str, Any]: ...


def assert_loopback(host_url: str) -> str:
    """Return ``host_url`` without a trailing slash, or raise if it is not loopback http."""
    parsed = urlparse(host_url)
    name = parsed.hostname or ""
    if parsed.scheme != "http" or not name:
        raise ValueError(f"classifier host must be a plain http loopback URL, got {host_url!r}")
    if name != "localhost":
        try:
            if not ipaddress.ip_address(name).is_loopback:
                raise ValueError(f"classifier host {name!r} is not a loopback address")
        except ValueError as exc:
            raise ValueError(f"classifier host {name!r} is not a loopback address") from exc
    return host_url.rstrip("/")


class OllamaHttpClient:
    def __init__(self, host: str = "http://localhost:11434") -> None:
        self.host = assert_loopback(host)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def chat(self, payload: Mapping[str, Any], timeout_s: float) -> Mapping[str, Any]:
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=timeout_s) as response:
                raw = response.read(_MAX_RESPONSE_BYTES)
            data = json.loads(raw)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise ClassifierUnavailableError(type(exc).__name__) from exc
        if not isinstance(data, dict):
            raise ClassifierUnavailableError("reply is not an object")
        return data
````

## headwaters/core/candidates.py

````python
"""Candidate source: turns discovered models into policy candidates (HDW-118).

Reads the shared SQLite state (models and scores written by discovery), the catalog (speeds) and
the current hardware snapshot, and answers the router's ``CandidateSource`` question: which
models could serve a request with these requirements, and how well do they fit?

* Scores: user override, else learned average, else catalog seed (``state_models``).
* Local models: KV/fit math for this request's size, plus warm/eviction inputs from the models
  Ollama held at the last refresh.
* Walls and cooldowns are NOT applied here: the router overlays live quota state on every request.
* The user's paid primary models (``tiers.paid.models``) are flagged ``is_primary``.

Not covered yet (engines add them): OpenRouter auto/free router routes and the premium route,
API-key presence for paid models, and live ``/api/ps`` calibration.
"""

from __future__ import annotations

from collections.abc import Callable

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.catalog.merge import CatalogLookup
from headwaters.core.catalog.schema import Arch, Catalog, ModelEntry
from headwaters.core.hardware.fit import GB_TO_GIB, FitConfig, FitResult, evaluate_fit
from headwaters.core.hardware.residency import (
    LoadedModel,
    ResidencyConfig,
    ResidencyInputs,
    default_max_loaded,
    residency_inputs,
)
from headwaters.core.hardware.state import HardwareState
from headwaters.core.hardware.sysmem import expected_tps_for
from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import Rules
from headwaters.core.state import Database
from headwaters.core.state_models import ModelRow, effective_scores, list_models

__all__ = ["CandidateBuilder", "fit_for_row"]

_ARCH_KEYS = ("n_layers", "n_kv_heads", "head_dim")


def _entry_for(row: ModelRow) -> ModelEntry:
    """A catalog-shaped entry for the fit math, built from what discovery stored.

    ``ModelRow.size_gb`` is GiB (discovery divides Ollama's bytes by 1024^3) while
    ``evaluate_fit`` reads ``size_gb`` as decimal GB, so convert back to keep the weights exact.
    """
    arch: Arch | None = None
    if row.arch and all(isinstance(row.arch.get(k), int) and row.arch[k] > 0 for k in _ARCH_KEYS):
        arch = Arch(**{k: int(row.arch[k]) for k in _ARCH_KEYS})
    size = row.size_gb / GB_TO_GIB if row.size_gb and row.size_gb > 0 else None
    return ModelEntry(
        id=row.id,
        provider=row.provider or "ollama",
        ctx=row.ctx if row.ctx and row.ctx > 0 else None,
        size_gb=size,
        arch=arch,
    )


def fit_for_row(row: ModelRow, budget_gb: float, est_tokens: int, kv_type: str) -> FitResult:
    """Fit of one local model on ``budget_gb`` of VRAM for a request of ``est_tokens``."""
    return evaluate_fit(_entry_for(row), budget_gb, est_tokens, FitConfig(kv_type=kv_type))


class CandidateBuilder:
    """``CandidateSource`` for the router. Cheap to create; reads the database per request."""

    def __init__(
        self,
        db: Database,
        rules: Rules,
        catalog: Catalog,
        hardware: Callable[[], HardwareState | None],
    ) -> None:
        self._db = db
        self._rules = rules
        self._catalog = catalog
        self._hardware = hardware

    def __call__(self, reqs: Requirements) -> list[Candidate]:
        rows = list_models(self._db)
        scores = effective_scores(self._db)
        hw = self._hardware()
        lookup = CatalogLookup(self._catalog)
        primary = set(self._rules.tiers.paid.models)
        loaded = [
            LoadedModel(r.id, r.size_vram_gb or r.size_gb or 0.0)
            for r in rows
            if r.tier == "local" and r.warm
        ]
        out: list[Candidate] = []
        for row in rows:
            fit: FitResult | None = None
            residency: ResidencyInputs | None = None
            tps: float | None = None
            if row.tier == "local":
                fit, residency = self._local_inputs(row, reqs.est_tokens, hw, loaded)
                entry = lookup.find([row.id])
                if entry is not None and hw is not None:
                    tps = expected_tps_for(entry, hw.profile)
            out.append(
                Candidate(
                    row,
                    scores.get(row.id, {}),
                    fit=fit,
                    residency=residency,
                    expected_tps=tps,
                    is_primary=row.id in primary,
                )
            )
        return out

    def _local_inputs(
        self,
        row: ModelRow,
        est_tokens: int,
        hw: HardwareState | None,
        loaded: list[LoadedModel],
    ) -> tuple[FitResult, ResidencyInputs]:
        budget = hw.budget_gb if hw is not None else 0.0
        fit = fit_for_row(row, budget, est_tokens, self._rules.tiers.local.kv_cache_type)
        needed = fit.weights_gb + fit.kv_gb if fit.usable else (row.size_gb or 0.0)
        config = ResidencyConfig(
            max_loaded_models=default_max_loaded(hw.profile if hw is not None else "custom")
        )
        return fit, residency_inputs(row.id, needed, loaded, budget, config)
````

## headwaters/core/catalog/discovery.py

````python
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
````

## headwaters/core/catalog/http.py

````python
"""Tiny JSON-over-HTTP client behind a Protocol, so discovery can be tested with mocks.

Loopback requests bypass system proxies. Plain ``http`` is only allowed for loopback hosts, so
an API key is never sent in clear text across a network. No prompt data passes through here.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from typing import Any, Protocol
from urllib.parse import urlparse

USER_AGENT = "Headwaters-discovery"


class SourceError(Exception):
    """A discovery source could not be read; the message is safe to show to the user."""


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    name = host.strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def is_loopback_url(url: str) -> bool:
    return is_loopback_host(urlparse(url).hostname)


class HttpClient(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any: ...


class UrllibHttp:
    def __init__(self, timeout: float = 10.0, max_bytes: int = 20_000_000) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        parsed = urlparse(url)
        loopback = is_loopback_host(parsed.hostname)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise SourceError(f"unsupported URL {url!r}")
        if parsed.scheme == "http" and not loopback:
            raise SourceError("plain http is only allowed for loopback hosts; use https")
        merged = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            merged["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=merged, method=method)
        opener = urllib.request.build_opener(
            *([urllib.request.ProxyHandler({})] if loopback else [])
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw: bytes = response.read(self.max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise SourceError(f"HTTP {exc.code} from {parsed.netloc}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise SourceError(f"cannot reach {parsed.netloc}: {exc}") from exc
        if len(raw) > self.max_bytes:
            raise SourceError(f"response from {parsed.netloc} is too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise SourceError(f"{parsed.netloc} did not return valid JSON") from exc
````

## headwaters/core/catalog/sources.py

````python
"""Live discovery sources. Each returns ``LiveModel`` items and raises ``SourceError`` on trouble.

Nothing here sends prompt data. Ollama uses the NATIVE API only (/api/tags, /api/show, /api/ps)
and refuses a non-loopback host (A-OLL-8). OpenRouter attribution headers are not sent (A-OR-10).
Sizes are GiB (bytes / 1024**3) to match the MiB figures GPUs report.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from headwaters.core.catalog.http import HttpClient, SourceError, is_loopback_url
from headwaters.core.rules import OpenAICompatEndpoint

LiveTier = Literal["local", "free_cloud", "paid"]
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_GIB = 1024**3


@dataclass
class LiveModel:
    id: str
    tier: LiveTier
    provider: str
    source: str
    modalities: list[str] = field(default_factory=lambda: ["text"])
    tools_ok: bool | None = None
    json_ok: bool | None = None
    ctx: int | None = None
    price_in: float | None = None  # USD per million tokens
    price_out: float | None = None
    size_gb: float | None = None
    arch: dict[str, int] | None = None
    warm: bool = False
    size_vram_gb: float | None = None
    retains_data: bool | None = None  # None = unknown; the catalog may say
    match_names: list[str] = field(default_factory=list)  # extra names to try in the catalog


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _int(value: Any) -> int | None:
    n = _num(value)
    return int(n) if n is not None and n > 0 else None


def _arch_from_show(info: Mapping[str, Any]) -> tuple[dict[str, int] | None, int | None]:
    """Architecture params for KV math (A-OLL-1). There is no single head_dim key."""
    prefix = info.get("general.architecture")
    if not isinstance(prefix, str):
        return None, None
    layers = _int(info.get(f"{prefix}.block_count"))
    heads = _int(info.get(f"{prefix}.attention.head_count"))
    kv_heads = _int(info.get(f"{prefix}.attention.head_count_kv")) or heads
    head_dim = _int(info.get(f"{prefix}.attention.key_length"))
    embed = _int(info.get(f"{prefix}.embedding_length"))
    if head_dim is None and embed and heads:
        head_dim = embed // heads
    ctx = _int(info.get(f"{prefix}.context_length"))
    if layers and kv_heads and head_dim:
        return {"n_layers": layers, "n_kv_heads": kv_heads, "head_dim": head_dim}, ctx
    return None, ctx


def ollama_models(http: HttpClient, host: str) -> tuple[list[LiveModel], list[str]]:
    """Installed Ollama models plus warm state. Returns (models, warnings)."""
    if not is_loopback_url(host):
        raise SourceError(f"Ollama host {host!r} is not loopback; local discovery refuses it")
    base = host.rstrip("/")
    tags = _as_dict(http.request_json("GET", f"{base}/api/tags"))
    warnings: list[str] = []
    models: dict[str, LiveModel] = {}
    for item in _as_list(tags.get("models")):
        name = _as_dict(item).get("name") or _as_dict(item).get("model")
        if not isinstance(name, str) or not name:
            continue
        size = _num(_as_dict(item).get("size"))
        live = LiveModel(
            id=name,
            tier="local",
            provider="ollama",
            source="ollama",
            size_gb=round(size / _GIB, 3) if size else None,
            retains_data=False,
        )
        try:
            show = _as_dict(http.request_json("POST", f"{base}/api/show", body={"model": name}))
        except SourceError as exc:
            warnings.append(f"ollama: /api/show failed for {name}: {exc}")
            models[name] = live
            continue
        caps = show.get("capabilities")
        if isinstance(caps, list):
            capset = {str(c) for c in caps}
            if "completion" not in capset:
                continue  # embedding-only model: not routable for chat
            if "vision" in capset:
                live.modalities = ["text", "image"]
            live.tools_ok = "tools" in capset
        live.arch, live.ctx = _arch_from_show(_as_dict(show.get("model_info")))
        models[name] = live
    try:
        ps = _as_dict(http.request_json("GET", f"{base}/api/ps"))
    except SourceError as exc:
        warnings.append(f"ollama: /api/ps failed (warm state unknown): {exc}")
    else:
        for item in _as_list(ps.get("models")):
            entry = _as_dict(item)
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name in models:
                models[name].warm = True
                vram = _num(entry.get("size_vram"))
                models[name].size_vram_gb = round(vram / _GIB, 3) if vram is not None else None
    return list(models.values()), warnings


def _per_million(value: Any) -> float | None:
    """OpenRouter prices are USD per token as strings (A-OR-1)."""
    n = _num(value)
    return round(n * 1_000_000, 6) if n is not None and n >= 0 else None


def openrouter_models(http: HttpClient, api_key: str | None = None) -> list[LiveModel]:
    """Models from /api/v1/models. ``:free`` ids are tier free_cloud, the rest paid."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    payload = _as_dict(http.request_json("GET", f"{OPENROUTER_BASE}/models", headers=headers))
    out: list[LiveModel] = []
    for item in _as_list(payload.get("data")):
        entry = _as_dict(item)
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        arch = _as_dict(entry.get("architecture"))
        inputs = [str(m) for m in _as_list(arch.get("input_modalities"))]
        outputs = [str(m) for m in _as_list(arch.get("output_modalities"))]
        if outputs and "text" not in outputs:
            continue  # image/audio generators are not chat models
        params = {str(p) for p in _as_list(entry.get("supported_parameters"))}
        pricing = _as_dict(entry.get("pricing"))
        free = model_id.endswith(":free")
        out.append(
            LiveModel(
                id=model_id,
                tier="free_cloud" if free else "paid",
                provider="openrouter",
                source="openrouter",
                modalities=inputs or ["text"],
                tools_ok="tools" in params,
                json_ok=bool(params & {"response_format", "structured_outputs"}),
                ctx=_int(entry.get("context_length")),
                price_in=0.0 if free else _per_million(pricing.get("prompt")),
                price_out=0.0 if free else _per_million(pricing.get("completion")),
                match_names=[s for s in (entry.get("canonical_slug"),) if isinstance(s, str)],
            )
        )
    return out


@dataclass(frozen=True)
class OpenRouterKeyInfo:
    is_free_tier: bool | None
    free_daily_limit: int | None
    free_daily_remaining: int | None
    credit_remaining: float | None


def openrouter_key(http: HttpClient, api_key: str) -> OpenRouterKeyInfo:
    """/api/v1/key. The deprecated ``rate_limit`` field is ignored (A-OR-3)."""
    headers = {"Authorization": f"Bearer {api_key}"}
    data = _as_dict(
        _as_dict(http.request_json("GET", f"{OPENROUTER_BASE}/key", headers=headers)).get("data")
    )
    free = _as_dict(data.get("free_model_daily_requests"))
    is_free = data.get("is_free_tier")
    return OpenRouterKeyInfo(
        is_free_tier=is_free if isinstance(is_free, bool) else None,
        free_daily_limit=_int(free.get("limit")),
        free_daily_remaining=(int(r) if (r := _num(free.get("remaining"))) is not None else None),
        credit_remaining=_num(data.get("limit_remaining")),
    )


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, leaving string contents alone."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 1
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def opencode_config_paths(explicit: str | None, env: Mapping[str, str], home: Path) -> list[Path]:
    """Where to look, lowest precedence first (A-OC-2). Windows path is unverified (R10)."""
    if explicit:
        return [Path(explicit)]
    found = [home / ".config" / "opencode" / name for name in ("opencode.json", "opencode.jsonc")]
    if env.get("OPENCODE_CONFIG"):
        found.append(Path(env["OPENCODE_CONFIG"]))
    return found


def opencode_models(
    config_paths: list[Path], user_list: list[str]
) -> tuple[list[LiveModel], list[str]]:
    """Free OpenCode models: the user list plus ``provider.opencode.models`` from the config.

    There is no machine-readable free-model endpoint (A-OC-3), so the curated catalog and the
    user list decide what is free. Free OpenCode endpoints may retain data (A-OC-4).
    """
    warnings: list[str] = []
    names: list[str] = list(user_list)
    for path in config_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError as exc:
            warnings.append(f"opencode: cannot read {path}: {exc.strerror or exc}")
            continue
        try:
            config = _as_dict(json.loads(_strip_jsonc(text)))
        except ValueError:
            warnings.append(f"opencode: {path} is not valid JSON; skipped")
            continue
        provider = _as_dict(_as_dict(config.get("provider")).get("opencode"))
        names.extend(str(k) for k in _as_dict(provider.get("models")))
    seen: dict[str, LiveModel] = {}
    for name in names:
        model_id = name if name.startswith("opencode/") else f"opencode/{name}"
        seen.setdefault(
            model_id,
            LiveModel(
                id=model_id,
                tier="free_cloud",
                provider="opencode",
                source="opencode",
                price_in=0.0,
                price_out=0.0,
                retains_data=True,
                match_names=[name.removeprefix("opencode/")],
            ),
        )
    return list(seen.values()), warnings


def openai_compatible_models(
    http: HttpClient, endpoint: OpenAICompatEndpoint, env: Mapping[str, str]
) -> list[LiveModel]:
    """A user-supplied free OpenAI-compatible endpoint (explicit list, or GET /models)."""
    names = list(endpoint.models)
    if not names:
        headers = None
        if endpoint.api_key_env and env.get(endpoint.api_key_env):
            headers = {"Authorization": f"Bearer {env[endpoint.api_key_env]}"}
        payload = http.request_json(
            "GET", endpoint.base_url.rstrip("/") + "/models", headers=headers
        )
        names = [
            str(_as_dict(m).get("id"))
            for m in _as_list(_as_dict(payload).get("data"))
            if _as_dict(m).get("id")
        ]
    return [
        LiveModel(
            id=f"{endpoint.name}/{name}",
            tier="free_cloud",
            provider=endpoint.name,
            source=f"openai_compatible:{endpoint.name}",
            price_in=0.0,
            price_out=0.0,
            match_names=[name],
        )
        for name in names
    ]
````

## headwaters/core/executor/__init__.py

````python

````

## headwaters/core/executor/accumulate.py

````python
"""Accumulates normalised deltas into one result (HDW-115). Reasoning stays apart from content."""

from __future__ import annotations

from dataclasses import dataclass, field

from headwaters.core.executor.types import Delta, ExecResult, ToolCall, Usage
from headwaters.core.scope import TierName


@dataclass
class _CallParts:
    id: str = ""
    name: str = ""
    arguments: list[str] = field(default_factory=list)


@dataclass
class Accumulator:
    model: str
    tier: TierName
    content: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    calls: dict[int, _CallParts] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)

    def add(self, delta: Delta) -> None:
        if delta.content:
            self.content.append(delta.content)
        if delta.reasoning:
            self.reasoning.append(delta.reasoning)
        for part in delta.tool_calls:
            slot = self.calls.setdefault(part.index, _CallParts())
            if part.id:
                slot.id = part.id
            if part.name:
                slot.name = part.name
            if part.arguments:
                slot.arguments.append(part.arguments)
        if delta.finish_reason:
            self.finish_reason = delta.finish_reason
        if delta.usage is not None:
            self.usage = Usage(
                prompt_tokens=delta.usage.prompt_tokens or self.usage.prompt_tokens,
                completion_tokens=delta.usage.completion_tokens or self.usage.completion_tokens,
            )

    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(
            ToolCall(id=c.id or f"call_{i}", name=c.name, arguments="".join(c.arguments) or "{}")
            for i, c in sorted(self.calls.items())
        )

    def result(self, first_token_s: float | None) -> ExecResult:
        return ExecResult(
            model=self.model,
            tier=self.tier,
            content="".join(self.content),
            reasoning="".join(self.reasoning),
            tool_calls=self.tool_calls(),
            finish_reason=self.finish_reason or "stop",
            usage=self.usage,
            first_token_s=first_token_s,
        )
````

## headwaters/core/executor/cloud.py

````python
"""Cloud backend: the ``litellm`` SDK behind the executor interface (HDW-115, D7).

litellm is imported lazily, on the first cloud call only. A local-scoped request never gets here,
so no cloud client is ever constructed for it. All litellm use stays in this module.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import AsyncIterator
from typing import Any

from headwaters.core.executor.cloud_map import delta_from_chunk, map_exception, reasoning_kwargs
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import BackendRequest, Delta


def _load_litellm() -> Any:
    # No network fetch of the model-cost map at import, and no telemetry.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    module = importlib.import_module("litellm")
    setattr(module, "telemetry", False)  # noqa: B010 - litellm ships no type stubs for it
    return module


def build_kwargs(request: BackendRequest) -> dict[str, Any]:
    target = request.target
    options = request.options
    kwargs: dict[str, Any] = {
        "model": target.route,
        "messages": [dict(m) for m in request.messages],
        "stream": True,
        "stream_options": {"include_usage": True},
        "num_retries": 0,
        "max_retries": 0,
    }
    if request.tools:
        kwargs["tools"] = [dict(t) for t in request.tools]
    if request.api_key:
        kwargs["api_key"] = request.api_key
    if target.api_base:
        kwargs["api_base"] = target.api_base
    if request.headers:
        kwargs["extra_headers"] = dict(request.headers)
    if options.max_tokens is not None:
        kwargs["max_tokens"] = options.max_tokens
    if options.temperature is not None:
        kwargs["temperature"] = options.temperature
    if options.json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": dict(options.json_schema)},
        }
    kwargs.update(reasoning_kwargs(target.provider, options.reasoning))
    return kwargs


class LiteLLMBackend:
    """Streams ``litellm.acompletion``. Retries are off: failover is the router's job."""

    def __init__(self, module: Any | None = None) -> None:
        self._module = module

    def _litellm(self) -> Any:
        if self._module is None:
            self._module = _load_litellm()
        return self._module

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        kwargs = build_kwargs(request)
        litellm = self._litellm()
        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                yield delta_from_chunk(chunk)
        except ProviderError:
            raise
        except Exception as exc:  # litellm raises its own exception tree
            raise map_exception(exc) from exc
````

## headwaters/core/executor/cloud_map.py

````python
"""litellm <-> executor mapping helpers (HDW-115): headers, reasoning params, errors, chunks.

Pure functions over duck-typed litellm objects, so they are tested without importing litellm.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import Delta, ToolCallDelta, Usage
from headwaters.core.rules import AttributionRules

OPENROUTER = "openrouter"
DEFAULT_EFFORT = "medium"  # used when reasoning is simply switched on (True)
_MAX_ERROR_CHARS = 4096
_TIMEOUT_NAMES = frozenset({"Timeout", "APITimeoutError", "ReadTimeout"})
_CONNECTION_NAMES = frozenset({"APIConnectionError", "ServiceUnavailableError"})


def attribution_headers(attribution: AttributionRules) -> dict[str, str]:
    """OpenRouter app attribution (A-OR-10). Opt-in: nothing is sent unless ``referer`` is set,
    because Headwaters promises no telemetry. ``X-Title`` is still accepted by OpenRouter."""
    if not attribution.referer:
        return {}
    return {"HTTP-Referer": attribution.referer, "X-OpenRouter-Title": attribution.title}


def reasoning_kwargs(provider: str, reasoning: bool | str | None) -> dict[str, Any]:
    """The provider's reasoning parameter (A-OR-7): OpenRouter's unified ``reasoning`` object
    through ``extra_body``; everything else through litellm's ``reasoning_effort``."""
    if reasoning is None:
        return {}
    if provider == OPENROUTER:
        if reasoning is False:
            return {"extra_body": {"reasoning": {"enabled": False}}}
        effort = DEFAULT_EFFORT if reasoning is True else reasoning
        return {"extra_body": {"reasoning": {"effort": effort}}}
    if reasoning is False:
        return {}
    return {"reasoning_effort": DEFAULT_EFFORT if reasoning is True else reasoning}


def _header_map(exc: BaseException) -> Mapping[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    try:
        return {str(k): str(v) for k, v in headers.items()}
    except (AttributeError, TypeError):
        return {}


def map_exception(exc: BaseException) -> ProviderError:
    """litellm exception -> ``ProviderError`` (status, headers and body for the wall classifier)."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    kind = "http"
    if not isinstance(status, int) or isinstance(status, bool):
        if name in _TIMEOUT_NAMES:
            status, kind = 408, "timeout"
        elif name in _CONNECTION_NAMES:
            status, kind = 0, "connection"
        else:
            status = 500
    body = str(getattr(exc, "message", None) or exc)[:_MAX_ERROR_CHARS]
    return ProviderError(status, _header_map(exc), body, kind=kind)


def _usage(value: object) -> Usage | None:
    if value is None:
        return None
    prompt = getattr(value, "prompt_tokens", None)
    completion = getattr(value, "completion_tokens", None)
    return Usage(
        prompt_tokens=prompt if isinstance(prompt, int) else None,
        completion_tokens=completion if isinstance(completion, int) else None,
    )


def delta_from_chunk(chunk: object) -> Delta:
    choices = getattr(chunk, "choices", None) or []
    usage = _usage(getattr(chunk, "usage", None))
    if not choices:
        return Delta(usage=usage)
    choice = choices[0]
    finish = getattr(choice, "finish_reason", None)
    if finish == "error":  # OpenRouter reports a mid-stream failure this way (A-OR-4)
        raise ProviderError(500, None, str(getattr(chunk, "error", "") or ""), kind="stream_error")
    delta = getattr(choice, "delta", None)
    content = getattr(delta, "content", None) or ""
    reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None) or ""
    calls: list[ToolCallDelta] = []
    for position, call in enumerate(getattr(delta, "tool_calls", None) or []):
        function = getattr(call, "function", None)
        index = getattr(call, "index", None)
        calls.append(
            ToolCallDelta(
                index=index if isinstance(index, int) else position,
                id=getattr(call, "id", None),
                name=getattr(function, "name", None),
                arguments=getattr(function, "arguments", None) or "",
            )
        )
    return Delta(
        content=str(content),
        reasoning=str(reasoning),
        tool_calls=tuple(calls),
        finish_reason=finish if isinstance(finish, str) else None,
        usage=usage,
    )
````

## headwaters/core/executor/errors.py

````python
"""Executor errors (HDW-115). They never carry prompt or output text."""

from __future__ import annotations

from collections.abc import Mapping

from headwaters.core.walls import WallEvent


class ExecutorError(Exception):
    """A call failed. ``wall`` is the wall classifier's reading of a provider response.

    ``before_first_token`` tells callers whether failing over to another model is still safe
    (nothing was streamed yet). Messages are short fixed text, never provider or user text.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        wall: WallEvent | None = None,
        status: int | None = None,
        provider: str = "",
        model: str = "",
        before_first_token: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.wall = wall
        self.status = status
        self.provider = provider
        self.model = model
        self.before_first_token = before_first_token

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "wall": None if self.wall is None else self.wall.kind,
            "before_first_token": self.before_first_token,
        }


class ScopeViolation(ExecutorError):
    """The target is not allowed by the effective scope (or by airplane mode)."""

    def __init__(self, message: str, *, model: str = "", code: str = "scope_violation") -> None:
        super().__init__(code, message, model=model)


class LocalScopeViolation(ScopeViolation):
    """A local-scoped call would leave the machine (non-loopback Ollama host or a cloud tier)."""

    def __init__(self, message: str, *, model: str = "") -> None:
        super().__init__(message, model=model, code="local_scope_violation")


class ProviderError(Exception):
    """Raw failure from a backend; the executor turns it into an ``ExecutorError``.

    ``body`` is only matched against wall patterns, never stored or logged.
    """

    def __init__(
        self,
        status: int,
        headers: Mapping[str, str] | None = None,
        body: str = "",
        *,
        kind: str = "http",  # http | timeout | connection | stream_error
    ) -> None:
        super().__init__(f"{kind} {status}")
        self.status = status
        self.headers: Mapping[str, str] = dict(headers or {})
        self.body = body
        self.kind = kind
````

## headwaters/core/executor/executor.py

````python
"""The executor (HDW-115, v3 section 4.8, v3.3 section D).

``Executor.run(decision, messages, tools, stream)`` calls the chosen model: cloud through the
litellm SDK, local through Ollama's native ``/api/chat``. Streaming exposes a ``first_token``
event so callers fail over only before it; later failures arrive as an ``error`` event. Provider
errors are classified by the wall classifier. Local scope is enforced here: no cloud backend is
constructed for a local-only request, and the Ollama host must be loopback.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from typing import Any, Literal, overload

from headwaters.core.executor.accumulate import Accumulator
from headwaters.core.executor.cloud import LiteLLMBackend
from headwaters.core.executor.cloud_map import OPENROUTER, attribution_headers
from headwaters.core.executor.errors import ExecutorError, ProviderError
from headwaters.core.executor.guard import enforce_scope
from headwaters.core.executor.ollama import OllamaBackend
from headwaters.core.executor.types import (
    Backend,
    BackendRequest,
    Delta,
    ExecOptions,
    ExecResult,
    ExecTarget,
    ExecutorConfig,
    Message,
    StreamEvent,
    ToolSpec,
)
from headwaters.core.policy.types import RouteDecision
from headwaters.core.rules import Rules
from headwaters.core.walls import WallClassifier

# Maps a decision to what to call (litellm route or Ollama name, ``num_ctx``, cold flag).
# The router supplies it from the catalog, discovery and hardware fit.
TargetResolver = Callable[[RouteDecision], ExecTarget]


def _has_output(delta: Delta) -> bool:
    return bool(delta.content or delta.reasoning or delta.tool_calls)


class Executor:
    def __init__(
        self,
        rules: Rules,
        walls: WallClassifier,
        resolve: TargetResolver,
        *,
        config: ExecutorConfig | None = None,
        cloud_factory: Callable[[], Backend] = LiteLLMBackend,
        local_factory: Callable[[str], Backend] = OllamaBackend,
        api_key_for: Callable[[str], str | None] | None = None,
    ) -> None:
        self._rules = rules
        self._walls = walls
        self._resolve = resolve
        self._config = config or ExecutorConfig()
        self._cloud_factory = cloud_factory
        self._local_factory = local_factory
        self._api_key_for = api_key_for
        self._cloud: Backend | None = None  # built on first cloud call, never for local scope

    @overload
    def run(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = ...,
        stream: Literal[False] = ...,
        options: ExecOptions | None = ...,
    ) -> Coroutine[Any, Any, ExecResult]: ...

    @overload
    def run(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        stream: Literal[True],
        options: ExecOptions | None = ...,
    ) -> AsyncIterator[StreamEvent]: ...

    def run(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        stream: bool = False,
        options: ExecOptions | None = None,
    ) -> Coroutine[Any, Any, ExecResult] | AsyncIterator[StreamEvent]:
        """``stream=False`` -> awaitable ``ExecResult`` (raises ``ExecutorError``).
        ``stream=True`` -> async iterator of ``StreamEvent``: errors before the first token are
        raised (the caller may fail over); later ones arrive as an ``error`` event."""
        opts = options or ExecOptions()
        if stream:
            return self._events(decision, messages, tools, opts)
        return self._collect(decision, messages, tools, opts)

    async def _collect(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        options: ExecOptions,
    ) -> ExecResult:
        final: ExecResult | None = None
        async for event in self._events(decision, messages, tools, options):
            if event.kind == "error" and event.error is not None:
                raise event.error
            if event.kind == "done":
                final = event.result
        if final is None:
            raise ExecutorError("no_result", "The model returned no result.", model=decision.model)
        return final

    def _prepare(
        self, decision: RouteDecision
    ) -> tuple[Backend, ExecTarget, dict[str, str], str | None]:
        enforce_scope(decision, self._rules)  # before any backend or client exists
        target = self._resolve(decision)
        if decision.tier == "local":
            return self._local_factory(self._rules.tiers.local.host), target, {}, None
        if self._cloud is None:
            self._cloud = self._cloud_factory()
        headers = (
            attribution_headers(self._rules.attribution) if target.provider == OPENROUTER else {}
        )
        key = self._api_key_for(target.provider) if self._api_key_for else None
        return self._cloud, target, headers, key

    def _first_token_limit(self, decision: RouteDecision, target: ExecTarget) -> float:
        cfg = self._config
        if decision.tier != "local":
            return cfg.cloud_first_token_s
        if target.cold is False:
            return cfg.local_warm_first_token_s
        return cfg.local_cold_first_token_s  # cold or unknown: the model may still be loading

    def _error(
        self, exc: ProviderError, decision: RouteDecision, target: ExecTarget, *, first: bool
    ) -> ExecutorError:
        provider = "ollama" if decision.tier == "local" else target.provider
        wall = self._walls.classify(exc.status, exc.headers, exc.body, provider)
        if exc.kind == "timeout":
            code = "timeout"
        elif exc.kind == "connection":
            code = "connection_error"
        elif wall.kind != "other":
            code = wall.kind
        else:
            code = "provider_error"
        return ExecutorError(
            code,
            f"Provider call failed ({code}).",
            wall=wall,
            status=exc.status,
            provider=provider,
            model=decision.model,
            before_first_token=first,
        )

    async def _events(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        options: ExecOptions,
    ) -> AsyncIterator[StreamEvent]:
        backend, target, headers, key = self._prepare(decision)
        keep_alive = self._rules.tiers.local.keep_alive
        request = BackendRequest(target, messages, tools, options, keep_alive, headers, key)
        acc = Accumulator(decision.model, decision.tier)
        started = time.monotonic()
        deadline = started + self._first_token_limit(decision, target)
        first_s: float | None = None
        source = backend.stream(request)
        try:
            while True:
                if first_s is None:
                    limit = deadline - time.monotonic()
                else:
                    limit = self._config.stream_idle_s
                try:
                    async with asyncio.timeout(max(limit, 0.0)):
                        delta = await anext(source)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise ProviderError(408, None, "", kind="timeout") from None
                if first_s is None and _has_output(delta):
                    first_s = time.monotonic() - started
                    yield StreamEvent("first_token", first_token_s=first_s)
                acc.add(delta)
                if delta.reasoning:
                    yield StreamEvent("reasoning", text=delta.reasoning)
                if delta.content:
                    yield StreamEvent("content", text=delta.content)
        except ProviderError as exc:
            error = self._error(exc, decision, target, first=first_s is None)
            if first_s is None:
                raise error from exc
            yield StreamEvent("error", error=error)
            return
        finally:
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                await aclose()
        for call in acc.tool_calls():
            yield StreamEvent("tool_call", tool_call=call)
        yield StreamEvent("done", result=acc.result(first_s), first_token_s=first_s)
````

## headwaters/core/executor/guard.py

````python
"""Local-scope guard (HDW-115, v3.3 section D).

Enforcement lives here, in the executor, not only in the policy layer: a local-scoped request
never builds a cloud client, and a local target must be a loopback Ollama host.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from headwaters.core.executor.errors import LocalScopeViolation, ScopeViolation
from headwaters.core.policy.types import RouteDecision
from headwaters.core.rules import Rules


def assert_loopback_url(url: str) -> str:
    """Return ``url`` without a trailing slash, or raise ``LocalScopeViolation``.

    Accepts a plain ``http`` URL whose host is ``localhost`` or a loopback IP (127.0.0.0/8,
    ``::1``). Names are not resolved: anything else, including other host names, is refused.
    """
    try:
        parsed = urlparse(url)
        name = parsed.hostname or ""
        parsed.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError as exc:
        raise LocalScopeViolation("Local host URL is not valid.") from exc
    if parsed.scheme != "http" or not name:
        raise LocalScopeViolation("Local host must be a plain http loopback URL.")
    if name.lower() != "localhost":
        try:
            loopback = ipaddress.ip_address(name).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise LocalScopeViolation("Local host is not a loopback address; refusing to send.")
    return url.rstrip("/")


def enforce_scope(decision: RouteDecision, rules: Rules) -> None:
    """Raise unless ``decision`` may run. Called before any backend is constructed."""
    tier = decision.tier
    model = decision.model
    if decision.scope.local_only and tier != "local":
        raise LocalScopeViolation("Local scope never uses a cloud tier.", model=model)
    if tier not in decision.scope.tiers:
        raise ScopeViolation(f"Tier {tier!r} is outside the effective scope.", model=model)
    if rules.airplane and tier != "local":
        raise ScopeViolation("Airplane mode blocks all network egress.", model=model)
    if tier == "local":
        try:
            assert_loopback_url(rules.tiers.local.host)
        except LocalScopeViolation as exc:
            raise LocalScopeViolation(exc.message, model=model) from exc
````

## headwaters/core/executor/ollama.py

````python
"""Local backend: Ollama NATIVE ``/api/chat`` over loopback only (HDW-115).

Streams NDJSON. Sends the computed ``num_ctx``, ``keep_alive`` and an optional ``format`` JSON
schema. ``message.thinking`` is kept apart from ``message.content``. Proxy environment variables
are ignored (``trust_env=False``), so a local-scope prompt cannot be routed off the machine.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.guard import assert_loopback_url
from headwaters.core.executor.ollama_convert import build_payload, parse_line
from headwaters.core.executor.types import BackendRequest, Delta

DEFAULT_CONNECT_S = 5.0
_MAX_ERROR_BYTES = 4096


def _error_body(raw: bytes) -> str:
    return raw[:_MAX_ERROR_BYTES].decode("utf-8", errors="replace")


class OllamaBackend:
    """Streams ``POST {host}/api/chat``. Construction asserts the host is loopback."""

    def __init__(
        self,
        host: str,
        *,
        connect_s: float = DEFAULT_CONNECT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.host = assert_loopback_url(host)
        self._connect_s = connect_s
        self._transport = transport

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        payload = build_payload(request)
        timeout = httpx.Timeout(None, connect=self._connect_s)
        seen_calls = 0
        try:
            async with (
                httpx.AsyncClient(
                    timeout=timeout, trust_env=False, transport=self._transport
                ) as client,
                client.stream("POST", f"{self.host}/api/chat", json=payload) as response,
            ):
                if response.status_code >= 400:
                    body = _error_body(await response.aread())
                    raise ProviderError(response.status_code, response.headers, body)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError as exc:
                        raise ProviderError(
                            502, None, "invalid stream line", kind="stream_error"
                        ) from exc
                    if not isinstance(data, dict):
                        continue
                    if data.get("error"):
                        text = str(data["error"])[:_MAX_ERROR_BYTES]
                        raise ProviderError(500, None, text, kind="stream_error")
                    delta, seen_calls = parse_line(data, seen_calls)
                    yield delta
        except httpx.TimeoutException as exc:
            raise ProviderError(408, None, "", kind="timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(0, None, "", kind="connection") from exc
````

## headwaters/core/executor/ollama_convert.py

````python
"""Ollama native-API conversion helpers (HDW-115): request payload and stream-line parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.types import BackendRequest, Delta, Message, ToolCallDelta, Usage

_DATA_URL_MARK = ";base64,"


def _text_and_images(content: object) -> tuple[str, list[str]]:
    """OpenAI content (string or parts) -> (text, base64 images). Remote image URLs are dropped:
    fetching them would be network egress that the executor does not control."""
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    images: list[str] = []
    if isinstance(content, Sequence):
        for part in content:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "text":
                texts.append(str(part.get("text", "")))
            elif part.get("type") == "image_url":
                url = part.get("image_url")
                raw = url.get("url", "") if isinstance(url, Mapping) else str(url or "")
                if raw.startswith("data:") and _DATA_URL_MARK in raw:
                    images.append(raw.split(_DATA_URL_MARK, 1)[1])
    return "".join(texts), images


def _arguments_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def to_ollama_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Convert OpenAI-style chat messages to Ollama's native shape."""
    names: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        text, images = _text_and_images(message.get("content"))
        item: dict[str, Any] = {"role": role, "content": text}
        if images:
            item["images"] = images
        calls = message.get("tool_calls")
        if role == "assistant" and isinstance(calls, Sequence) and calls:
            converted: list[dict[str, Any]] = []
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function")
                fn = function if isinstance(function, Mapping) else {}
                name = str(fn.get("name", ""))
                if call.get("id"):
                    names[str(call["id"])] = name
                arguments = _arguments_object(fn.get("arguments"))
                converted.append({"function": {"name": name, "arguments": arguments}})
            item["tool_calls"] = converted
        if role == "tool":
            tool_name = message.get("name") or names.get(str(message.get("tool_call_id", "")))
            if tool_name:
                item["tool_name"] = str(tool_name)
        out.append(item)
    return out


def build_payload(request: BackendRequest) -> dict[str, Any]:
    target = request.target
    if target.num_ctx is None:
        raise ExecutorError(
            "missing_num_ctx",
            "Local call needs a computed num_ctx; refusing to fall back to the server default.",
            model=target.model_id,
        )
    options: dict[str, Any] = {"num_ctx": target.num_ctx}
    if request.options.temperature is not None:
        options["temperature"] = request.options.temperature
    if request.options.max_tokens is not None:
        options["num_predict"] = request.options.max_tokens
    payload: dict[str, Any] = {
        "model": target.route,
        "messages": to_ollama_messages(request.messages),
        "stream": True,
        "options": options,
        "keep_alive": request.keep_alive,
    }
    if request.tools:
        payload["tools"] = [dict(t) for t in request.tools]
    if request.options.json_schema is not None:
        payload["format"] = dict(request.options.json_schema)
    if request.options.reasoning is not None:
        payload["think"] = request.options.reasoning
    return payload


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_line(data: Mapping[str, Any], seen_calls: int) -> tuple[Delta, int]:
    """One NDJSON object -> (``Delta``, number of tool calls seen so far)."""
    raw_message = data.get("message")
    message: Mapping[str, Any] = raw_message if isinstance(raw_message, Mapping) else {}
    calls: list[ToolCallDelta] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, Sequence):
        for call in raw_calls:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            fn = function if isinstance(function, Mapping) else {}
            calls.append(
                ToolCallDelta(
                    index=seen_calls,
                    id=str(call.get("id") or f"call_{seen_calls}"),
                    name=str(fn.get("name", "")),
                    arguments=json.dumps(_arguments_object(fn.get("arguments"))),
                )
            )
            seen_calls += 1
    finish: str | None = None
    usage: Usage | None = None
    if data.get("done"):
        finish = "tool_calls" if seen_calls else str(data.get("done_reason") or "stop")
        usage = Usage(
            prompt_tokens=_opt_int(data.get("prompt_eval_count")),
            completion_tokens=_opt_int(data.get("eval_count")),
        )
    delta = Delta(
        content=str(message.get("content") or ""),
        reasoning=str(message.get("thinking") or ""),
        tool_calls=tuple(calls),
        finish_reason=finish,
        usage=usage,
    )
    return delta, seen_calls
````

## headwaters/core/executor/types.py

````python
"""Executor data types and the backend protocol (HDW-115)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.scope import TierName

Message = Mapping[str, Any]  # OpenAI-style chat message
ToolSpec = Mapping[str, Any]  # OpenAI-style tool: {"type": "function", "function": {...}}


@dataclass(frozen=True)
class ExecTarget:
    """What to call for one decision. ``route`` is a litellm model string (cloud) or an Ollama
    model name (local). ``num_ctx`` is required for local calls. ``cold`` is True/None when the
    local model is not known to be loaded (None = unknown, treated as cold)."""

    model_id: str
    provider: str
    route: str
    num_ctx: int | None = None
    cold: bool | None = None
    api_base: str | None = None


@dataclass(frozen=True)
class ExecOptions:
    """Per-call options. ``reasoning``: None = provider default, False = off, True or an
    effort string (minimal/low/medium/high/xhigh) = on. ``json_schema`` asks for structured
    output (Ollama ``format``, or an OpenAI ``response_format``)."""

    reasoning: bool | str | None = None
    json_schema: Mapping[str, Any] | None = None
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class ExecutorConfig:
    """Timeouts in seconds. The first-token limit is longer for a cold local model, because the
    model must be loaded (and the prompt processed) before anything streams."""

    cloud_first_token_s: float = 30.0
    local_warm_first_token_s: float = 60.0
    local_cold_first_token_s: float = 240.0
    stream_idle_s: float = 60.0  # max silence between tokens once streaming
    connect_s: float = 5.0


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text


@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""  # a fragment; fragments with the same index are concatenated


@dataclass(frozen=True)
class Delta:
    """One normalised piece of a provider stream."""

    content: str = ""
    reasoning: str = ""
    tool_calls: tuple[ToolCallDelta, ...] = ()
    finish_reason: str | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class BackendRequest:
    target: ExecTarget
    messages: Sequence[Message]
    tools: Sequence[ToolSpec] | None
    options: ExecOptions
    keep_alive: str
    headers: Mapping[str, str] = field(default_factory=dict)
    api_key: str | None = None


class Backend(Protocol):
    """Streams normalised deltas. Raises ``ProviderError`` (or ``ExecutorError``) on failure."""

    def stream(self, request: BackendRequest) -> AsyncIterator[Delta]: ...


StreamKind = Literal["first_token", "reasoning", "content", "tool_call", "done", "error"]


@dataclass(frozen=True)
class ExecResult:
    """The whole answer. ``reasoning`` is kept apart from ``content``."""

    model: str
    tier: TierName
    content: str
    reasoning: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str
    usage: Usage
    first_token_s: float | None


@dataclass(frozen=True)
class StreamEvent:
    """``first_token`` fires once, before the first reasoning/content/tool output. Callers may
    fail over only until they have seen it; after it, failures arrive as an ``error`` event."""

    kind: StreamKind
    text: str = ""
    tool_call: ToolCall | None = None
    first_token_s: float | None = None
    result: ExecResult | None = None
    error: ExecutorError | None = None
````

## headwaters/core/features.py

````python
"""Feature registry.

Features (core and ★) describe themselves with a ``FeatureSpec`` and register plugin
implementations here. Core never imports ``headwaters.features.*``: ★ packages are found at
runtime by name (``discover_features``) or through Python entry points (``headwaters.plugins``).

A ★ feature package exposes ``register(registry)`` which calls ``registry.register(spec, ...)``.

Runtime flags (the ``features:`` map from rules) are for user choice, never licensing. During the
beta every feature is on unless the user switches it off.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from headwaters.core.plugins import INTERFACES, PluginContractError, check_plugin

STAR_LEGEND = "★ = free during beta; may become part of Headwaters Pro at 1.0"
FEATURES_PACKAGE = "headwaters.features"
ENTRY_POINT_GROUP = "headwaters.plugins"


@dataclass(frozen=True)
class FeatureSpec:
    id: str
    star: bool
    title: str
    interfaces: tuple[str, ...] = ()
    default_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("FeatureSpec.id must not be empty")
        unknown = [name for name in self.interfaces if name not in INTERFACES]
        if unknown:
            raise ValueError(f"FeatureSpec {self.id!r} lists unknown interfaces: {unknown}")

    @property
    def label(self) -> str:
        """Title with the ★ marker for display."""
        return f"★ {self.title}" if self.star else self.title


class FeatureRegistry:
    def __init__(self, flags: Mapping[str, bool] | None = None) -> None:
        self._specs: dict[str, FeatureSpec] = {}
        self._plugins: dict[str, list[tuple[str, object]]] = {}
        self._flags: dict[str, bool] = dict(flags or {})

    def register(self, spec: FeatureSpec, plugins: Mapping[str, object] | None = None) -> None:
        """Register a feature and the plugin instance it provides for each interface.

        ``plugins`` maps interface name to implementation; every key must be listed in
        ``spec.interfaces`` and the implementation must pass the contract check.
        """
        if spec.id in self._specs:
            raise ValueError(f"feature already registered: {spec.id!r}")
        provided = dict(plugins or {})
        for interface, plugin in provided.items():
            if interface not in spec.interfaces:
                raise PluginContractError(
                    f"feature {spec.id!r} provides {interface} but does not declare it"
                )
            check_plugin(interface, plugin)
        self._specs[spec.id] = spec
        self._plugins[spec.id] = list(provided.items())

    def set_flags(self, flags: Mapping[str, bool]) -> None:
        """Replace the runtime flags (the ``features:`` map from rules)."""
        self._flags = {str(k): bool(v) for k, v in flags.items()}

    def enabled(self, feature_id: str) -> bool:
        """True when the feature is registered and on. An explicit flag wins over the default."""
        spec = self._specs.get(feature_id)
        if spec is None:
            return False
        return self._flags.get(feature_id, spec.default_enabled)

    def get(self, feature_id: str) -> FeatureSpec | None:
        return self._specs.get(feature_id)

    def all(self) -> list[FeatureSpec]:
        return sorted(self._specs.values(), key=lambda s: s.id)

    def plugins_for(self, interface: str) -> list[object]:
        """Plugins implementing ``interface`` from enabled features, ordered by feature id."""
        if interface not in INTERFACES:
            raise ValueError(f"unknown plugin interface: {interface!r}")
        found: list[object] = []
        for spec in self.all():
            if not self.enabled(spec.id):
                continue
            found.extend(p for name, p in self._plugins[spec.id] if name == interface)
        return found

    def clear(self) -> None:
        self._specs.clear()
        self._plugins.clear()


def discover_features(registry: FeatureRegistry, package: str = FEATURES_PACKAGE) -> list[str]:
    """Import each subpackage of ``package`` by name and call its ``register(registry)``.

    Returns the module names that registered. Subpackages without ``register`` are skipped.
    Imports are dynamic on purpose: core has no static dependency on ★ code.
    """
    root = importlib.import_module(package)
    search_path = getattr(root, "__path__", None)
    if search_path is None:
        return []
    registered: list[str] = []
    for info in sorted(pkgutil.iter_modules(search_path), key=lambda m: m.name):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{package}.{info.name}")
        hook = getattr(module, "register", None)
        if callable(hook):
            hook(registry)
            registered.append(module.__name__)
    return registered


def discover_entry_points(registry: FeatureRegistry, group: str = ENTRY_POINT_GROUP) -> list[str]:
    """Load optional split-out plugins. Each entry point is a ``register`` callable (or a module
    exposing one)."""
    registered: list[str] = []
    for entry_point in sorted(importlib.metadata.entry_points(group=group), key=lambda e: e.name):
        loaded = entry_point.load()
        hook: Callable[[FeatureRegistry], None] | None = getattr(loaded, "register", None) or (
            loaded if callable(loaded) else None
        )
        if hook is None:
            raise PluginContractError(f"entry point {entry_point.name!r} has no register()")
        hook(registry)
        registered.append(entry_point.name)
    return registered


_default = FeatureRegistry()


def get_registry() -> FeatureRegistry:
    """The process-wide registry."""
    return _default


def register(spec: FeatureSpec, plugins: Mapping[str, object] | None = None) -> None:
    _default.register(spec, plugins)


def enabled(feature_id: str) -> bool:
    return _default.enabled(feature_id)
````

## headwaters/core/logging.py

````python
"""Structured, redacted logging.

Logs go to stderr and a rotating file, never stdout (MCP stdio carries JSON-RPC only).
Prompts, outputs and keys are never logged: fields named ``prompt``, ``content`` or
``messages`` are dropped, and key/token-like strings are masked.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from headwaters.core import paths

REDACTED = "[REDACTED]"
LOGGER_NAME = "headwaters"
LOG_FILE_NAME = "headwaters.log"
DEBUG_ENV_VAR = "HEADWATERS_DEBUG"
_HANDLER_NAME = "headwaters-managed"

# Extra-field names that may carry user content: dropped entirely.
DROPPED_FIELDS = frozenset({"prompt", "content", "messages"})
# Extra-field names whose values are always secrets.
SECRET_FIELDS = frozenset(
    {"api_key", "apikey", "token", "access_token", "secret", "password", "authorization"}
)

# Token-shaped strings. These are log-safety patterns, not routing data; secret *detection*
# for routing (with placeholder handling) is a separate core module.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),  # OpenAI, Anthropic (sk-ant-), OpenRouter (sk-or-)
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}(?![A-Za-z0-9+/_-])"),  # long opaque blobs
)
# name=value / name: value assignments; keeps the name, masks the value.
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|authorization)"
    r"(\s*[=:]\s*)(['\"]?)[^\s'\",;&]{4,}"
)


def redact_text(text: str) -> str:
    """Mask key/token-like substrings in ``text``."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", text)
    return text


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Redact a value of any JSON-ish shape; drops forbidden fields inside mappings."""
    if key is not None and key.lower() in SECRET_FIELDS:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(k): redact_value(v, key=str(k))
            for k, v in value.items()
            if str(k).lower() not in DROPPED_FIELDS
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value))


# Attributes present on every LogRecord; anything else came from ``extra=``.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class RedactionFilter(logging.Filter):
    """Scrubs secrets from the message, exception text and ``extra`` fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        for name in list(record.__dict__):
            if name in _STANDARD_ATTRS:
                continue
            if name.lower() in DROPPED_FIELDS:
                del record.__dict__[name]
            else:
                record.__dict__[name] = redact_value(record.__dict__[name], key=name)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, plus extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, value in record.__dict__.items():
            if name not in _STANDARD_ATTRS:
                payload[name] = value
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False, default=str)


def _is_debug_env() -> bool:
    return os.environ.get(DEBUG_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def configure_logging(
    *,
    debug: bool | None = None,
    log_file: Path | None = None,
    to_file: bool = True,
    max_bytes: int = 1_000_000,
    backup_count: int = 3,
) -> logging.Logger:
    """Configure the ``headwaters`` logger: stderr + rotating file, redacted, JSON lines.

    Idempotent: previous Headwaters handlers are removed first. ``debug`` defaults to the
    ``HEADWATERS_DEBUG`` environment variable. Never writes to stdout.
    """
    if debug is None:
        debug = _is_debug_env()
    logger = logging.getLogger(LOGGER_NAME)
    shutdown_logging()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if to_file:
        target = log_file or (paths.log_dir() / LOG_FILE_NAME)
        paths.long_safe(target.parent).mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                paths.long_safe(target),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
        )
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RedactionFilter())
        handler.set_name(_HANDLER_NAME)
        logger.addHandler(handler)
    return logger


def shutdown_logging() -> None:
    """Remove and close handlers installed by ``configure_logging``."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if handler.get_name() == _HANDLER_NAME:
            logger.removeHandler(handler)
            handler.close()


def get_logger(name: str | None = None) -> logging.Logger:
    """Child logger under ``headwaters`` (``get_logger('router')`` -> ``headwaters.router``)."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
````

## headwaters/core/plugins.py

````python
"""Versioned plugin interfaces.

Every plugin carries an ``api_version`` attribute. Core checks it at registration time, so an
incompatible plugin is refused instead of failing later. Interfaces are ``runtime_checkable``
Protocols, so ``isinstance`` verifies that the required members exist (it does not check
signatures; the contract tests in ``tests/contracts`` exercise behaviour).

Interfaces are versioned together: bump ``CURRENT_API_VERSION`` only for a breaking change and
keep ``SUPPORTED_API_VERSIONS`` listing every version core still accepts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

CURRENT_API_VERSION = 1
SUPPORTED_API_VERSIONS: frozenset[int] = frozenset({CURRENT_API_VERSION})


@runtime_checkable
class SurfacePlugin(Protocol):
    """A user-facing surface (tray, status widget, wizard)."""

    api_version: int
    surface_id: str

    def start(self) -> None: ...

    def stop(self) -> None: ...


@runtime_checkable
class VerifierPlugin(Protocol):
    """Checks a candidate answer; returns a list of problems (empty means it passed)."""

    api_version: int

    def verify(self, candidate: str) -> list[str]: ...


@runtime_checkable
class ContextPlugin(Protocol):
    """Builds extra repo context for a request within a token budget."""

    api_version: int

    def build_context(self, query: str, budget_tokens: int) -> str: ...


@runtime_checkable
class BridgePlugin(Protocol):
    """Integration with a host application (installer or hook support)."""

    api_version: int
    host_id: str

    def supports(self, host_id: str) -> bool: ...


@runtime_checkable
class DashboardPlugin(Protocol):
    """Contributes a section to the dashboard / status page."""

    api_version: int

    def render_summary(self) -> dict[str, object]: ...


@runtime_checkable
class LedgerPlugin(Protocol):
    """Records decision metadata (never prompts, outputs or keys)."""

    api_version: int

    def record(self, event: Mapping[str, object]) -> None: ...


@runtime_checkable
class ClassifierLayerPlugin(Protocol):
    """An extra classifier layer; returns a score per label."""

    api_version: int

    def classify(self, features: Mapping[str, float]) -> dict[str, float]: ...


@runtime_checkable
class EscalationPlugin(Protocol):
    """Decides whether a failed attempt should escalate to a stronger candidate."""

    api_version: int

    def should_escalate(self, attempt: int, problems: Sequence[str]) -> bool: ...


INTERFACES: dict[str, type] = {
    "SurfacePlugin": SurfacePlugin,
    "VerifierPlugin": VerifierPlugin,
    "ContextPlugin": ContextPlugin,
    "BridgePlugin": BridgePlugin,
    "DashboardPlugin": DashboardPlugin,
    "LedgerPlugin": LedgerPlugin,
    "ClassifierLayerPlugin": ClassifierLayerPlugin,
    "EscalationPlugin": EscalationPlugin,
}


class PluginContractError(ValueError):
    """A plugin does not satisfy its declared interface."""


def check_plugin(interface: str, plugin: object) -> None:
    """Raise ``PluginContractError`` unless ``plugin`` satisfies ``interface`` at a supported
    API version."""
    protocol = INTERFACES.get(interface)
    if protocol is None:
        raise PluginContractError(f"unknown plugin interface: {interface!r}")
    if not isinstance(plugin, protocol):
        raise PluginContractError(f"{type(plugin).__name__} does not implement {interface}")
    version = getattr(plugin, "api_version", None)
    if not isinstance(version, int) or isinstance(version, bool):
        raise PluginContractError(f"{type(plugin).__name__}.api_version must be an int")
    if version not in SUPPORTED_API_VERSIONS:
        raise PluginContractError(
            f"{type(plugin).__name__} targets api_version {version}; "
            f"supported: {sorted(SUPPORTED_API_VERSIONS)}"
        )
````

## headwaters/core/policy/__init__.py

````python

````

## headwaters/core/policy/decide.py

````python
"""Policy engine B: the decision ladder (HDW-114, v3 section 4.5, v3.3 section B4).

``decide(req, ctx)`` picks a model inside the effective scope. Ladder (first = decision,
the rest = fallbacks):

    0. pin, then force overrides (the hard filter still applies, so locks always win)
    1. hard filter (requirements, availability, hardware fit, scope)
    2. cheap rung      best of {local, free cloud} with score >= floor (local wins ties)
    3. optimistic rung within margin of the floor AND verifiable (code/JSON); only when an
                       ``EscalationPlugin`` is registered
    4. paid rung       the user's paid primary, then the OpenRouter auto router
    5. best effort     cheap models below the floor (``below_floor``, verification mandatory),
                       then the OpenRouter free router if enabled and complexity allows
    6. ``NoRouteError`` with reset ETAs and suggestions

``primary_first`` moves the paid rung to the front. In local scope nothing leaves the machine:
escalation is the larger local model (it ranks higher) and then an error. Premium (Fusion) is
only chosen by an explicit force. Battery or a busy GPU only add a ``headwaters/cloud``
suggestion. Pure functions; never touches request text.
"""

from __future__ import annotations

from headwaters.core.policy.filters import Candidate, Rejection, hard_filter
from headwaters.core.policy.ladder import build_ladder, no_route, suggestions_for
from headwaters.core.policy.scoring import floor_for, rank_candidates
from headwaters.core.policy.types import (
    FORCE_PREMIUM,
    Entry,
    PolicyContext,
    PolicyRequest,
    RouteDecision,
    Rung,
)


def _override(
    kept: list[Candidate], rejected: tuple[Rejection, ...], req: PolicyRequest, ctx: PolicyContext
) -> tuple[Candidate, Rung, str] | None:
    """Pin beats force. An override must still pass the hard filter."""
    if req.pin:
        match = next((c for c in kept if c.id == req.pin), None)
        if match is None:
            why = next((r.reason for r in rejected if r.model_id == req.pin), "unknown_model")
            msg = f"Pinned model {req.pin!r} cannot serve this request ({why})."
            raise no_route("pin_rejected", msg, rejected, ctx.candidates, req)
        return match, "pin", f"pinned {match.id}"
    if req.force == FORCE_PREMIUM:
        premium = next((c for c in kept if c.role == "premium"), None)
        if premium is None:
            msg = "Premium is not available in this scope."
            raise no_route("premium_unavailable", msg, rejected, ctx.candidates, req)
        return premium, "force", "forced premium"
    return None


def _decision(
    entry: Entry, rest: list[Entry], req: PolicyRequest, ctx: PolicyContext, reasons: list[str]
) -> RouteDecision:
    cand, b, rung = entry
    below = not b.meets_floor
    tail = f", below floor by {b.below_floor_by:.1f}" if below else ""
    reasons.append(f"{rung}: {cand.id} score {b.total:.1f}, floor {b.floor:.0f}{tail}")
    return RouteDecision(
        model=cand.id,
        tier=cand.tier,
        score=b.total,
        floor=b.floor,
        below_floor=below,
        scope=req.scope,
        reasons=tuple(reasons),
        fallbacks=tuple(e[0].id for e in rest),
        suggestions=suggestions_for(cand, req, ctx),
        rung=rung,
        verify_required=below or rung == "optimistic",
        breakdown=b,
    )


def decide(req: PolicyRequest, ctx: PolicyContext) -> RouteDecision:
    """Run the ladder. Raises ``NoRouteError`` when nothing can serve the request."""
    reqs = req.requirements
    result = hard_filter(ctx.candidates, reqs.hard, req.scope, ctx.rules, ctx.hw)
    kept, rejected = list(result.kept), result.rejected
    floor = floor_for(reqs.soft, ctx.rules)
    reasons = [
        f"scope tiers: {', '.join(sorted(req.scope.tiers))}",
        *req.scope.reasons,
        f"floor {floor.value:.0f} for {reqs.soft.task}",
    ]
    forced = _override(kept, rejected, req, ctx)
    if forced is not None:
        cand, rung, label = forced
        args = (reqs.soft, reqs.est_tokens, req.scope.strategy, ctx.rules, ctx.hw)
        breakdown = rank_candidates([cand], *args)[0][1]
        reasons.append(label)
        rest = build_ladder([k for k in kept if k.id != cand.id and k.role != "premium"], req, ctx)
        return _decision((cand, breakdown, rung), rest, req, ctx, reasons)

    ladder = build_ladder([k for k in kept if k.role != "premium"], req, ctx)
    if ladder:
        return _decision(ladder[0], ladder[1:], req, ctx, reasons)
    has_normal = any(k.role == "normal" for k in kept)  # local models that passed the filter
    battery_local = ctx.hw is not None and ctx.hw.battery_policy == "local_only"
    if (req.scope.local_only or battery_local) and has_normal:
        msg = "No local model reaches the quality floor for this request."
        raise no_route("local_below_floor", msg, rejected, ctx.candidates, req)
    code = "no_route" if has_normal else "no_candidates"
    msg = "No model can serve this request in the current scope."
    raise no_route(code, msg, rejected, ctx.candidates, req)
````

## headwaters/core/policy/errors.py

````python
"""Structured routing errors (HDW-114). Never carries prompt text."""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.policy.filters import Rejection


@dataclass(frozen=True)
class Suggestion:
    """One next step for the user or the host. ``code`` is stable; ``text`` is for people."""

    code: str  # wait | split_task | pull_model | use_cloud | enable_free_router | ...
    text: str


class NoRouteError(Exception):
    """No model can serve the request inside the effective scope.

    ``etas`` maps a model id to the seconds until it is usable again (only known resets).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        etas: dict[str, float] | None = None,
        suggestions: tuple[Suggestion, ...] = (),
        rejected: tuple[Rejection, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.etas = dict(etas or {})
        self.suggestions = suggestions
        self.rejected = rejected

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "etas": self.etas,
            "suggestions": [{"code": s.code, "text": s.text} for s in self.suggestions],
            "rejected": [{"model": r.model_id, "reason": r.reason} for r in self.rejected],
        }
````

## headwaters/core/policy/filters.py

````python
"""Policy engine A, part 1: candidates and the hard filter (HDW-113, v3 section 4.5).

Hard filter = requirements (vision, tools, json, context >= headroom x tokens) intersected with
availability (walls/quotas), hardware fit (local models) and the effective scope tiers.
Pure functions: the router gathers candidates (catalog merge, quota tracker, hardware fit) and
this module only decides. No I/O, never touches request text.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from headwaters.core.analyzer.requirements import HardRequirements
from headwaters.core.hardware.fit import FitResult
from headwaters.core.hardware.residency import ResidencyInputs
from headwaters.core.hardware.state import HardwareState
from headwaters.core.quotas import Availability
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeDecision, TierName
from headwaters.core.state_models import ModelRow

_DEFAULT_TIER: dict[str, TierName] = {"local": "local", "free_cloud": "free_cloud", "paid": "paid"}

# ``normal`` models are ranked by score. The other roles are routes the router names (their
# slugs come from the catalog/discovery, never from this module): the OpenRouter auto router,
# the OpenRouter free router, and the premium (Fusion) route that is never picked automatically.
Role = Literal["normal", "openrouter_auto", "openrouter_free", "premium"]
_ROLE_TIER: dict[str, TierName] = {
    "openrouter_auto": "openrouter_auto",
    "openrouter_free": "free_cloud",
}


@dataclass(frozen=True)
class Candidate:
    """One model the router could call, with everything the policy needs to judge it.

    ``scores`` are 0..100 per task (user override already applied by the catalog merge).
    ``scope_tier`` tells the scope check which tier this model belongs to; it defaults from
    ``row.tier`` (a paid model is ``paid``; the router sets ``openrouter_auto`` for that route).
    ``fit`` is required for local models. ``expected_tps`` is the catalog speed for this GPU
    class. ``is_primary`` marks the user's configured paid primary model(s).
    """

    row: ModelRow
    scores: Mapping[str, float]
    availability: Availability = field(default_factory=lambda: Availability("ok"))
    fit: FitResult | None = None
    residency: ResidencyInputs | None = None
    expected_tps: float | None = None
    is_primary: bool = False
    scope_tier: TierName | None = None
    role: Role = "normal"

    @property
    def id(self) -> str:
        return self.row.id

    @property
    def is_local(self) -> bool:
        return self.row.tier == "local"

    @property
    def tier(self) -> TierName:
        if self.scope_tier is not None:
            return self.scope_tier
        if self.role in _ROLE_TIER:
            return _ROLE_TIER[self.role]
        return _DEFAULT_TIER.get(self.row.tier, "paid")


@dataclass(frozen=True)
class Rejection:
    model_id: str
    reason: str  # stable code, e.g. no_vision, no_tools, ctx_too_small, walled, scope


@dataclass(frozen=True)
class FilterResult:
    kept: tuple[Candidate, ...]
    rejected: tuple[Rejection, ...]


def _first_failure(
    c: Candidate,
    hard: HardRequirements,
    scope: ScopeDecision,
    hw: HardwareState | None,
    rules: Rules,
) -> str | None:
    if c.tier not in scope.tiers:
        return "scope"
    if not c.availability.usable:
        return f"unavailable_{c.availability.status}"
    if c.is_local:
        if c.fit is None:
            return "no_fit_data"
        if not c.fit.usable:
            return f"no_fit_{c.fit.reason}"
    elif hw is not None and hw.battery_policy == "local_only":
        return "on_battery_local_only"
    if hard.vision and "image" not in c.row.modalities:
        return "no_vision"
    if hard.tools and c.row.tools_ok is not True:
        return "no_tools"
    if hard.json and c.row.json_ok is not True:
        return "no_json"
    needed = math.ceil(rules.scoring.ctx_headroom * hard.min_ctx)
    if c.row.ctx is None or c.row.ctx < needed:
        return "ctx_too_small"
    return None


def hard_filter(
    candidates: Iterable[Candidate],
    hard: HardRequirements,
    scope: ScopeDecision,
    rules: Rules,
    hw: HardwareState | None = None,
) -> FilterResult:
    """Keep candidates that meet every hard requirement; say why the others were dropped."""
    kept: list[Candidate] = []
    rejected: list[Rejection] = []
    for c in candidates:
        why = _first_failure(c, hard, scope, hw, rules)
        if why is None:
            kept.append(c)
        else:
            rejected.append(Rejection(c.id, why))
    return FilterResult(tuple(kept), tuple(rejected))
````

## headwaters/core/policy/ladder.py

````python
"""Ladder construction and error/suggestion helpers (HDW-114). See ``decide`` for the rungs."""

from __future__ import annotations

from collections.abc import Sequence

from headwaters.core.policy.errors import NoRouteError, Suggestion
from headwaters.core.policy.filters import Candidate, Rejection
from headwaters.core.policy.scoring import ScoreBreakdown, rank_candidates
from headwaters.core.policy.types import Entry, PolicyContext, PolicyRequest, Rung
from headwaters.core.scope import TierName

_CHEAP_TIERS: frozenset[TierName] = frozenset({"local", "free_cloud"})
_VERIFIABLE_TASKS = frozenset({"code"})  # plus any request that asks for JSON


def _entries(
    ranked: list[tuple[Candidate, ScoreBreakdown]], rung: Rung, only_ok: bool | None = None
) -> list[Entry]:
    return [(c, b, rung) for c, b in ranked if only_ok is None or b.meets_floor == only_ok]


def suggestions_for(
    chosen: Candidate, req: PolicyRequest, ctx: PolicyContext
) -> tuple[Suggestion, ...]:
    """Battery / busy GPU: suggest ``headwaters/cloud``; never leave local automatically."""
    hw = ctx.hw
    if hw is None or not chosen.is_local or req.scope.locks:  # a lock forbids the hint
        return ()
    if hw.battery_policy == "prefer_cloud":
        why = "on battery"
    elif hw.gpu_busy.busy:
        why = "the GPU is busy"
    else:
        return ()
    text = f"Running locally while {why}; pick headwaters/cloud to use cloud models instead."
    return (Suggestion("use_cloud", text),)


def no_route(
    code: str,
    message: str,
    rejected: tuple[Rejection, ...],
    cands: Sequence[Candidate],
    req: PolicyRequest,
) -> NoRouteError:
    etas = {
        c.id: c.availability.eta
        for c in cands
        if c.availability.eta is not None and not c.availability.usable
    }
    codes = {r.reason for r in rejected}
    tips: list[Suggestion] = []
    if etas:
        tips.append(Suggestion("wait", f"Wait about {int(min(etas.values()))}s for a reset."))
    if "ctx_too_small" in codes:
        tips.append(Suggestion("split_task", "Split the task or send less context."))
    if any(x.startswith("no_fit_") for x in codes) and "local" in req.scope.tiers:
        tips.append(Suggestion("pull_model", "Pull a local model that fits your GPU."))
    if codes & {"no_vision", "no_tools", "no_json"}:
        tips.append(Suggestion("add_capable_model", "No model supports what this needs."))
    if code == "local_below_floor":
        tips.append(Suggestion("split_task", "Split the task into smaller steps."))
        tips.append(Suggestion("pull_model", "Pull a larger local model that fits your GPU."))
    if req.scope.local_only and not req.scope.locks:
        tips.append(Suggestion("use_cloud", "Use headwaters/cloud to allow cloud models."))
    return NoRouteError(code, message, etas=etas, suggestions=tuple(tips), rejected=rejected)


def build_ladder(kept: list[Candidate], req: PolicyRequest, ctx: PolicyContext) -> list[Entry]:
    """Ordered candidates: first is the decision, the rest are fallbacks (no duplicates)."""
    rules, reqs = ctx.rules, req.requirements
    soft, strategy = reqs.soft, req.scope.strategy

    def ranked(items: list[Candidate]) -> list[tuple[Candidate, ScoreBreakdown]]:
        return rank_candidates(items, soft, reqs.est_tokens, strategy, rules, ctx.hw)

    normal = [c for c in kept if c.role == "normal"]
    cheap = ranked([c for c in normal if c.tier in _CHEAP_TIERS])
    primaries = ranked([c for c in normal if c.tier == "paid" and c.is_primary])
    auto = ranked([c for c in kept if c.role == "openrouter_auto"])
    free_router = ranked([c for c in kept if c.role == "openrouter_free"])

    paid = _entries(primaries, "paid") + _entries(auto, "paid")
    cheap_ok = _entries(cheap, "cheap", True)
    cheap_low = _entries(cheap, "best_effort", False)
    verifiable = soft.task in _VERIFIABLE_TASKS or reqs.hard.json

    ladder: list[Entry] = []
    if strategy == "primary_first":
        ladder += [e for e in paid if e[1].meets_floor]
    ladder += cheap_ok
    if not cheap_ok and ctx.escalation is not None and verifiable and cheap_low:
        near = cheap_low[0]
        if near[1].below_floor_by <= rules.floors.optimistic_margin:
            ladder.append((near[0], near[1], "optimistic"))
    ladder += paid
    battery_local = ctx.hw is not None and ctx.hw.battery_policy == "local_only"
    if not (req.scope.local_only or battery_local):  # local never goes below the floor: errors
        ladder += cheap_low
        cap = rules.tiers.openrouter_free_router
        if cap.enabled and soft.complexity <= cap.max_complexity:
            ladder += _entries(free_router, "free_router")
    seen: set[str] = set()
    unique: list[Entry] = []
    for entry in ladder:
        if entry[0].id not in seen:
            seen.add(entry[0].id)
            unique.append(entry)
    return unique
````

## headwaters/core/policy/scoring.py

````python
"""Policy engine A, part 2: quality floor and score (HDW-113, v3 section 4.5).

    floor = base[task] + k[task] * complexity + high_risk_bonus * (risk == high)
            + agentic_bonus * agentic          (agentic also needs agentic_tools >= floor)
    score = quality[task] - complexity_penalty + warm_bonus - latency_penalty
            - evict_penalty - cost_weight * cost

Every number comes from ``rules.yaml`` (``floors`` and ``scoring``); nothing is hard-coded here.
Strategies (cost_saver, quality_first, primary_first) only change the weights. Pure functions,
no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.analyzer.requirements import SoftRequirements
from headwaters.core.hardware.state import HardwareState
from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import FloorRule, Rules, Strategy, StrategyWeights

AGENTIC_TASK = "agentic_tools"
HIGH_RISK = "high"
_TOKENS_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class FloorResult:
    value: float
    task_base: float
    complexity_part: float
    risk_part: float
    agentic_part: float


@dataclass(frozen=True)
class ScoreBreakdown:
    """Every term of the score, so decisions can be explained and tested term by term."""

    model_id: str
    quality: float
    complexity_penalty: float
    warm_bonus: float
    latency_penalty: float
    evict_penalty: float
    cost_usd: float
    cost_penalty: float
    soft_penalty: float
    primary_bonus: float
    total: float
    floor: float
    meets_floor: bool
    below_floor_by: float  # 0 when the floor is met


def strategy_weights(rules: Rules, strategy: Strategy) -> StrategyWeights:
    return getattr(rules.scoring.strategies, strategy)  # type: ignore[no-any-return]


def floor_for(soft: SoftRequirements, rules: Rules) -> FloorResult:
    """``base[task] + k[task]*complexity + high_risk_bonus + agentic_bonus``."""
    rule = getattr(rules.floors, soft.task, None)
    if not isinstance(rule, FloorRule):
        raise ValueError(f"no floor configured for task {soft.task!r}")
    complexity = rule.per_complexity * soft.complexity
    risk = rules.floors.high_risk_bonus if soft.risk == HIGH_RISK else 0
    agentic = rules.floors.agentic_bonus if soft.agentic else 0
    return FloorResult(
        float(rule.base + complexity + risk + agentic), rule.base, complexity, risk, agentic
    )


def meets_floor(c: Candidate, soft: SoftRequirements, floor: float) -> bool:
    """Task quality must reach the floor; agentic requests also need ``agentic_tools``."""
    if c.scores.get(soft.task, 0.0) < floor:
        return False
    return not (soft.agentic and c.scores.get(AGENTIC_TASK, 0.0) < floor)


def estimate_cost_usd(c: Candidate, est_tokens: int, rules: Rules) -> float:
    """USD for the request using catalog prices (per million tokens). Unpriced models cost 0."""
    price_in = c.row.price_in or 0.0
    price_out = c.row.price_out or 0.0
    out = rules.scoring.assumed_output_tokens
    return (est_tokens * price_in + out * price_out) / _TOKENS_PER_MILLION


def _latency_penalty(c: Candidate, hw: HardwareState | None, rules: Rules) -> float:
    s = rules.scoring
    penalty = 0.0
    if c.expected_tps is not None and c.expected_tps > 0:
        slowdown = max(0.0, s.latency_ref_tps / c.expected_tps - 1.0)
        penalty += min(s.latency_max_penalty, s.latency_weight * slowdown)
    if c.is_local and hw is not None:
        if hw.battery_policy == "prefer_cloud":  # already "normal" on AC (HDW-108)
            penalty += s.on_battery_local_penalty
        if hw.gpu_busy.busy:
            penalty += s.gpu_busy_local_penalty
    return penalty


def score_candidate(
    c: Candidate,
    soft: SoftRequirements,
    est_tokens: int,
    strategy: Strategy,
    rules: Rules,
    hw: HardwareState | None = None,
) -> ScoreBreakdown:
    s = rules.scoring
    w = strategy_weights(rules, strategy)
    floor = floor_for(soft, rules).value
    quality = float(c.scores.get(soft.task, 0.0))
    complexity_penalty = s.complexity_penalty_per_level * max(0, soft.complexity - 1)
    res = c.residency
    warm = res.warm_bonus * s.residency_scale * w.warm_mult if res else 0.0
    evict = res.eviction_penalty * s.residency_scale * w.evict_mult if res else 0.0
    latency = _latency_penalty(c, hw, rules) * w.latency_mult
    cost = estimate_cost_usd(c, est_tokens, rules)
    cost_penalty = w.cost_weight * cost
    soft_penalty = s.soft_quota_penalty if c.availability.status == "soft" else 0.0
    primary = w.primary_bonus if c.is_primary else 0.0
    total = (
        quality
        - complexity_penalty
        + warm
        - latency
        - evict
        - cost_penalty
        - soft_penalty
        + primary
    )
    ok = meets_floor(c, soft, floor)
    gap = max(0.0, floor - c.scores.get(soft.task, 0.0))
    if soft.agentic:
        gap = max(gap, floor - c.scores.get(AGENTIC_TASK, 0.0), 0.0)
    return ScoreBreakdown(
        c.id, quality, complexity_penalty, warm, latency, evict, cost, cost_penalty,
        soft_penalty, primary, total, floor, ok, 0.0 if ok else gap,
    )  # fmt: skip


def rank_candidates(
    candidates: list[Candidate],
    soft: SoftRequirements,
    est_tokens: int,
    strategy: Strategy,
    rules: Rules,
    hw: HardwareState | None = None,
) -> list[tuple[Candidate, ScoreBreakdown]]:
    """Best first. Ties go to local, then to the model id (stable, deterministic)."""
    scored = [(c, score_candidate(c, soft, est_tokens, strategy, rules, hw)) for c in candidates]
    scored.sort(key=lambda p: (-p[1].total, not p[0].is_local, p[0].id))
    return scored
````

## headwaters/core/policy/types.py

````python
"""Request, context and result types of the decision ladder (HDW-114)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.hardware.state import HardwareState
from headwaters.core.plugins import EscalationPlugin
from headwaters.core.policy.errors import Suggestion
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.scoring import ScoreBreakdown
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeDecision, TierName

Rung = Literal["pin", "force", "cheap", "optimistic", "paid", "best_effort", "free_router"]
FORCE_PREMIUM = "premium"


@dataclass(frozen=True)
class PolicyRequest:
    requirements: Requirements
    scope: ScopeDecision
    pin: str | None = None  # a model id the user pinned
    force: str | None = None  # "premium" (Fusion) is the only force flag in the core


@dataclass(frozen=True)
class PolicyContext:
    candidates: Sequence[Candidate]
    rules: Rules
    hw: HardwareState | None = None
    escalation: EscalationPlugin | None = None


@dataclass(frozen=True)
class RouteDecision:
    model: str
    tier: TierName
    score: float
    floor: float
    below_floor: bool
    scope: ScopeDecision
    reasons: tuple[str, ...]
    fallbacks: tuple[str, ...]
    suggestions: tuple[Suggestion, ...]
    rung: Rung
    verify_required: bool  # the verification gate is mandatory (below floor or optimistic)
    breakdown: ScoreBreakdown


Entry = tuple[Candidate, ScoreBreakdown, Rung]
````

## headwaters/core/project_config.py

````python
"""Per-project config (``.headwaters.yaml``), discovered by walking up from the workspace.

``privacy`` can only tighten routing: ``local`` > ``free`` > ``any``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from headwaters.core import paths
from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError, format_validation_error, read_yaml_mapping

PROJECT_FILE_NAME = ".headwaters.yaml"

Privacy = Literal["local", "free", "any"]
# Higher = tighter.
_PRIVACY_RANK: dict[str, int] = {"any": 0, "free": 1, "local": 2}
# Scopes a privacy level still allows (rules.default_scope values).
_SCOPE_RANK: dict[str, int] = {"auto": 0, "cloud": 0, "free": 1, "local": 2}


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    privacy: Privacy = "any"
    checks: list[str] = Field(default_factory=list)


def find_project_file(start: Path) -> Path | None:
    """Walk up from ``start`` (a directory or a file) to the nearest ``.headwaters.yaml``."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / PROJECT_FILE_NAME
        if paths.long_safe(candidate).is_file():
            return candidate
    return None


def load_project_config(workspace: Path) -> tuple[ProjectConfig, Path | None]:
    """Return the nearest project config and its path, or defaults and ``None``."""
    found = find_project_file(workspace)
    if found is None:
        return ProjectConfig(), None
    data = read_yaml_mapping(found)
    try:
        return ProjectConfig.model_validate(data), found
    except ValidationError as exc:
        raise ConfigError(found, format_validation_error(exc)) from exc


def effective_scope(rules: Rules, project: ProjectConfig) -> str:
    """Default scope after the project rule is applied (tighten only, never loosen).

    ``private_lock`` and the other locks are resolved by the policy layer, not here.
    """
    scope = rules.default_scope
    if _PRIVACY_RANK[project.privacy] > _SCOPE_RANK[scope]:
        return project.privacy
    return scope
````

## headwaters/core/router.py

````python
"""Router façade: the one core API every engine calls (HDW-117, v3 section 2, v3.4 section A).

``Router.handle(request)`` runs the whole pipeline:

    1. analyze          requirements (layers 1-3)
    2. resolve scope    Private lock > project rule > secret > requested > default
    3. decide           the policy ladder picks a model and its fallbacks
    4. execute          cloud through litellm, local through Ollama
    5. classify errors  wall events feed the quota tracker; cascade to the next candidate
                        (bounded attempts; a stream only fails over BEFORE its first token)
    6. verify           registered verifiers, per the ``verify`` setting
    7. escalate         ``EscalationPlugin`` when present; without one a failed check is reported
    8. log              one decision row and one outcome row (metadata only)

``handle`` returns an awaitable ``RouteResult`` or, when ``request.stream`` is set, an async
iterator of ``RouteEvent``; ``run`` and ``stream`` are the typed forms of the same thing.

Failure contract: ``ScopeError`` and ``NoRouteError`` (already structured) are raised as they are
when the first decision cannot be made. Once attempts have been made, running out of candidates
or attempts raises ``RouteFailed``. Local scope is fail-closed: candidates outside the effective
scope never reach the policy's choice, the executor refuses cloud tiers again, and a failure is a
structured error, never a cloud fallback. Streaming does not escalate (the caller already has the
tokens); a failed check is reported on the final result.

Privacy: this module never logs or stores request text, only decision metadata.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from headwaters.core import router_log
from headwaters.core.analyzer.requirements import AnalyzerInput, Requirements
from headwaters.core.executor.errors import ExecutorError, ScopeViolation
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import ExecResult
from headwaters.core.features import FeatureRegistry, get_registry
from headwaters.core.hardware.residency import ResidencyConfig
from headwaters.core.hardware.state import HardwareState
from headwaters.core.plugins import EscalationPlugin, LedgerPlugin, VerifierPlugin
from headwaters.core.policy.decide import decide
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Candidate, hard_filter
from headwaters.core.policy.types import PolicyContext, PolicyRequest, RouteDecision
from headwaters.core.quotas import QuotaTracker, WallDecision
from headwaters.core.router_log import LogEntry
from headwaters.core.router_plan import RoutePlan
from headwaters.core.router_prep import (
    analyzer_input,
    executor_messages,
    executor_options,
    local_usable,
    only_local,
    scan_texts,
    with_warm_preference,
)
from headwaters.core.router_run import RouteRun
from headwaters.core.router_session import session_key
from headwaters.core.router_types import (
    Attempt,
    ProgressEvent,
    RouteEvent,
    RouteFailed,
    RouteRequest,
    RouteResult,
    Stage,
    VerifyOutcome,
)
from headwaters.core.router_verify import run_verifiers, wants_verify
from headwaters.core.rules import Rules
from headwaters.core.scope import (
    ScopeDecision,
    ScopeError,
    paid_scopes_available,
    resolve_scope,
)
from headwaters.core.secrets_detect import SecretDetector, SecretMatch
from headwaters.core.state import Database
from headwaters.core.state_misc import SessionRow, get_session, pin_session

__all__ = ["CandidateSource", "RequirementsAnalyzer", "RoutePlan", "Router", "RouterConfig"]

CandidateSource = Callable[[Requirements], Sequence[Candidate]]


class RequirementsAnalyzer(Protocol):
    """``Analyzer`` and ``AnalyzerChain`` both satisfy this."""

    def analyze(self, inp: AnalyzerInput) -> Requirements: ...


@dataclass(frozen=True)
class RouterConfig:
    """``max_attempts`` bounds every model call of one request (retries, cascades and
    escalations together). First-draft value, see docs/ASSUMPTIONS.md."""

    max_attempts: int = 4
    residency: ResidencyConfig = field(default_factory=ResidencyConfig)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")


@dataclass(frozen=True)
class _Selection:
    """Steps 2-3 of the pipeline: the scope, the candidates it was decided on, the first pick."""

    scope: ScopeDecision
    candidates: tuple[Candidate, ...]
    hw: HardwareState | None
    first: RouteDecision


class Router:
    def __init__(
        self,
        rules: Rules,
        db: Database,
        analyzer: RequirementsAnalyzer,
        candidates: CandidateSource,
        executor: Executor,
        quotas: QuotaTracker,
        *,
        secrets: SecretDetector | None = None,
        verifiers: Sequence[VerifierPlugin] = (),
        registry: FeatureRegistry | None = None,
        hardware: Callable[[], HardwareState | None] | None = None,
        config: RouterConfig | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._rules = rules
        self._db = db
        self._analyzer = analyzer
        self._candidates = candidates
        self._executor = executor
        self._quotas = quotas
        self._secrets = secrets
        self._verifiers = tuple(verifiers)
        self._registry = registry if registry is not None else get_registry()
        self._hardware = hardware
        self._config = config or RouterConfig()
        self._sleep = sleep

    # ------------------------------------------------------------------ public API

    def handle(
        self, request: RouteRequest
    ) -> Coroutine[Any, Any, RouteResult] | AsyncIterator[RouteEvent]:
        """``request.stream`` False -> awaitable ``RouteResult``; True -> async ``RouteEvent``s."""
        return self.stream(request) if request.stream else self.run(request)

    async def run(self, request: RouteRequest) -> RouteResult:
        run = await self._prepare(request)
        decision = run.first
        while True:
            decision, result = await self._execute(run, decision)
            verify = self._verify(run, decision, result)
            if verify is not None and verify.failed:
                run.attempts.append(Attempt(decision.model, decision.tier, "verify_failed"))
                nxt = self._escalate(run, decision, verify)
                if nxt is not None:
                    decision = nxt
                    continue
            else:
                run.attempts.append(Attempt(decision.model, decision.tier, "ok"))
            return self._finish(run, decision, result, verify)

    async def stream(self, request: RouteRequest) -> AsyncIterator[RouteEvent]:
        run = await self._prepare(request)
        decision = run.first
        while True:
            events = self._executor.run(decision, run.messages, request.tools, True, run.options)
            seen_first = False
            result: ExecResult | None = None
            failure: ExecutorError | None = None
            try:
                async for event in events:
                    if event.kind == "first_token":
                        seen_first = True
                        self._emit(run, "executing", decision, "first_token")
                        yield RouteEvent("route", decision=decision)
                    elif event.kind == "done":
                        result = event.result
                    elif event.kind == "error":
                        failure = event.error
                    yield RouteEvent("stream", event=event)
            except ExecutorError as exc:
                failure = exc
            finally:
                aclose = getattr(events, "aclose", None)
                if aclose is not None:
                    await aclose()
            if failure is None and result is not None:
                verify = self._verify(run, decision, result)
                failed = verify is not None and verify.failed
                run.attempts.append(
                    Attempt(decision.model, decision.tier, "verify_failed" if failed else "ok")
                )
                yield RouteEvent("final", result=self._finish(run, decision, result, verify))
                return
            if failure is None:
                failure = ExecutorError(
                    "no_result", "The model returned no result.", model=decision.model
                )
            if seen_first or not failure.before_first_token:
                # Tokens already reached the caller: never switch models mid-stream.
                self._record_wall(run, decision, failure)
                run.attempts.append(Attempt(decision.model, decision.tier, failure.code))
                self._log_failure(run, decision, failure.code)
                yield RouteEvent("final", error=failure)
                return
            decision = await self._recover(run, decision, failure)

    # ------------------------------------------------------------------ steps 1-3

    async def plan(self, request: RouteRequest) -> RoutePlan:
        """Dry run of steps 1-3: which model would answer, and why. For ``headwaters pick``.

        Calls no model, writes nothing (no decision row, no session pin) and never logs
        request text. Raises ``ScopeError`` / ``NoRouteError`` exactly as ``handle`` would.
        """
        key = session_key(
            messages=request.messages,
            system=request.system,
            headers=request.headers,
            explicit=request.session_id,
        )
        reqs = await asyncio.to_thread(self._analyzer.analyze, analyzer_input(request))
        chosen = self._select(request, key, reqs)
        rejected = hard_filter(
            chosen.candidates, reqs.hard, chosen.scope, self._rules, chosen.hw
        ).rejected
        return RoutePlan(reqs, chosen.scope, chosen.first, chosen.candidates, rejected, key)

    async def _prepare(self, request: RouteRequest) -> RouteRun:
        started = time.monotonic()
        key = session_key(
            messages=request.messages,
            system=request.system,
            headers=request.headers,
            explicit=request.session_id,
        )
        self._progress(request, "analyzing")
        reqs = await asyncio.to_thread(self._analyzer.analyze, analyzer_input(request))
        scope_name = request.scope or self._rules.default_scope
        try:
            chosen = self._select(request, key, reqs)
        except (ScopeError, NoRouteError) as exc:
            self._log_early(request, key, started, reqs, scope_name, exc)
            raise
        return RouteRun(
            request=request,
            key=key,
            started=started,
            reqs=reqs,
            scope=chosen.scope,
            candidates=chosen.candidates,
            hw=chosen.hw,
            messages=executor_messages(request),
            options=executor_options(request),
            first=chosen.first,
        )

    def _select(self, request: RouteRequest, key: str, reqs: Requirements) -> _Selection:
        """Steps 2-3: secrets, candidates, scope and the policy ladder. No side effects."""
        matches = self._scan_secrets(request)
        candidates = self._with_live_availability(self._candidates(reqs))
        if self._rules.airplane:
            candidates = only_local(candidates)
        session = self._get_session(key)
        warm = session.pinned_model if session is not None else None
        candidates = with_warm_preference(candidates, warm, self._config.residency)
        hw = self._hardware() if self._hardware is not None else None
        self._progress(request, "scope")
        scope = resolve_scope(
            self._rules,
            request.project,
            requested=request.scope,
            strategy=request.strategy,
            verify=request.verify,
            secret_matches=matches,
            secret_override=request.secret_override,
            paid_scopes=paid_scopes_available(self._registry),
            local_available=local_usable(candidates),
        )
        self._progress(request, "deciding")
        first = decide(
            PolicyRequest(reqs, scope, request.pin, request.force),
            PolicyContext(candidates, self._rules, hw, self._escalation()),
        )
        return _Selection(scope, candidates, hw, first)

    def _with_live_availability(self, candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
        """Walls live in shared SQLite, so another engine may have set one a moment ago.

        The tracker is read on every request; a wall or cooldown it knows about wins over what
        the candidate source said, while ``ok`` leaves the source's own reading alone.
        """
        out: list[Candidate] = []
        for c in candidates:
            live = self._quotas.availability(c.id, c.row.provider)
            out.append(c if live.status == "ok" else replace(c, availability=live))
        return tuple(out)

    def _scan_secrets(self, request: RouteRequest) -> list[SecretMatch]:
        if self._secrets is None:
            return []
        return self._secrets.scan_all(scan_texts(request))

    def _get_session(self, key: str) -> SessionRow | None:
        try:
            return get_session(self._db, key)
        except Exception:  # a locked or missing database must not block routing
            return None

    # ------------------------------------------------------------------ steps 4-5

    async def _execute(
        self, run: RouteRun, decision: RouteDecision
    ) -> tuple[RouteDecision, ExecResult]:
        """Run ``decision``; on a failure before any answer, retry or cascade (bounded)."""
        while True:
            self._emit(run, "executing", decision)
            try:
                result = await self._executor.run(
                    decision, run.messages, run.request.tools, False, run.options
                )
            except ExecutorError as exc:
                decision = await self._recover(run, decision, exc)
                continue
            return decision, result

    def _record_wall(
        self, run: RouteRun, decision: RouteDecision, exc: ExecutorError
    ) -> WallDecision | None:
        """Give the wall classifier's reading to the quota tracker (it persists the cooldown)."""
        if exc.wall is None:
            return None
        cand = run.candidate(decision.model)
        provider = cand.row.provider if cand is not None else exc.provider
        return self._quotas.record_wall(exc.wall, model=decision.model, provider=provider)

    async def _recover(
        self, run: RouteRun, decision: RouteDecision, exc: ExecutorError
    ) -> RouteDecision:
        """Handle a failed call: the decision to try next, or raise ``RouteFailed``."""
        if isinstance(exc, ScopeViolation):  # a scope breach is never retried or cascaded
            run.attempts.append(Attempt(decision.model, decision.tier, exc.code))
            self._log_failure(run, decision, exc.code)
            raise RouteFailed(
                exc.code, exc.message, attempts=run.attempts, last_error=exc.as_dict()
            ) from exc
        wall = self._record_wall(run, decision, exc)
        wait = wall.wait_s if wall is not None and wall.action == "retry" else 0.0
        run.attempts.append(Attempt(decision.model, decision.tier, exc.code, wait))
        if len(run.attempts) >= self._config.max_attempts:
            self._log_failure(run, decision, "attempts_exhausted")
            raise RouteFailed(
                "attempts_exhausted",
                "Every allowed attempt failed.",
                attempts=run.attempts,
                last_error=exc.as_dict(),
            ) from exc
        if wall is not None and wall.action == "retry":
            run.notes.append(f"{decision.model}: {exc.code}, retry in {wait:.1f}s")
            self._emit(run, "retrying", decision, exc.code)
            await self._sleep(wait)
            return decision
        run.excluded.add(decision.model)
        run.notes.append(f"{decision.model}: {exc.code}, cascading")
        self._emit(run, "cascading", decision, exc.code)
        try:
            return self._redecide(run, decision, exc.as_dict())
        except RouteFailed:
            self._log_failure(run, decision, "no_route_left")
            raise

    def _redecide(
        self, run: RouteRun, last: RouteDecision, last_error: Mapping[str, object] | None = None
    ) -> RouteDecision:
        """Decide again without the models already tried, using fresh walls and quotas."""
        fresh = self._with_live_availability(
            [c for c in run.candidates if c.id not in run.excluded]
        )
        try:
            return decide(
                PolicyRequest(run.reqs, run.scope, run.request.pin, run.request.force),
                PolicyContext(fresh, self._rules, run.hw, self._escalation()),
            )
        except NoRouteError as err:
            raise RouteFailed(
                "no_route_left",
                "No other model can serve this request in the current scope.",
                attempts=run.attempts,
                last_error=last_error,
                no_route=err,
            ) from err

    # ------------------------------------------------------------------ steps 6-7

    def _plugins(self, interface: str) -> list[Any]:
        return list(self._registry.plugins_for(interface))

    def _escalation(self) -> EscalationPlugin | None:
        found = self._plugins("EscalationPlugin")
        return found[0] if found else None

    def _ledgers(self) -> list[LedgerPlugin]:
        return self._plugins("LedgerPlugin")

    def _verify(
        self, run: RouteRun, decision: RouteDecision, result: ExecResult
    ) -> VerifyOutcome | None:
        if not wants_verify(
            run.scope.verify, agentic=run.reqs.soft.agentic, required=decision.verify_required
        ):
            return None
        self._emit(run, "verifying", decision)
        verifiers = [*self._verifiers, *self._plugins("VerifierPlugin")]
        return run_verifiers(
            verifiers,
            result,
            tools=run.request.tools,
            response_format=run.request.response_format,
        )

    def _escalate(
        self, run: RouteRun, decision: RouteDecision, verify: VerifyOutcome
    ) -> RouteDecision | None:
        """The next decision after a failed check, or ``None`` to report the failure as it is."""
        plugin = self._escalation()
        if plugin is None or len(run.attempts) >= self._config.max_attempts:
            return None
        try:
            wanted = plugin.should_escalate(run.escalations + 1, verify.problems)
        except Exception:  # a broken plugin means: do not escalate
            return None
        if not wanted:
            return None
        run.excluded.add(decision.model)
        try:
            nxt = self._redecide(run, decision)
        except RouteFailed:
            run.excluded.discard(decision.model)
            return None  # nothing stronger left: hand back the answer we have, flagged as failed
        run.escalations += 1
        run.notes.append(f"{decision.model}: verification failed, escalating to {nxt.model}")
        self._emit(run, "escalating", decision, "verify_failed")
        return nxt

    # ------------------------------------------------------------------ step 8

    def _finish(
        self,
        run: RouteRun,
        decision: RouteDecision,
        result: ExecResult,
        verify: VerifyOutcome | None,
    ) -> RouteResult:
        cand = run.candidate(decision.model)
        self._quotas.record_success(
            model=decision.model, provider=cand.row.provider if cand is not None else ""
        )
        cost = (
            router_log.cost_usd(cand.row.price_in, cand.row.price_out, result)
            if cand is not None
            else None
        )
        failed = verify is not None and verify.failed
        entry = self._entry(
            run,
            decision,
            "verify_failed" if failed else "ok",
            result=result,
            verify=verify,
            cost=cost,
            escalated=decision.model if run.escalations and not failed else None,
        )
        decision_id = router_log.write(self._db, entry)
        router_log.notify_ledgers(self._ledgers(), entry, decision_id)
        self._remember(run, decision)
        self._emit(run, "done", decision, "verify_failed" if failed else "ok")
        return RouteResult(
            result=result,
            decision=decision,
            session_key=run.key,
            attempts=tuple(run.attempts),
            decision_id=decision_id,
            verify=verify,
            first_choice=run.first.model if run.first.model != decision.model else None,
            reasons=(*decision.reasons, *run.notes),
        )

    def _entry(
        self,
        run: RouteRun,
        decision: RouteDecision | None,
        status: str,
        *,
        result: ExecResult | None = None,
        verify: VerifyOutcome | None = None,
        cost: float | None = None,
        escalated: str | None = None,
    ) -> LogEntry:
        return LogEntry(
            surface=run.request.surface,
            session=run.key,
            scope=run.scope.scope,
            strategy=run.scope.strategy,
            status=status,
            latency_ms=int((time.monotonic() - run.started) * 1000),
            locks=run.scope.locks,
            requirements=run.reqs,
            decision=decision,
            attempts=tuple(run.attempts),
            notes=tuple(run.notes),
            result=result,
            verify=verify,
            cost_usd=cost,
            escalated_to=escalated,
        )

    def _log_failure(self, run: RouteRun, decision: RouteDecision, status: str) -> None:
        entry = self._entry(run, decision, status)
        router_log.notify_ledgers(self._ledgers(), entry, router_log.write(self._db, entry))

    def _log_early(
        self,
        request: RouteRequest,
        key: str,
        started: float,
        reqs: Requirements,
        scope_name: str,
        exc: ScopeError | NoRouteError,
    ) -> None:
        """Log a request that never got a decision (a scope error or no candidate)."""
        entry = LogEntry(
            surface=request.surface,
            session=key,
            scope=scope_name,
            strategy=request.strategy or self._rules.strategy,
            status=exc.code,
            latency_ms=int((time.monotonic() - started) * 1000),
            requirements=reqs,
            notes=(f"{type(exc).__name__}: {exc.code}",),
        )
        router_log.notify_ledgers(self._ledgers(), entry, router_log.write(self._db, entry))

    def _remember(self, run: RouteRun, decision: RouteDecision) -> None:
        """Remember the answering model so the next turn of this session prefers it warm."""
        session = self._get_session(run.key)
        if session is not None and session.pinned_model == decision.model:
            return
        try:
            pin_session(self._db, run.key, decision.model, decision.tier)
        except Exception:  # best effort
            return

    # ------------------------------------------------------------------ progress

    @staticmethod
    def _progress(request: RouteRequest, stage: Stage, **kw: Any) -> None:
        if request.on_progress is None:
            return
        try:
            request.on_progress(ProgressEvent(stage, **kw))
        except Exception:  # a callback must never break routing
            return

    def _emit(self, run: RouteRun, stage: Stage, decision: RouteDecision, detail: str = "") -> None:
        self._progress(
            run.request, stage, attempt=len(run.attempts) + 1, model=decision.model, detail=detail
        )
````

## headwaters/core/router_log.py

````python
"""Decision and outcome logging for the router (HDW-117). Metadata only.

One ``decisions`` row per request plus one ``outcomes`` row, and an event for each registered
``LedgerPlugin``. Prompts, answers and keys never reach this module. A storage failure is logged
and swallowed: routing must not fail because the database is busy.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.executor.types import ExecResult
from headwaters.core.logging import get_logger
from headwaters.core.plugins import LedgerPlugin
from headwaters.core.policy.types import RouteDecision
from headwaters.core.router_types import Attempt, Surface, VerifyOutcome
from headwaters.core.state import Database
from headwaters.core.state_decisions import DecisionRow, OutcomeRow, insert_decision, save_outcome

_log = get_logger("router")
_TOKENS_PER_MILLION = 1_000_000.0


@dataclass(frozen=True)
class LogEntry:
    surface: Surface
    session: str
    scope: str
    strategy: str
    status: str  # ok | verify_failed | an error code
    latency_ms: int
    locks: Sequence[str] = ()
    requirements: Requirements | None = None
    decision: RouteDecision | None = None
    attempts: Sequence[Attempt] = ()
    notes: Sequence[str] = ()
    result: ExecResult | None = None
    verify: VerifyOutcome | None = None
    cost_usd: float | None = None
    escalated_to: str | None = None
    extra_reasons: Sequence[str] = field(default=())


def requirements_dict(req: Requirements | None) -> dict[str, Any]:
    return {} if req is None else asdict(req)


def verify_label(verify: VerifyOutcome | None) -> str | None:
    if verify is None:
        return None
    if not verify.ran:
        return "skipped"
    return "pass" if verify.passed else "fail"


def cost_usd(price_in: float | None, price_out: float | None, result: ExecResult) -> float:
    """Catalog prices are per million tokens; unpriced models cost 0."""
    tokens_in = result.usage.prompt_tokens or 0
    tokens_out = result.usage.completion_tokens or 0
    return (tokens_in * (price_in or 0.0) + tokens_out * (price_out or 0.0)) / _TOKENS_PER_MILLION


def _alternatives(entry: LogEntry) -> list[Any]:
    chosen = entry.decision.model if entry.decision else None
    tried: list[Any] = [
        {"model": a.model, "status": a.status}
        for a in entry.attempts
        if not (a.model == chosen and a.status in ("ok", "verify_failed"))
    ]
    if entry.decision is not None:
        tried.extend(entry.decision.fallbacks)
    return tried


def write(db: Database, entry: LogEntry) -> int | None:
    """Insert the decision and its outcome; return the decision id (None if storage failed)."""
    d = entry.decision
    reasons = [*(d.reasons if d else ()), *entry.notes, *entry.extra_reasons]
    row = DecisionRow(
        surface=entry.surface,
        scope=entry.scope,
        strategy=entry.strategy,
        locks_applied=list(entry.locks),
        session=entry.session,
        requirements=requirements_dict(entry.requirements),
        chosen=d.model if d else None,
        alternatives=_alternatives(entry),
        floor=d.floor if d else None,
        score=d.score if d else None,
        below_floor=d.below_floor if d else False,
        reasons=reasons,
    )
    usage = entry.result.usage if entry.result else None
    try:
        decision_id = insert_decision(db, row)
        save_outcome(
            db,
            OutcomeRow(
                decision_id=decision_id,
                status=entry.status,
                latency_ms=entry.latency_ms,
                tokens_in=usage.prompt_tokens if usage else None,
                tokens_out=usage.completion_tokens if usage else None,
                cost_usd=entry.cost_usd,
                verify_result=verify_label(entry.verify),
                escalated_to=entry.escalated_to,
            ),
        )
    except sqlite3.Error as exc:
        _log.warning("could not store routing decision (%s)", type(exc).__name__)
        return None
    return decision_id


def ledger_event(entry: LogEntry, decision_id: int | None) -> dict[str, object]:
    d = entry.decision
    usage = entry.result.usage if entry.result else None
    return {
        "decision_id": decision_id,
        "surface": entry.surface,
        "scope": entry.scope,
        "strategy": entry.strategy,
        "status": entry.status,
        "model": d.model if d else None,
        "tier": d.tier if d else None,
        "latency_ms": entry.latency_ms,
        "tokens_in": usage.prompt_tokens if usage else None,
        "tokens_out": usage.completion_tokens if usage else None,
        "cost_usd": entry.cost_usd,
        "attempts": len(entry.attempts),
        "verify": verify_label(entry.verify),
    }


def notify_ledgers(
    ledgers: Sequence[LedgerPlugin], entry: LogEntry, decision_id: int | None
) -> None:
    event: Mapping[str, object] = ledger_event(entry, decision_id)
    for ledger in ledgers:
        try:
            ledger.record(event)
        except Exception as exc:  # a plugin must never break routing
            _log.warning("ledger plugin failed (%s)", type(exc).__name__)
````

## headwaters/core/router_plan.py

````python
"""Result of a routing dry run (HDW-118): what ``Router.handle`` would do, without doing it."""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.policy.filters import Candidate, Rejection
from headwaters.core.policy.types import RouteDecision
from headwaters.core.scope import ScopeDecision

__all__ = ["RoutePlan"]


@dataclass(frozen=True)
class RoutePlan:
    """The first decision of a request plus the context needed to explain it.

    ``candidates`` carry the live wall/cooldown state the decision was made with, so a caller can
    show each fallback's tier. ``rejected`` lists the models the hard filter dropped and why
    (stable codes such as ``ctx_too_small``). Nothing here holds request text.
    """

    requirements: Requirements
    scope: ScopeDecision
    decision: RouteDecision
    candidates: tuple[Candidate, ...]
    rejected: tuple[Rejection, ...]
    session_key: str

    def candidate(self, model_id: str) -> Candidate | None:
        return next((c for c in self.candidates if c.id == model_id), None)
````

## headwaters/core/router_prep.py

````python
"""Request preparation for the router (HDW-117): messages, options, secrets, candidates.

Pure helpers. Nothing here stores or logs request text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from headwaters.core.analyzer.requirements import AnalyzerInput
from headwaters.core.executor.types import ExecOptions, Message
from headwaters.core.hardware.residency import ResidencyConfig, ResidencyInputs
from headwaters.core.policy.filters import Candidate
from headwaters.core.router_session import system_text, text_of
from headwaters.core.router_types import RouteRequest
from headwaters.core.router_verify import schema_of


def analyzer_input(request: RouteRequest) -> AnalyzerInput:
    return AnalyzerInput(
        messages=request.messages,
        system=request.system,
        tools=request.tools,
        response_format=request.response_format,
        file_paths=request.file_paths,
        headers=request.headers,
        max_output_tokens=request.max_output_tokens,
    )


def executor_messages(request: RouteRequest) -> list[Message]:
    """Messages for the executor. An Anthropic top-level ``system`` becomes a system message."""
    messages: list[Message] = list(request.messages)
    top = text_of(request.system)
    if top and not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": top})
    return messages


def executor_options(request: RouteRequest) -> ExecOptions:
    if request.options is not None:
        return request.options
    return ExecOptions(
        json_schema=schema_of(request.response_format), max_tokens=request.max_output_tokens
    )


def scan_texts(request: RouteRequest) -> list[str]:
    """Every text the secret scan must see: the system prompt and each message."""
    texts = [system_text(request.system, request.messages)]
    texts.extend(text_of(m.get("content")) for m in request.messages)
    return [t for t in texts if t]


def with_warm_preference(
    candidates: Sequence[Candidate], warm_model: str | None, config: ResidencyConfig | None = None
) -> tuple[Candidate, ...]:
    """Give the session's previous model the warm bonus (the policy turns it into a score term).

    Models Ollama already holds keep their own residency data; only a candidate without a warm
    flag gets the session preference.
    """
    if warm_model is None:
        return tuple(candidates)
    bonus = (config or ResidencyConfig()).warm_bonus
    out: list[Candidate] = []
    for c in candidates:
        if c.id == warm_model and not (c.residency is not None and c.residency.warm):
            c = replace(c, residency=ResidencyInputs(True, bonus, (), 0.0))
        out.append(c)
    return tuple(out)


def local_usable(candidates: Sequence[Candidate]) -> bool:
    """True when at least one local model could run right now (fits and not walled)."""
    return any(
        c.is_local and c.fit is not None and c.fit.usable and c.availability.usable
        for c in candidates
    )


def only_local(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """Airplane mode: nothing may leave the machine."""
    return tuple(c for c in candidates if c.is_local)
````

## headwaters/core/router_run.py

````python
"""Per-request working state of the router (HDW-117). Holds the request and routing metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.executor.types import ExecOptions, Message
from headwaters.core.hardware.state import HardwareState
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.types import RouteDecision
from headwaters.core.router_types import Attempt, RouteRequest
from headwaters.core.scope import ScopeDecision


@dataclass
class RouteRun:
    request: RouteRequest
    key: str
    started: float
    reqs: Requirements
    scope: ScopeDecision
    candidates: tuple[Candidate, ...]
    hw: HardwareState | None
    messages: list[Message]
    options: ExecOptions
    first: RouteDecision
    attempts: list[Attempt] = field(default_factory=list)
    excluded: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    escalations: int = 0

    def candidate(self, model_id: str) -> Candidate | None:
        return next((c for c in self.candidates if c.id == model_id), None)
````

## headwaters/core/router_session.py

````python
"""Session keying and text helpers for the router (HDW-117).

A session key is ``id:<x-session-id>`` when the host sends the header (or the caller passes an
explicit id), else ``h:`` plus a hash of the system prompt and the first user message. The hash
is one-way and only the key is stored: prompt text never reaches the database or the logs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

SESSION_HEADER = "x-session-id"
_MAX_ID_CHARS = 128
_HASH_CHARS = 32


def text_of(content: object) -> str:
    """Plain text of a message ``content``: a string, a text part or a list of parts."""
    if content is None or isinstance(content, bytes | bytearray):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        for key in ("text", "content"):
            value = content.get(key)
            if value is not None:
                return text_of(value)
        return ""
    if isinstance(content, Sequence):
        return "\n".join(t for t in (text_of(part) for part in content) if t)
    return ""


def system_text(
    system: str | Sequence[Mapping[str, Any]] | None, messages: Sequence[Mapping[str, Any]] = ()
) -> str:
    """The system prompt: the Anthropic top-level ``system`` or the first system message."""
    top = text_of(system)
    if top:
        return top
    for message in messages:
        if message.get("role") == "system":
            return text_of(message.get("content"))
    return ""


def header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    """Case-insensitive header lookup."""
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def session_key(
    *,
    messages: Sequence[Mapping[str, Any]],
    system: str | Sequence[Mapping[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
    explicit: str | None = None,
) -> str:
    """``x-session-id`` (or ``explicit``) wins; else hash(system prompt + first user message)."""
    given = (explicit or header_value(headers, SESSION_HEADER) or "").strip()
    if given:
        return "id:" + given[:_MAX_ID_CHARS]
    first_user = next(
        (text_of(m.get("content")) for m in messages if m.get("role") == "user"),
        "",
    )
    raw = system_text(system, messages) + "\x00" + first_user
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    return "h:" + digest[:_HASH_CHARS]
````

## headwaters/core/router_types.py

````python
"""Request, result and error types of the router façade (HDW-117).

Nothing here carries prompt or output text except ``RouteResult.result`` (the answer handed back
to the caller). Everything that is logged or stored is metadata.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.types import (
    ExecOptions,
    ExecResult,
    Message,
    StreamEvent,
    ToolSpec,
)
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.types import RouteDecision
from headwaters.core.project_config import ProjectConfig
from headwaters.core.rules import Scope, Strategy
from headwaters.core.scope import TierName, VerifyMode

Surface = Literal["gateway", "mcp", "hook", "cli"]
Stage = Literal[
    "analyzing",
    "scope",
    "deciding",
    "executing",
    "retrying",
    "cascading",
    "verifying",
    "escalating",
    "done",
]


@dataclass(frozen=True)
class ProgressEvent:
    """One step of a request. ``detail`` is short fixed text (a code), never request content."""

    stage: Stage
    attempt: int = 0
    model: str | None = None
    detail: str = ""


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True)
class RouteRequest:
    """One request from any engine (gateway, MCP, hook, CLI).

    ``scope``, ``strategy`` and ``verify`` come from the picker model (``headwaters/free+verify``)
    or an MCP parameter; ``None`` means the ``rules.yaml`` default. ``stream`` selects the shape
    ``Router.handle`` returns.
    """

    messages: Sequence[Message]
    surface: Surface = "gateway"
    system: str | Sequence[Mapping[str, Any]] | None = None
    tools: Sequence[ToolSpec] | None = None
    response_format: Mapping[str, Any] | str | None = None
    file_paths: Sequence[str] = ()
    headers: Mapping[str, str] | None = None
    max_output_tokens: int | None = None
    stream: bool = False
    scope: Scope | None = None
    strategy: Strategy | None = None
    verify: VerifyMode | None = None
    pin: str | None = None
    force: str | None = None
    session_id: str | None = None
    project: ProjectConfig | None = None
    secret_override: bool = False
    options: ExecOptions | None = None
    on_progress: ProgressCallback | None = None


@dataclass(frozen=True)
class Attempt:
    """One model call. ``status`` is ``ok``, ``verify_failed`` or an error code."""

    model: str
    tier: TierName
    status: str
    wait_s: float = 0.0


@dataclass(frozen=True)
class VerifyOutcome:
    """``passed`` is None when verification was wanted but no verifier could run (``ran`` False)."""

    ran: bool
    passed: bool | None
    problems: tuple[str, ...] = ()
    verifiers: int = 0

    @property
    def failed(self) -> bool:
        return self.ran and self.passed is False


@dataclass(frozen=True)
class RouteResult:
    """The answer plus how it was routed. ``decision`` is the one that produced ``result``."""

    result: ExecResult
    decision: RouteDecision
    session_key: str
    attempts: tuple[Attempt, ...]
    decision_id: int | None = None
    verify: VerifyOutcome | None = None
    first_choice: str | None = None  # what the policy picked first, when different from model
    reasons: tuple[str, ...] = field(default=())

    @property
    def verify_failed(self) -> bool:
        return self.verify is not None and self.verify.failed


RouteEventKind = Literal["route", "stream", "final"]


@dataclass(frozen=True)
class RouteEvent:
    """What a streaming ``Router.handle`` yields.

    ``route`` comes once, before the first token of the attempt that is streaming (so a gateway can
    set its headers). ``stream`` wraps an executor event. ``final`` ends the stream: ``result``
    on success, ``error`` when the stream broke after the first token.
    """

    kind: RouteEventKind
    decision: RouteDecision | None = None
    event: StreamEvent | None = None
    result: RouteResult | None = None
    error: ExecutorError | None = None


class RouteFailed(Exception):
    """Every bounded attempt failed (or nothing was left to try). Carries no request text."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        attempts: Sequence[Attempt] = (),
        last_error: Mapping[str, object] | None = None,
        no_route: NoRouteError | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.attempts = tuple(attempts)
        self.last_error = dict(last_error or {})
        self.no_route = no_route

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "attempts": [
                {"model": a.model, "tier": a.tier, "status": a.status} for a in self.attempts
            ],
            "last_error": self.last_error,
            "no_route": self.no_route.as_dict() if self.no_route else None,
        }
````

## headwaters/core/router_verify.py

````python
"""Verification step of the router (HDW-117, v3 section 4.6).

Runs every registered ``VerifierPlugin`` on the answer. ``BasicVerifier`` is called through its
richer ``verify_report`` so JSON schemas and tool-call arguments are checked too; other plugins
get the plain ``verify(candidate)``. A verifier that crashes is skipped, never fatal. Problems are
rendered reason codes, never the candidate text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from headwaters.core.executor.types import ExecResult
from headwaters.core.plugins import VerifierPlugin
from headwaters.core.router_types import VerifyOutcome
from headwaters.core.scope import VerifyMode
from headwaters.core.verify_basic import BasicVerifier


def wants_verify(mode: VerifyMode, *, agentic: bool, required: bool) -> bool:
    """``required`` (below the floor or an optimistic pick) beats ``off``: the gate is mandatory."""
    if required or mode == "always":
        return True
    return mode == "agentic" and agentic


def schema_of(response_format: Mapping[str, Any] | str | None) -> Mapping[str, Any] | None:
    """The JSON Schema inside an OpenAI ``response_format``, if it carries one."""
    if not isinstance(response_format, Mapping):
        return None
    inner = response_format.get("json_schema")
    schema = inner.get("schema") if isinstance(inner, Mapping) else None
    return schema if isinstance(schema, Mapping) else None


def _tool_calls(result: ExecResult) -> list[dict[str, Any]] | None:
    if not result.tool_calls:
        return None
    return [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in result.tool_calls]


def _one(
    verifier: VerifierPlugin,
    result: ExecResult,
    tools: Sequence[Mapping[str, Any]] | None,
    schema: Mapping[str, Any] | None,
) -> list[str]:
    if isinstance(verifier, BasicVerifier):
        report = verifier.verify_report(
            result.content, json_schema=schema, tool_calls=_tool_calls(result), tools=tools
        )
        return [reason.render() for reason in report.reasons]
    return list(verifier.verify(result.content))


def run_verifiers(
    verifiers: Sequence[VerifierPlugin],
    result: ExecResult,
    *,
    tools: Sequence[Mapping[str, Any]] | None = None,
    response_format: Mapping[str, Any] | str | None = None,
) -> VerifyOutcome:
    schema = schema_of(response_format)
    problems: list[str] = []
    ran = 0
    for verifier in verifiers:
        try:
            found = _one(verifier, result, tools, schema)
        except Exception:  # a broken verifier must not fail the user's answer
            continue
        ran += 1
        problems.extend(p for p in found if p not in problems)
    if ran == 0:
        return VerifyOutcome(False, None)
    return VerifyOutcome(True, not problems, tuple(problems), ran)
````

## headwaters/core/rules.py

````python
"""User rules model (``rules.yaml``).

A pydantic model describes every setting; unknown keys are errors so typos are reported.
Loading, defaults-on-first-run and schema export live in ``rules_io``.

Model slugs, limits and prices are NOT defaults here: they live in the catalog.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Scope = Literal["auto", "free", "local", "cloud"]
Strategy = Literal["cost_saver", "quality_first", "primary_first"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecretsRules(_Model):
    action: Literal["local", "refuse", "off"] = "local"


class PickerRules(_Model):
    show_extras: bool = False


class BudgetRules(_Model):
    paid_daily_usd: float = Field(default=5.00, ge=0)
    premium_max_usd_per_call: float = Field(default=1.00, ge=0)
    confirm_above_usd: float = Field(default=0.25, ge=0)


class PaidTier(_Model):
    models: list[str] = Field(default_factory=list)
    use_floor_variant: bool = False


class EnabledFlag(_Model):
    enabled: bool = True


class FusionRules(_Model):
    enabled: bool = True
    preset: str = "quality"


class PremiumTier(_Model):
    fusion: FusionRules = Field(default_factory=FusionRules)


class FreeCloudTier(_Model):
    ranking: Literal["catalog"] = "catalog"
    opencode_free: list[str] = Field(default_factory=list)  # empty = auto-discovered


class FreeRouterRules(_Model):
    enabled: bool = False
    max_complexity: int = Field(default=2, ge=0)


class ClassifierRules(_Model):
    model: str = "auto_small"
    num_gpu: int = Field(default=0, ge=0)
    timeout_s: float = Field(default=2, gt=0)


class LocalTier(_Model):
    host: str = "http://localhost:11434"
    profile: Literal["auto", "laptop_12gb", "desktop_16gb", "custom"] = "auto"
    reserve_gb: float | None = Field(default=None, ge=0)  # None = auto-detect
    kv_cache_type: str = "q8_0"
    keep_alive: str = "30m"
    keep_warm_model: str = "auto"
    classifier: ClassifierRules = Field(default_factory=ClassifierRules)
    on_battery: Literal["prefer_cloud", "normal", "local_only"] = "prefer_cloud"


class TiersRules(_Model):
    paid: PaidTier = Field(default_factory=PaidTier)
    openrouter_auto: EnabledFlag = Field(default_factory=EnabledFlag)
    premium: PremiumTier = Field(default_factory=PremiumTier)
    free_cloud: FreeCloudTier = Field(default_factory=FreeCloudTier)
    openrouter_free_router: FreeRouterRules = Field(default_factory=FreeRouterRules)
    local: LocalTier = Field(default_factory=LocalTier)


class FloorRule(_Model):
    base: int = Field(ge=0)
    per_complexity: int = Field(ge=0)


class FloorsRules(_Model):
    code: FloorRule = FloorRule(base=70, per_complexity=5)
    reasoning: FloorRule = FloorRule(base=70, per_complexity=6)
    chat: FloorRule = FloorRule(base=50, per_complexity=3)
    summarize: FloorRule = FloorRule(base=50, per_complexity=3)
    extract: FloorRule = FloorRule(base=55, per_complexity=3)
    vision: FloorRule = FloorRule(base=60, per_complexity=4)
    creative: FloorRule = FloorRule(base=50, per_complexity=3)
    agentic_bonus: int = Field(default=15, ge=0)
    high_risk_bonus: int = Field(default=10, ge=0)
    optimistic_margin: int = Field(default=8, ge=0)


class StrategyWeights(_Model):
    """How one strategy adjusts the score terms (HDW-113). All values are first-draft."""

    cost_weight: float = Field(ge=0)  # score points per USD of estimated request cost
    latency_mult: float = Field(default=1.0, ge=0)
    warm_mult: float = Field(default=1.0, ge=0)
    evict_mult: float = Field(default=1.0, ge=0)
    primary_bonus: float = Field(default=0.0, ge=0)  # added to the user's paid primary models


class StrategiesRules(_Model):
    cost_saver: StrategyWeights = StrategyWeights(cost_weight=400)
    quality_first: StrategyWeights = StrategyWeights(
        cost_weight=40, latency_mult=0.5, warm_mult=0.5, evict_mult=0.5
    )
    primary_first: StrategyWeights = StrategyWeights(cost_weight=0, primary_bonus=30)


class ScoringRules(_Model):
    """Policy engine weights (HDW-113). Scores are on the same 0..100 scale as the floors."""

    ctx_headroom: float = Field(default=1.2, ge=1)  # context must be >= headroom x tokens
    complexity_penalty_per_level: float = Field(default=3.0, ge=0)
    assumed_output_tokens: int = Field(default=1000, ge=0)  # for the cost estimate
    residency_scale: float = Field(default=100.0, ge=0)  # 0..1 residency inputs -> points
    latency_ref_tps: float = Field(default=30.0, gt=0)  # at or above this speed: no penalty
    latency_weight: float = Field(default=5.0, ge=0)
    latency_max_penalty: float = Field(default=20.0, ge=0)
    on_battery_local_penalty: float = Field(default=20.0, ge=0)  # local.on_battery=prefer_cloud
    gpu_busy_local_penalty: float = Field(default=15.0, ge=0)
    soft_quota_penalty: float = Field(default=10.0, ge=0)  # model near its free-tier limit
    strategies: StrategiesRules = Field(default_factory=StrategiesRules)


class WallsRules(_Model):
    cascade_if_wait_over_s: float = Field(default=8, ge=0)
    soft_exhaust_pct: float = Field(default=90, ge=0, le=100)
    # Quota tracker (HDW-111): backoff for repeated 429s, short retries for overload, key polling.
    backoff_base_s: float = Field(default=2, gt=0)
    backoff_max_s: float = Field(default=300, gt=0)
    overloaded_max_retries: int = Field(default=2, ge=0)
    overloaded_backoff_s: float = Field(default=1, gt=0)
    overloaded_cooldown_s: float = Field(default=30, ge=0)
    key_poll_minutes: float = Field(default=10, gt=0)


class GatewayRules(_Model):
    enabled: bool = True
    port: int = Field(default=4000, ge=1, le=65535)


class DashboardRules(_Model):
    enabled: bool = True
    port: int = Field(default=4001, ge=1, le=65535)


class McpRules(_Model):
    transport: Literal["stdio"] = "stdio"
    async_threshold_s: float = Field(default=20, ge=0)
    roots: Literal["auto", "off"] = "auto"


class AttributionRules(_Model):
    referer: str | None = None
    title: str = "Headwaters"


class CatalogRules(_Model):
    auto_update: bool = True
    url: str | None = None
    # When true, fetches are also off while the Private lock or local default scope is set.
    disable_when_local: bool = False


SCORE_TASKS = (
    "code",
    "reasoning",
    "chat",
    "summarize",
    "extract",
    "creative",
    "vision",
    "agentic_tools",
)


class ModelOverride(_Model):
    """Per-model user override (applied last). Scores are 0..100, like the floors."""

    tier: Literal["local", "free_cloud", "paid"] | None = None
    scores: dict[str, int] = Field(default_factory=dict)
    tools_ok: bool | None = None
    json_ok: bool | None = None
    ctx: int | None = Field(default=None, gt=0)

    @field_validator("scores")
    @classmethod
    def _scores_valid(cls, value: dict[str, int]) -> dict[str, int]:
        for task, score in value.items():
            if task not in SCORE_TASKS:
                raise ValueError(f"unknown task {task!r}; use one of {', '.join(SCORE_TASKS)}")
            if not 0 <= score <= 100:
                raise ValueError(f"score for {task!r} must be between 0 and 100")
        return value


class OpenAICompatEndpoint(_Model):
    """A free OpenAI-compatible endpoint. The key is read from an env var, never stored here."""

    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    base_url: str = Field(min_length=1)
    api_key_env: str | None = None
    models: list[str] = Field(default_factory=list)  # empty = ask GET {base_url}/models


class ModelsRules(_Model):
    refresh_ttl_s: float = Field(default=3600, ge=60)
    opencode_config: str | None = None  # explicit opencode.json path; None = standard places
    overrides: dict[str, ModelOverride] = Field(default_factory=dict)
    openai_compatible: list[OpenAICompatEndpoint] = Field(default_factory=list)


class HostsRules(_Model):
    # Host id -> True when that host runs on a subscription (advice only; Headwaters never
    # proxies or reuses subscription credentials).
    subscription: dict[str, bool] = Field(default_factory=dict)


class Rules(_Model):
    """The whole ``rules.yaml`` document."""

    version: Literal[1] = 1
    default_scope: Scope = "auto"
    strategy: Strategy = "cost_saver"
    private_lock: bool = False
    airplane: bool = False
    secrets: SecretsRules = Field(default_factory=SecretsRules)
    verify: Literal["always", "agentic", "off"] = "agentic"
    picker: PickerRules = Field(default_factory=PickerRules)
    budget: BudgetRules = Field(default_factory=BudgetRules)
    tiers: TiersRules = Field(default_factory=TiersRules)
    floors: FloorsRules = Field(default_factory=FloorsRules)
    scoring: ScoringRules = Field(default_factory=ScoringRules)
    walls: WallsRules = Field(default_factory=WallsRules)
    gateway: GatewayRules = Field(default_factory=GatewayRules)
    dashboard: DashboardRules = Field(default_factory=DashboardRules)
    mcp: McpRules = Field(default_factory=McpRules)
    attribution: AttributionRules = Field(default_factory=AttributionRules)
    telemetry: Literal[False] = False
    catalog: CatalogRules = Field(default_factory=CatalogRules)
    models: ModelsRules = Field(default_factory=ModelsRules)
    features: dict[str, bool] = Field(default_factory=dict)
    hosts: HostsRules = Field(default_factory=HostsRules)
    advice: Literal["off", "inline", "header_only"] = "off"
````

## headwaters/core/rules_edit.py

````python
"""Targeted edits to ``rules.yaml`` that keep the user's comments and layout (HDW-118).

Only top-level scalar settings can be changed here (``default_scope``, ``private_lock``); the CLI
uses it for ``headwaters scope`` and ``headwaters private``. The edit replaces one line, is
validated against ``Rules`` before anything is written, and is swapped in atomically so the
hot-reload watcher never sees a half-written file. Line endings of the file are kept.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from headwaters.core import paths
from headwaters.core.rules import Rules
from headwaters.core.rules_io import (
    ConfigError,
    ensure_rules_file,
    format_validation_error,
    load_rules,
    rules_path,
)

EDITABLE_KEYS: frozenset[str] = frozenset({"default_scope", "private_lock"})
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _render(value: bool | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if not _SAFE_VALUE.match(value):
        raise ValueError(f"unsupported value: {value!r}")
    return value


def _replace_line(text: str, key: str, rendered: str, target: Path) -> str:
    """Replace the value on the ``key:`` line (keeping any trailing comment), or append it."""
    name = re.escape(key)
    line = re.compile(
        rf"^(?P<head>{name}[ \t]*:[ \t]*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s#]+)"
        rf"(?P<tail>[ \t]*(?:#.*)?)$",
        re.MULTILINE,
    )
    if line.search(text):
        return line.sub(lambda m: m.group("head") + rendered + m.group("tail"), text, count=1)
    if re.search(rf"^{name}[ \t]*:", text, re.MULTILINE):
        raise ConfigError(target, [f"cannot edit '{key}' automatically; change it in the file"])
    separator = "" if not text or text.endswith("\n") else "\n"
    return f"{text}{separator}{key}: {rendered}\n"


def _validate(text: str, key: str, value: bool | str, target: Path) -> None:
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(target, [f"the edited file is not valid YAML: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ConfigError(target, ["top level must be a mapping of settings"])
    try:
        Rules.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(target, format_validation_error(exc)) from exc
    if data.get(key) != value:
        raise ConfigError(target, [f"'{key}' is set more than once; change it in the file"])


def _write_atomic(target: Path, text: str) -> None:
    safe = paths.long_safe(target)
    fd, tmp = tempfile.mkstemp(dir=str(safe.parent), prefix=".rules-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(tmp, safe)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def set_setting(key: str, value: bool | str, path: Path | None = None) -> tuple[Any, Any]:
    """Set a top-level setting in ``rules.yaml`` (created with defaults on first use).

    Returns ``(previous, new)``. Raises ``ConfigError`` when the file is invalid or the result
    would be; in that case the file is left exactly as it was.
    """
    if key not in EDITABLE_KEYS:
        raise ValueError(f"{key!r} cannot be edited by the CLI")
    target = path or rules_path()
    ensure_rules_file(target)
    previous = getattr(load_rules(target), key)
    raw = paths.long_safe(target).read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    edited = _replace_line(text, key, _render(value), target)
    _validate(edited, key, value, target)
    if edited != text:
        _write_atomic(target, edited.replace("\n", "\r\n") if crlf else edited)
    return previous, value
````

## headwaters/core/scope.py

````python
"""Scope resolver: which tiers may serve a request.

    effective_scope = intersect(
        private_lock ? local : ANY,
        project_rule (.headwaters.yaml privacy: local | free | any),
        secret_detected ? local : ANY,       # secrets.action: local | refuse | off
        requested_scope (picker model / MCP param),
        default_scope (rules.yaml) when nothing was requested,
    )

Scopes are sets of tiers, so the intersection is exact and a lock can only ever remove tiers
(project rules and the Private lock tighten, never loosen). An empty or unsatisfiable result is a
structured ``ScopeError`` with a fix hint, never a silent fallback to a looser scope.

Without the paid-scopes ★ feature, ``auto`` and ``cloud`` exclude the paid tiers and the reasons
say so. The resolver handles scope only: ranking inside a scope is the policy layer's job.

Reasons never contain prompt text or matched secrets, only pattern names.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast, get_args

from headwaters.core.features import FeatureRegistry
from headwaters.core.project_config import Privacy, ProjectConfig
from headwaters.core.rules import Rules, Scope, Strategy
from headwaters.core.secrets_detect import SecretMatch

TierName = Literal["paid", "openrouter_auto", "free_cloud", "local"]
VerifyMode = Literal["always", "agentic", "off"]

PAID_SCOPES_FEATURE_ID = "paid_scopes"
ALIAS_PREFIX = "headwaters/"

ALL_TIERS: frozenset[TierName] = frozenset({"paid", "openrouter_auto", "free_cloud", "local"})
PAID_TIERS: frozenset[TierName] = frozenset({"paid", "openrouter_auto"})
LOCAL_TIERS: frozenset[TierName] = frozenset({"local"})
FREE_TIERS: frozenset[TierName] = frozenset({"free_cloud", "local"})

SCOPE_TIERS: dict[Scope, frozenset[TierName]] = {
    "auto": ALL_TIERS,
    "free": FREE_TIERS,
    "local": LOCAL_TIERS,
    "cloud": frozenset({"paid", "openrouter_auto", "free_cloud"}),
}
PRIVACY_TIERS: dict[Privacy, frozenset[TierName]] = {
    "any": ALL_TIERS,
    "free": FREE_TIERS,
    "local": LOCAL_TIERS,
}

# Picker model suffixes (``headwaters/free+verify``, ``headwaters/auto+quality``).
SUFFIX_STRATEGY: dict[str, Strategy] = {"quality": "quality_first", "primary": "primary_first"}
SUFFIX_VERIFY: dict[str, VerifyMode] = {"verify": "always", "noverify": "off"}
# ``headwaters/quality`` is the optional picker entry: auto scope + quality_first.
_EXTRA_ALIASES: dict[str, tuple[Scope, Strategy]] = {"quality": ("auto", "quality_first")}


class ScopeError(Exception):
    """The request cannot be served within the effective scope (or is not understood)."""

    def __init__(self, code: str, message: str, fix_hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix_hint = fix_hint

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix_hint": self.fix_hint}


@dataclass(frozen=True)
class ParsedModel:
    """A parsed ``headwaters/<name>[+suffix...]`` picker model id."""

    scope: Scope
    strategy: Strategy | None = None
    verify: VerifyMode | None = None
    suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeDecision:
    scope: Scope  # nearest named scope; ``tiers`` is authoritative
    tiers: frozenset[TierName]
    strategy: Strategy
    verify: VerifyMode
    locks: tuple[str, ...] = ()  # locks that narrowed the request
    reasons: tuple[str, ...] = ()
    secret_patterns: tuple[str, ...] = ()  # pattern names only, never matched text

    @property
    def local_only(self) -> bool:
        return self.tiers == LOCAL_TIERS

    @property
    def allows_cloud(self) -> bool:
        return bool(self.tiers - LOCAL_TIERS)


def paid_scopes_available(registry: FeatureRegistry) -> bool:
    """True when the paid-scopes ★ feature is registered and switched on."""
    return registry.enabled(PAID_SCOPES_FEATURE_ID)


def parse_model_name(model: str) -> ParsedModel | None:
    """Parse a picker model id. Returns ``None`` for ids that are not ``headwaters/...``.

    ``headwaters/free+verify`` -> free scope, verify always; ``headwaters/auto+quality`` ->
    auto scope, quality_first. Unknown names or suffixes raise ``ScopeError``.
    """
    text = model.strip()
    if not text.lower().startswith(ALIAS_PREFIX):
        return None
    name, *suffixes = text[len(ALIAS_PREFIX) :].lower().split("+")
    strategy: Strategy | None = None
    if name in get_args(Scope):
        scope = cast(Scope, name)
    elif name in _EXTRA_ALIASES:
        scope, strategy = _EXTRA_ALIASES[name]
    else:
        known = ", ".join(ALIAS_PREFIX + n for n in sorted([*SCOPE_TIERS, *_EXTRA_ALIASES]))
        raise ScopeError(
            "unknown_model", f"Unknown Headwaters model {model!r}.", f"Use one of: {known}."
        )
    verify: VerifyMode | None = None
    for suffix in suffixes:
        if suffix in SUFFIX_STRATEGY:
            strategy = SUFFIX_STRATEGY[suffix]
        elif suffix in SUFFIX_VERIFY:
            verify = SUFFIX_VERIFY[suffix]
        else:
            allowed = ", ".join(f"+{s}" for s in (*SUFFIX_STRATEGY, *SUFFIX_VERIFY))
            raise ScopeError(
                "unknown_suffix",
                f"Unknown suffix '+{suffix}' in {model!r}.",
                f"Allowed suffixes: {allowed}.",
            )
    return ParsedModel(scope, strategy, verify, tuple(suffixes))


def _name_for(tiers: frozenset[TierName]) -> Scope:
    """The named scope that best describes ``tiers`` (``tiers`` itself stays authoritative)."""
    if "local" not in tiers:
        return "cloud"
    if tiers == LOCAL_TIERS:
        return "local"
    return "free" if tiers <= FREE_TIERS else "auto"


def _narrow(
    tiers: frozenset[TierName],
    limit: frozenset[TierName],
    lock: str,
    narrowed: list[str],
    error: ScopeError,
) -> frozenset[TierName]:
    result = tiers & limit
    if not result:
        raise error
    if result != tiers:
        narrowed.append(lock)
    return result


def resolve_scope(
    rules: Rules,
    project: ProjectConfig | None = None,
    *,
    requested: Scope | None = None,
    strategy: Strategy | None = None,
    verify: VerifyMode | None = None,
    secret_matches: Sequence[SecretMatch] = (),
    secret_override: bool = False,
    paid_scopes: bool = False,
    local_available: bool = True,
    local_fix_hint: str | None = None,
) -> ScopeDecision:
    """Resolve the effective scope for one request.

    ``requested`` comes from the picker model or an MCP ``scope`` parameter; when ``None`` the
    ``rules.default_scope`` applies. ``secret_override`` must only be set by an explicit user
    action for this request; it lifts the secret lock but never the Private lock or a project
    rule. ``paid_scopes`` says whether the ★ paid-scopes feature is on
    (see ``paid_scopes_available``). ``local_available`` / ``local_fix_hint`` come from the
    hardware and discovery layers and only matter when the result is local-only.
    """
    base: Scope = requested if requested is not None else rules.default_scope
    reasons = [f"{'requested' if requested is not None else 'default'} scope: {base}"]
    narrowed: list[str] = []
    tiers = SCOPE_TIERS[base]

    # Locks, strongest first. Each can only remove tiers.
    if rules.private_lock:
        tiers = _narrow(
            tiers,
            LOCAL_TIERS,
            "private_lock",
            narrowed,
            ScopeError(
                "private_lock_conflict",
                f"The Private lock allows local models only, but scope '{base}' was requested.",
                "Disable the Private lock in the tray (or run `headwaters private off`), "
                "or request the local scope.",
            ),
        )
        reasons.append("Private lock on: local models only")

    privacy: Privacy = project.privacy if project is not None else "any"
    if privacy != "any":
        tiers = _narrow(
            tiers,
            PRIVACY_TIERS[privacy],
            "project_rule",
            narrowed,
            ScopeError(
                "project_rule_conflict",
                f"This project's privacy rule ({privacy}) excludes every tier "
                f"that scope '{base}' allows.",
                "Request a scope the project allows, or edit `privacy:` in .headwaters.yaml.",
            ),
        )
        reasons.append(f"project rule privacy={privacy}")

    names = tuple(sorted({m.pattern for m in secret_matches}))
    if secret_matches and rules.secrets.action != "off":
        shown = ", ".join(names)
        if secret_override:
            reasons.append(f"secret detected ({shown}): override confirmed for this request")
        elif rules.secrets.action == "refuse":
            raise ScopeError(
                "secret_refused",
                f"A secret was detected in the request ({shown}) and secrets.action is 'refuse'.",
                "Remove the secret from the prompt, or set secrets.action to 'local' "
                "in rules.yaml.",
            )
        else:
            tiers = _narrow(
                tiers,
                LOCAL_TIERS,
                "secret",
                narrowed,
                ScopeError(
                    "secret_conflict",
                    f"A secret was detected ({shown}), so the request must stay local, "
                    f"but scope '{base}' excludes local models.",
                    "Remove the secret from the prompt, or confirm the override for this request.",
                ),
            )
            reasons.append(f"secret detected ({shown}): local only")

    if not paid_scopes and tiers & PAID_TIERS:
        tiers = tiers - PAID_TIERS
        reasons.append("paid tiers excluded: the paid-scopes feature is not enabled")

    if tiers == LOCAL_TIERS and not local_available:
        raise ScopeError(
            "local_unavailable",
            "Only local models are allowed here, but none is available.",
            local_fix_hint
            or "Start Ollama and pull a model (run `headwaters doctor` for a recommendation).",
        )

    return ScopeDecision(
        scope=_name_for(tiers),
        tiers=tiers,
        strategy=strategy if strategy is not None else rules.strategy,
        verify=verify if verify is not None else rules.verify,
        locks=tuple(narrowed),
        reasons=tuple(reasons),
        secret_patterns=names,
    )
````

## headwaters/core/secrets_detect.py

````python
"""Secret detection for routing (API keys, tokens, private keys).

Detection patterns and placeholder patterns are catalog data (``secret_patterns``,
``secret_placeholders`` and ``placeholder_patterns``), never code. A match whose text also matches
a placeholder pattern (``sk-xxxx``, ``YOUR_API_KEY``) is ignored.

Results carry the pattern name and the character span only. The matched text is never stored,
returned or logged, so a ``SecretMatch`` is safe to put in decision metadata.

The action to take on a hit (``local`` | ``refuse`` | ``off``) is applied by ``core.scope``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from headwaters.core.catalog.schema import Catalog, NamedPattern


@dataclass(frozen=True)
class SecretMatch:
    """Where a secret-shaped string was found. Deliberately holds no text."""

    pattern: str
    start: int
    end: int


class SecretDetector:
    """Compiled catalog patterns. Build once per catalog, reuse for every request."""

    def __init__(
        self,
        secrets: Sequence[NamedPattern],
        placeholders: Sequence[NamedPattern] = (),
    ) -> None:
        self._secrets: list[tuple[str, re.Pattern[str]]] = [
            (p.name, re.compile(p.regex)) for p in secrets
        ]
        self._placeholders: list[re.Pattern[str]] = [re.compile(p.regex) for p in placeholders]

    @classmethod
    def from_catalog(cls, catalog: Catalog) -> SecretDetector:
        return cls(
            catalog.secret_patterns,
            [*catalog.secret_placeholders, *catalog.placeholder_patterns],
        )

    def _is_placeholder(self, matched: str) -> bool:
        return any(p.search(matched) for p in self._placeholders)

    def scan(self, text: str) -> list[SecretMatch]:
        """Every non-placeholder secret-shaped match, ordered by position."""
        found: list[SecretMatch] = []
        for name, pattern in self._secrets:
            for match in pattern.finditer(text):
                if match.end() == match.start():
                    continue
                if self._is_placeholder(match.group(0)):
                    continue
                found.append(SecretMatch(name, match.start(), match.end()))
        found.sort(key=lambda m: (m.start, m.end, m.pattern))
        return found

    def contains_secret(self, text: str) -> bool:
        return bool(self.scan(text))

    def scan_all(self, texts: Iterable[str]) -> list[SecretMatch]:
        """Scan several texts (e.g. every message). Spans are relative to their own text."""
        found: list[SecretMatch] = []
        for text in texts:
            found.extend(self.scan(text))
        return found
````

## headwaters/core/state.py

````python
"""Shared SQLite state (gateway, MCP daemon and CLI all open the same database).

WAL journal, ``busy_timeout``, one short-lived connection per operation, and writes inside
``BEGIN IMMEDIATE`` so concurrent processes queue instead of failing. Migrations are
versioned and idempotent. Typed repository functions live in ``state_models``,
``state_decisions``, ``state_limits`` and ``state_misc``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from headwaters.core import paths
from headwaters.core.state_schema import LATEST_VERSION, MIGRATIONS

DB_FILE_NAME = "headwaters.db"
BUSY_TIMEOUT_MS = 30_000

__all__ = ["LATEST_VERSION", "Database", "default_db_path", "dumps", "open_database", "scrub"]

# JSON keys that could carry prompt/output text: stripped before anything is stored.
_FORBIDDEN_KEYS = frozenset({"prompt", "content", "messages", "output", "text"})


def scrub(value: Any) -> Any:
    """Drop keys that could carry prompt/output text, recursively."""
    if isinstance(value, Mapping):
        return {str(k): scrub(v) for k, v in value.items() if str(k).lower() not in _FORBIDDEN_KEYS}
    if isinstance(value, list | tuple | set | frozenset):
        return [scrub(v) for v in value]
    return value


def dumps(value: Any) -> str:
    return json.dumps(scrub(value), ensure_ascii=False, sort_keys=True, default=str)


def default_db_path() -> Path:
    return paths.state_dir() / DB_FILE_NAME


def _split_statements(ddl: str) -> list[str]:
    # The DDL contains no semicolons inside literals, so a plain split is sufficient.
    return [s.strip() for s in ddl.split(";") if s.strip()]


class Database:
    """Handle to the shared database. Cheap to create; opens a connection per operation."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()

    def _open(self) -> sqlite3.Connection:
        safe = paths.long_safe(self.path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(safe), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._open()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One short write transaction; takes the write lock up front (waits for it)."""
        conn = self._open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def migrate(self) -> list[int]:
        """Apply pending migrations; safe to call repeatedly and from many processes.

        Returns the versions applied by this call.
        """
        applied: list[int] = []
        with self.write() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at REAL NOT NULL)"
            )
            # Checked inside the write lock, so a racing process cannot apply a version twice.
            done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
            for version, description, ddl in MIGRATIONS:
                if version in done:
                    continue
                for statement in _split_statements(ddl):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, description, applied_at)"
                    " VALUES (?, ?, ?)",
                    (version, description, time.time()),
                )
                applied.append(version)
        return applied

    def schema_version(self) -> int:
        with self.read() as conn:
            try:
                row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row["v"] or 0)


def open_database(path: Path | None = None) -> Database:
    """Create the handle and make sure the schema is current."""
    db = Database(path)
    db.migrate()
    return db
````

## headwaters/core/state_decisions.py

````python
"""Repositories for ``decisions`` and ``outcomes`` (metadata and token counts only)."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from headwaters.core.state import Database, dumps


@dataclass(frozen=True)
class DecisionRow:
    surface: str  # gateway | mcp | hook | cli
    scope: str
    strategy: str
    locks_applied: Sequence[str] = ()
    session: str | None = None
    requirements: dict[str, Any] = field(default_factory=dict)
    chosen: str | None = None
    alternatives: Sequence[Any] = ()
    floor: float | None = None
    score: float | None = None
    below_floor: bool = False
    reasons: Sequence[str] = ()
    id: int | None = None
    ts: float | None = None


def insert_decision(db: Database, d: DecisionRow) -> int:
    with db.write() as c:
        cur = c.execute(
            "INSERT INTO decisions (ts, surface, session, scope, strategy, locks_applied,"
            " requirements_json, chosen, alternatives, floor, score, below_floor, reasons)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d.ts or time.time(),
                d.surface,
                d.session,
                d.scope,
                d.strategy,
                dumps(list(d.locks_applied)),
                dumps(d.requirements),
                d.chosen,
                dumps(list(d.alternatives)),
                d.floor,
                d.score,
                int(d.below_floor),
                dumps(list(d.reasons)),
            ),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid


def _decision(r: sqlite3.Row) -> DecisionRow:
    return DecisionRow(
        id=r["id"],
        ts=r["ts"],
        surface=r["surface"],
        session=r["session"],
        scope=r["scope"],
        strategy=r["strategy"],
        locks_applied=json.loads(r["locks_applied"]),
        requirements=json.loads(r["requirements_json"]),
        chosen=r["chosen"],
        alternatives=json.loads(r["alternatives"]),
        floor=r["floor"],
        score=r["score"],
        below_floor=bool(r["below_floor"]),
        reasons=json.loads(r["reasons"]),
    )


def get_decision(db: Database, decision_id: int) -> DecisionRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return _decision(r) if r else None


def recent_decisions(db: Database, limit: int = 50) -> list[DecisionRow]:
    with db.read() as c:
        rows = c.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
        return [_decision(r) for r in rows]


@dataclass(frozen=True)
class OutcomeRow:
    decision_id: int
    status: str
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    counterfactual_usd: float | None = None  # nullable: unknown unless computed
    verify_result: str | None = None
    escalated_to: str | None = None
    user_feedback: str | None = None


_OUTCOME_COLUMNS = (
    "decision_id",
    "status",
    "latency_ms",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "counterfactual_usd",
    "verify_result",
    "escalated_to",
    "user_feedback",
)


def save_outcome(db: Database, o: OutcomeRow) -> None:
    values = tuple(getattr(o, col) for col in _OUTCOME_COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in _OUTCOME_COLUMNS[1:])
    with db.write() as c:
        c.execute(
            f"INSERT INTO outcomes ({', '.join(_OUTCOME_COLUMNS)})"
            f" VALUES ({', '.join('?' * len(_OUTCOME_COLUMNS))})"
            f" ON CONFLICT(decision_id) DO UPDATE SET {updates}",
            values,
        )


def get_outcome(db: Database, decision_id: int) -> OutcomeRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM outcomes WHERE decision_id = ?", (decision_id,)).fetchone()
    return OutcomeRow(**dict(r)) if r else None


def set_user_feedback(db: Database, decision_id: int, feedback: str) -> bool:
    with db.write() as c:
        return (
            c.execute(
                "UPDATE outcomes SET user_feedback = ? WHERE decision_id = ?",
                (feedback, decision_id),
            ).rowcount
            > 0
        )
````
