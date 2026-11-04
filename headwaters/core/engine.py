"""The production wiring of the core: rules + catalog + state -> a ready ``Router`` (HDW-201).

The daemon builds one ``Engine`` and swaps in a new one when the catalog changes. Everything a
real call needs is assembled here: analyzer chain, candidate builder, executor (with the
catalog's cloud-proxied-model patterns, D13 / FU-121-2), quota tracker and secret detector.

Local ``num_ctx`` comes from the fit computed for *this* request. The executor's resolver only
sees the decision, so the candidate source records the request's candidates in a ``ContextVar``
(one request is one asyncio task, so one context).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass

from headwaters.core.analyzer.layers import build_chain
from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.candidates import CandidateBuilder
from headwaters.core.catalog.schema import Catalog, GpuProfile
from headwaters.core.executor.cloud import LiteLLMBackend
from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import Backend, ExecTarget
from headwaters.core.features import FeatureRegistry, discover_features
from headwaters.core.hardware.state import HardwareMonitor, HardwareState
from headwaters.core.keys import KeyStore
from headwaters.core.ollama_remote import RemotePatterns, remote_patterns_from
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.types import RouteDecision
from headwaters.core.providers import Registry
from headwaters.core.quotas import QuotaTracker
from headwaters.core.router import CandidateSource, RequirementsAnalyzer, Router
from headwaters.core.rules import OpenAICompatEndpoint, Rules, RulesProvider, as_provider
from headwaters.core.secrets_detect import SecretDetector
from headwaters.core.state import Database
from headwaters.core.walls import WallClassifier

__all__ = ["Engine", "TargetBuilder", "build_engine"]

OLLAMA = "ollama"
OPENROUTER = "openrouter"
OPENCODE = "opencode"
_OPENAI_ROUTE = "openai"
ANONYMOUS_KEY = "headwaters-no-key"

_REQUEST_CANDIDATES: ContextVar[Mapping[str, Candidate] | None] = ContextVar(
    "headwaters_request_candidates", default=None
)


class TargetBuilder:
    """``TargetResolver`` (decision -> what to call) and the key lookup for cloud calls."""

    def __init__(
        self,
        rules: Rules,
        env: Mapping[str, str] | None = None,
        *,
        key_store: KeyStore | None = None,
        registry: Registry | None = None,
    ) -> None:
        self._env = os.environ if env is None else env
        self._keys = key_store
        self._registry = registry if registry is not None else Registry()
        self._endpoints: dict[str, OpenAICompatEndpoint] = {
            e.name: e for e in rules.models.openai_compatible
        }

    def __call__(self, decision: RouteDecision) -> ExecTarget:
        found = (_REQUEST_CANDIDATES.get() or {}).get(decision.model)
        if decision.tier == "local":
            fit = found.fit if found is not None else None
            return ExecTarget(
                model_id=decision.model,
                provider=OLLAMA,
                route=decision.model,
                num_ctx=fit.num_ctx if fit is not None and fit.usable else None,
                cold=None if found is None else not found.row.warm,
            )
        fallback = decision.model.split("/", 1)[0]
        provider = (found.row.provider if found is not None else "") or fallback
        return self._cloud_target(decision.model, provider)

    def _cloud_target(self, model: str, provider: str) -> ExecTarget:
        prefix = f"{provider}/"
        endpoint = self._endpoints.get(provider)
        if endpoint is not None:
            name = model.removeprefix(prefix)
            base = endpoint.base_url.rstrip("/")
            return ExecTarget(model, provider, f"{_OPENAI_ROUTE}/{name}", api_base=base)
        entry = self._registry.get(provider)
        if entry is not None:
            name = model.removeprefix(prefix)
            if entry.refuses(name):
                # The provider's own listing excludes this id for outside clients (OpenCode's
                # free-tier models are app-only): refuse instead of calling for a 429.
                raise ExecutorError(
                    "provider_unsupported",
                    f"{entry.title} does not serve {name!r} outside its own app.",
                    provider=provider,
                    model=model,
                )
            base = entry.base_url.rstrip("/")
            return ExecTarget(
                model,
                provider,
                f"{_OPENAI_ROUTE}/{name}",
                api_base=base,
                session_header=entry.session_header,
            )
        if provider == OPENCODE:
            # Free-tier models are served only to OpenCode itself (429 elsewhere); defence in
            # depth, since discovery no longer offers them (A-OC-3, HDW-150).
            raise ExecutorError(
                "provider_unsupported",
                "OpenCode free models can only be used from inside OpenCode.",
                provider=provider,
                model=model,
            )
        route = model if model.startswith(prefix) else prefix + model
        return ExecTarget(model, provider, route)

    def api_key_for(self, provider: str) -> str | None:
        """The key a call to ``provider`` uses.

        A ``KeyStore`` answers first (credential store, then the environment variable), so a key
        saved with ``headwaters keys set`` reaches the call. Without a store this stays the
        endpoint-only env lookup it always was.

        A registered provider never gets ``None``: litellm's ``openai/`` route would then send
        the user's ``OPENAI_API_KEY`` to another company's server, so a missing key becomes a
        fixed placeholder (an anonymous call, or a clean 401 from the provider).
        """
        key = self._stored_key(provider)
        if key is None and provider in self._registry:
            return ANONYMOUS_KEY
        return key

    def _stored_key(self, provider: str) -> str | None:
        if self._keys is not None:
            return self._keys.api_key_for(provider)
        endpoint = self._endpoints.get(provider)
        if endpoint is not None and endpoint.api_key_env:
            return self._env.get(endpoint.api_key_env) or None
        entry = self._registry.get(provider)
        if entry is not None:
            return self._env.get(entry.env_var) or None
        return None


class _RememberingSource:
    """Wraps the candidate source so ``TargetBuilder`` can see this request's fits."""

    def __init__(self, inner: CandidateSource) -> None:
        self._inner = inner

    def __call__(self, reqs: Requirements) -> list[Candidate]:
        found = list(self._inner(reqs))
        _REQUEST_CANDIDATES.set({c.id: c for c in found})
        return found


@dataclass(frozen=True)
class Engine:
    """One built router and what was built with it. It holds no copy of the rules: the parts
    that read them per request take the provider, and the parts that read them once at build
    time are rebuilt by the daemon when the rules change (HDW-148)."""

    router: Router
    catalog: Catalog
    catalog_source: str
    remote_patterns: RemotePatterns
    targets: TargetBuilder
    registry: FeatureRegistry
    hardware: Callable[[], HardwareState | None]


def hardware_reader(
    rules: Rules, profiles: Mapping[str, GpuProfile] | None = None
) -> Callable[[], HardwareState | None]:
    monitor = HardwareMonitor(rules.tiers.local, profiles=profiles)

    def read() -> HardwareState | None:
        try:
            return monitor.get_state()
        except Exception:  # a broken GPU probe must never stop routing
            return None

    return read


def build_engine(
    rules: Rules | RulesProvider,
    catalog: Catalog,
    catalog_source: str,
    db: Database,
    *,
    hardware: Callable[[], HardwareState | None] | None = None,
    analyzer: RequirementsAnalyzer | None = None,
    cloud_factory: Callable[[], Backend] = LiteLLMBackend,
    local_factory: Callable[[str], Backend] | None = None,
    env: Mapping[str, str] | None = None,
    key_store: KeyStore | None = None,
    enable_classifier: bool = True,
    snapshot: Rules | None = None,
) -> Engine:
    """Assemble a router for ``rules`` and ``catalog``. Nothing is called or started here.

    The router, executor and quota tracker read ``rules`` per request. The candidate builder,
    hardware monitor, feature flags, ``TargetBuilder`` and analyzer chain take one reading at
    build time: ``snapshot`` when the caller has it (the daemon, so its stale-engine check and
    the build agree), else one call of the provider. The daemon builds a new engine whenever
    the rules change (HDW-148); a request already running finishes on the engine it started on.

    Keys come from ``key_store`` (the daemon's credential store); without one a store over
    ``rules`` and ``env`` is built here, so the executor and the router's key-scoped walls agree.
    """
    provider = as_provider(rules)
    rules = snapshot if snapshot is not None else provider()
    patterns = remote_patterns_from(catalog)
    keys = (
        key_store
        if key_store is not None
        else KeyStore(rules, env=env, registry=Registry.from_catalog(catalog))
    )
    targets = TargetBuilder(rules, env, key_store=keys, registry=Registry.from_catalog(catalog))
    registry = FeatureRegistry()
    discover_features(registry)
    registry.set_flags(rules.features)
    read_hardware = hardware or hardware_reader(rules, catalog.gpu_profiles)
    executor = Executor(
        provider,
        WallClassifier(catalog.wall_patterns),
        targets,
        cloud_factory=cloud_factory,
        local_factory=local_factory,
        api_key_for=targets.api_key_for,
        key_alias_for=keys.key_alias_for,
        remote_patterns=patterns,
    )
    chain = analyzer or build_chain(catalog, rules.tiers.local, enable_layer3=enable_classifier)
    router = Router(
        provider,
        db,
        chain,
        _RememberingSource(CandidateBuilder(db, rules, catalog, read_hardware)),
        executor,
        QuotaTracker(db, provider, catalog.free_limits),
        secrets=SecretDetector.from_catalog(catalog),
        registry=registry,
        hardware=read_hardware,
        gpu_profiles=catalog.gpu_profiles,
    )
    return Engine(router, catalog, catalog_source, patterns, targets, registry, read_hardware)
