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
from headwaters.core.catalog.schema import Catalog
from headwaters.core.executor.cloud import LiteLLMBackend
from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import Backend, ExecTarget
from headwaters.core.features import FeatureRegistry, discover_features
from headwaters.core.hardware.state import HardwareMonitor, HardwareState
from headwaters.core.ollama_remote import RemotePatterns, remote_patterns_from
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.types import RouteDecision
from headwaters.core.quotas import QuotaTracker
from headwaters.core.router import CandidateSource, RequirementsAnalyzer, Router
from headwaters.core.rules import OpenAICompatEndpoint, Rules
from headwaters.core.secrets_detect import SecretDetector
from headwaters.core.state import Database
from headwaters.core.walls import WallClassifier

__all__ = ["Engine", "TargetBuilder", "build_engine"]

OLLAMA = "ollama"
OPENROUTER = "openrouter"
OPENCODE = "opencode"
_OPENAI_ROUTE = "openai"

_REQUEST_CANDIDATES: ContextVar[Mapping[str, Candidate] | None] = ContextVar(
    "headwaters_request_candidates", default=None
)


class TargetBuilder:
    """``TargetResolver`` (decision -> what to call) and the key lookup for cloud calls."""

    def __init__(self, rules: Rules, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env
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
        if provider == OPENCODE:
            # No verified base URL for OpenCode's free gateway yet (ASSUMPTIONS A-OC-3).
            raise ExecutorError(
                "provider_unsupported",
                "OpenCode free models cannot be called by the daemon yet.",
                provider=provider,
                model=model,
            )
        route = model if model.startswith(prefix) else prefix + model
        return ExecTarget(model, provider, route)

    def api_key_for(self, provider: str) -> str | None:
        """Key of a user-configured endpoint, from its env var. ``None`` leaves the lookup to
        litellm, which reads the provider's standard variable. The OS credential store is
        HDW-401."""
        endpoint = self._endpoints.get(provider)
        if endpoint is None or not endpoint.api_key_env:
            return None
        return self._env.get(endpoint.api_key_env) or None


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
    router: Router
    rules: Rules
    catalog: Catalog
    catalog_source: str
    remote_patterns: RemotePatterns
    targets: TargetBuilder
    registry: FeatureRegistry
    hardware: Callable[[], HardwareState | None]


def hardware_reader(rules: Rules) -> Callable[[], HardwareState | None]:
    monitor = HardwareMonitor(rules.tiers.local)

    def read() -> HardwareState | None:
        try:
            return monitor.get_state()
        except Exception:  # a broken GPU probe must never stop routing
            return None

    return read


def build_engine(
    rules: Rules,
    catalog: Catalog,
    catalog_source: str,
    db: Database,
    *,
    hardware: Callable[[], HardwareState | None] | None = None,
    analyzer: RequirementsAnalyzer | None = None,
    cloud_factory: Callable[[], Backend] = LiteLLMBackend,
    local_factory: Callable[[str], Backend] | None = None,
    env: Mapping[str, str] | None = None,
    enable_classifier: bool = True,
) -> Engine:
    """Assemble a router for ``rules`` and ``catalog``. Nothing is called or started here."""
    patterns = remote_patterns_from(catalog)
    targets = TargetBuilder(rules, env)
    registry = FeatureRegistry()
    discover_features(registry)
    registry.set_flags(rules.features)
    read_hardware = hardware or hardware_reader(rules)
    executor = Executor(
        rules,
        WallClassifier(catalog.wall_patterns),
        targets,
        cloud_factory=cloud_factory,
        local_factory=local_factory,
        api_key_for=targets.api_key_for,
        remote_patterns=patterns,
    )
    chain = analyzer or build_chain(catalog, rules.tiers.local, enable_layer3=enable_classifier)
    router = Router(
        rules,
        db,
        chain,
        _RememberingSource(CandidateBuilder(db, rules, catalog, read_hardware)),
        executor,
        QuotaTracker(db, rules.walls, catalog.free_limits),
        secrets=SecretDetector.from_catalog(catalog),
        registry=registry,
        hardware=read_hardware,
    )
    return Engine(
        router, rules, catalog, catalog_source, patterns, targets, registry, read_hardware
    )
