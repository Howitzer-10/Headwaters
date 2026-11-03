"""Wiring for CLI commands: rules, catalog, state, project rule, features, hardware, router.

Everything is created per command and nothing here starts a server or calls a model.
``hardware_state`` is the one place that probes the GPU, so tests replace it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import typer

from headwaters.cli.output import EXIT_CONFIG, fail
from headwaters.core.analyzer.requirements import Analyzer, AnalyzerInput, Requirements
from headwaters.core.candidates import CandidateBuilder
from headwaters.core.catalog import updater as catalog_updater
from headwaters.core.catalog.schema import Catalog
from headwaters.core.engine import TargetBuilder
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import ExecTarget
from headwaters.core.features import FeatureRegistry, discover_features
from headwaters.core.hardware.state import HardwareMonitor, HardwareState
from headwaters.core.keys import KeyStore
from headwaters.core.ollama_remote import remote_patterns_from
from headwaters.core.policy.types import RouteDecision
from headwaters.core.project_config import ProjectConfig, load_project_config
from headwaters.core.quotas import QuotaTracker
from headwaters.core.router import Router
from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError, load_rules, rules_path
from headwaters.core.secrets_detect import SecretDetector
from headwaters.core.state import Database, open_database
from headwaters.core.walls import WallClassifier

__all__ = [
    "Runtime",
    "build_executor",
    "build_router",
    "hardware_state",
    "load_rules_or_exit",
    "load_runtime",
]


def load_rules_or_exit() -> Rules:
    """``rules.yaml`` (defaults when absent). An invalid file is reported and exits with 2."""
    path = rules_path()
    if not path.exists():
        return Rules()
    try:
        return load_rules(path)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(EXIT_CONFIG) from exc


def hardware_state(rules: Rules) -> HardwareState | None:
    """Current GPU/battery snapshot, or ``None`` when probing fails."""
    try:
        profiles = catalog_updater.load_active()[0].gpu_profiles
        return HardwareMonitor(rules.tiers.local, profiles=profiles).get_state()
    except Exception:  # a broken probe must never stop a CLI command
        return None


def _registry(rules: Rules) -> FeatureRegistry:
    registry = FeatureRegistry()
    discover_features(registry)
    registry.set_flags(rules.features)
    return registry


@dataclass(frozen=True)
class Runtime:
    rules: Rules
    catalog: Catalog
    catalog_source: str
    db: Database
    project: ProjectConfig
    project_path: Path | None
    registry: FeatureRegistry


def load_runtime(*, as_json: bool = False) -> Runtime:
    rules = load_rules_or_exit()
    catalog, source = catalog_updater.load_active()
    try:
        project, project_path = load_project_config(Path.cwd())
    except ConfigError as exc:
        fail(
            "invalid_project_config",
            str(exc),
            fix="Fix the problems listed above in .headwaters.yaml.",
            exit_code=EXIT_CONFIG,
            as_json=as_json,
        )
    return Runtime(
        rules=rules,
        catalog=catalog,
        catalog_source=source,
        db=open_database(),
        project=project,
        project_path=project_path,
        registry=_registry(rules),
    )


class SizedAnalyzer:
    """Deterministic analyzer that can pretend the request has a given size (``--tokens``).

    Layer 3 (the CPU classifier) is not used: a dry run must not call any model, not even a
    local one. The complexity formula mirrors layer 1 (one level per token step passed, plus
    one for a reasoning hint; agentic is on the floor, not here), so a big ``--tokens`` raises it.
    """

    def __init__(self, base: Analyzer, tokens: int | None = None) -> None:
        self._base = base
        self._tokens = tokens

    def analyze(self, inp: AnalyzerInput) -> Requirements:
        found = self._base.analyze(inp)
        size = self._tokens
        if size is None:
            return found
        steps = self._base.hints.complexity_token_steps
        level = 1 + sum(1 for step in steps if size > step)
        level += int(found.soft.reasoning_hint)
        hard = dataclasses.replace(found.hard, min_ctx=size)
        soft = dataclasses.replace(found.soft, complexity=max(1, min(5, level)))
        return dataclasses.replace(found, hard=hard, soft=soft, est_tokens=size)


def _never_runs(decision: RouteDecision) -> ExecTarget:
    raise RuntimeError("the CLI dry run never executes a model")


def build_executor(rules: Rules, catalog: Catalog) -> Executor:
    """A real executor for CLI commands that must call a model (``models verify-tools``).

    Same wiring the daemon uses (key store, loopback guard, catalog cloud-proxy patterns), so a
    probe is bounded and refused exactly where a normal call would be.
    """
    keys = KeyStore(rules)
    return Executor(
        rules,
        WallClassifier(catalog.wall_patterns),
        TargetBuilder(rules, key_store=keys),
        api_key_for=keys.api_key_for,
        key_alias_for=keys.key_alias_for,
        remote_patterns=remote_patterns_from(catalog),
    )


def build_router(rt: Runtime, hw: HardwareState | None, tokens: int | None = None) -> Router:
    """A router for dry runs: real scope, policy, quotas and candidates; no execution."""
    catalog = rt.catalog
    keys = KeyStore(rt.rules)
    executor = Executor(
        rt.rules,
        WallClassifier(catalog.wall_patterns),
        _never_runs,
        api_key_for=keys.api_key_for,
        key_alias_for=keys.key_alias_for,
        remote_patterns=remote_patterns_from(catalog),
    )
    candidates = CandidateBuilder(rt.db, rt.rules, catalog, lambda: hw)
    return Router(
        rt.rules,
        rt.db,
        SizedAnalyzer(Analyzer.from_catalog(catalog), tokens),
        candidates,
        executor,
        QuotaTracker(rt.db, rt.rules.walls, catalog.free_limits),
        secrets=SecretDetector.from_catalog(catalog),
        registry=rt.registry,
        hardware=lambda: hw,
        gpu_profiles=catalog.gpu_profiles,
    )
