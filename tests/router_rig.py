"""The router wired to fakes, with handles on everything a test asserts on (HDW-117)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.catalog.schema import FreeLimit, GpuProfile
from headwaters.core.executor.executor import Executor
from headwaters.core.features import FeatureRegistry
from headwaters.core.policy.filters import Candidate
from headwaters.core.quotas import QuotaTracker
from headwaters.core.router import Router, RouterConfig
from headwaters.core.router_types import RouteEvent, RouteRequest, RouteResult
from headwaters.core.rules import Rules
from headwaters.core.secrets_detect import SecretDetector
from headwaters.core.state import Database, open_database
from tests.executor_support import WALLS, Step, target_for
from tests.router_support import MESSAGES, Dispatch, FixedAnalyzer, registry_with


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        scripts: Mapping[str, Sequence[Step]],
        candidates: Sequence[Candidate],
        *,
        rules: Rules | None = None,
        registry: FeatureRegistry | None = None,
        verifiers: Sequence[Any] = (),
        config: RouterConfig | None = None,
        secrets: SecretDetector | None = None,
        reqs: Requirements | None = None,
        key_alias_for: Callable[[str], str | None] | None = None,
        api_key_for: Callable[[str], str | None] | None = None,
        free_limits: Sequence[FreeLimit] = (),
        clock: Callable[[], float] | None = None,
        rules_source: Callable[[], Rules] | None = None,
        gpu_profiles: Mapping[str, GpuProfile] | None = None,
        analyzer: Any | None = None,
    ) -> None:
        self.rules = rules or Rules()
        live: Rules | Callable[[], Rules] = rules_source or self.rules  # HDW-131
        self.db: Database = open_database(tmp_path / "state.db")
        self.dispatch = Dispatch(scripts)
        self.cloud_clients = 0
        self.waits: list[float] = []
        self.analyzer = analyzer if analyzer is not None else FixedAnalyzer(reqs)
        pool = list(candidates)

        def cloud_factory() -> Dispatch:
            self.cloud_clients += 1
            return self.dispatch

        executor = Executor(
            live,
            WALLS,
            lambda d: target_for(d, cold=False),
            cloud_factory=cloud_factory,
            local_factory=lambda host: self.dispatch,
            key_alias_for=key_alias_for,
            api_key_for=api_key_for,
        )

        async def sleep(seconds: float) -> None:
            self.waits.append(seconds)

        self.quotas = QuotaTracker(
            self.db, rules_source or self.rules.walls, free_limits, clock=clock
        )
        self.router = Router(
            live,
            self.db,
            self.analyzer,
            lambda _reqs: pool,
            executor,
            self.quotas,
            secrets=secrets,
            verifiers=verifiers,
            registry=registry if registry is not None else registry_with(),
            config=config,
            sleep=sleep,
            gpu_profiles=gpu_profiles,
        )

    def run(self, **kw: Any) -> RouteResult:
        kw.setdefault("messages", MESSAGES)
        return asyncio.run(self.router.run(RouteRequest(**kw)))

    def stream(self, **kw: Any) -> list[RouteEvent]:
        kw.setdefault("messages", MESSAGES)

        async def go() -> list[RouteEvent]:
            return [e async for e in self.router.stream(RouteRequest(stream=True, **kw))]

        return asyncio.run(go())
