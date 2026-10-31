# Tests: priority set

Policy, scope, executor scope, router, walls, quotas, fit, import contract and their fakes.

30 files.

| File | Lines | Purpose |
| --- | ---: | --- |
| `tests/cli_support.py` | 102 | Shared setup for the CLI tests (HDW-118): an isolated home, seeded models, a fake GPU. |
| `tests/conftest.py` | 13 |  |
| `tests/executor_support.py` | 136 | Shared fakes for the executor tests (HDW-115). All HTTP and litellm calls are mocked. |
| `tests/policy_golden_support.py` | 141 | Shared pieces for the HDW-114 golden table: a model pool, a scenario runner, assertions. |
| `tests/router_rig.py` | 86 | The router wired to fakes, with handles on everything a test asserts on (HDW-117). |
| `tests/router_support.py` | 129 | Shared fakes for the router tests (HDW-117). All providers are mocked; nothing leaves the box. |
| `tests/test_candidates.py` | 118 | HDW-118: the database-backed candidate source and the score lookup under it. |
| `tests/test_cli_pick.py` | 162 | HDW-118: ``headwaters pick`` shows the reasons and the fallbacks, and is a pure dry run. |
| `tests/test_cli_pick_filters.py` | 78 | HDW-118: the ``pick`` flags (--image, --tools, --tokens) and wall/ETA reporting. |
| `tests/test_executor.py` | 145 | HDW-115: executor streaming, reasoning capture, tool calls and wall-classified errors. |
| `tests/test_executor_scope.py` | 128 | HDW-115: local-scope guard (v3.3 section D) and cloud attribution headers. |
| `tests/test_hardware_fit.py` | 112 | HDW-109: KV math, fit, num_ctx and weights calibration (worked example from the ticket). |
| `tests/test_import_contract.py` | 48 | The import-linter contract must pass on the real repo and fail on a deliberate violation. |
| `tests/test_policy_floor.py` | 59 | HDW-113: quality floor, filter on battery, and agentic-tools requirement. |
| `tests/test_policy_golden_a.py` | 194 | HDW-114 golden table A: base ladder, strategies, optimistic rung, walls. |
| `tests/test_policy_golden_b.py` | 120 | HDW-114 golden table B: vision, agentic, scopes, private lock. Same pool as table A. |
| `tests/test_policy_golden_c.py` | 118 | HDW-114 golden table C: GPU profiles, battery, GPU busy, unscored models, pin, premium. |
| `tests/test_policy_scoring.py` | 154 | HDW-113: hard filter, quality floor and score terms. Every weight comes from rules. |
| `tests/test_policy_strategies.py` | 76 | HDW-113: strategies (cost_saver, quality_first, primary_first), breakdown and ranking. |
| `tests/test_policy_terms.py` | 99 | HDW-113: every score term (quality, complexity, warm, evict, latency, battery, GPU, cost). |
| `tests/test_quotas.py` | 393 | HDW-111: quota tracker and cooldowns, with a frozen clock for every transition. |
| `tests/test_router.py` | 122 | HDW-117: router façade cascades and scope, end to end with mocked providers. |
| `tests/test_router_plan.py` | 82 | HDW-118: ``Router.plan`` is a dry run of steps 1-3 with no model call and no stored state. |
| `tests/test_router_session.py` | 87 | HDW-117: session keying and warm preference, progress hook, secret detection. |
| `tests/test_router_stream.py` | 77 | HDW-117: streaming failover and the handle() entry point. |
| `tests/test_router_verifiers.py` | 72 | HDW-117: the router's verification helpers (BasicVerifier wiring, broken verifiers). |
| `tests/test_router_verify_log.py` | 132 | HDW-117: router verification, escalation and decision/outcome logging. |
| `tests/test_scope.py` | 317 |  |
| `tests/test_secrets_detect.py` | 139 |  |
| `tests/test_walls.py` | 383 | HDW-110: wall classifier, with realistically shaped bodies for each provider. |

## tests/cli_support.py

````python
"""Shared setup for the CLI tests (HDW-118): an isolated home, seeded models, a fake GPU.

Nothing here touches the network or a real GPU. ``invoke`` runs the real Typer app through
``CliRunner`` against a throwaway ``HEADWATERS_HOME``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from headwaters.cli import runtime
from headwaters.cli.main import app
from headwaters.core import paths
from headwaters.core.hardware.gpu import GpuInfo
from headwaters.core.hardware.state import BatteryState, HardwareState, build_state
from headwaters.core.rules import LocalTier
from headwaters.core.state import Database, open_database
from headwaters.core.state_models import ModelRow, ScoreRow, set_score, upsert_model

runner = CliRunner()

LOCAL_ARCH = {"n_layers": 48, "n_kv_heads": 8, "head_dim": 128}
CODE_PROMPT = "write a function that adds two numbers"
FAKE_KEY = "sk-Zq8Xv3Lm9Pw2Rt5Yb7Nc4Hd6Jf1Kg"  # secret-shaped, not a placeholder


def gpu_state(total_gb: int = 16, on_battery: bool = False) -> HardwareState:
    gpu = GpuInfo("Test RTX", total_gb * 1024.0, total_gb * 1024.0 - 512, True, "nvml")
    return build_state(gpu, LocalTier(), BatteryState(True, on_battery, 80.0))


def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hw: HardwareState | None = None) -> Path:
    """Point Headwaters at ``tmp_path``, run from there, and fix the hardware snapshot."""
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.chdir(tmp_path)
    state = hw if hw is not None else gpu_state()
    monkeypatch.setattr(runtime, "hardware_state", lambda rules: state)
    return tmp_path


def add_model(
    db: Database,
    model_id: str,
    tier: str,
    code: float | None = 85.0,
    *,
    agentic: float | None = None,
    **row: Any,
) -> None:
    fields: dict[str, Any] = {
        "provider": "ollama" if tier == "local" else "vendor",
        "modalities": ["text"],
        "tools_ok": True,
        "json_ok": True,
        "ctx": 32768,
        "source": "test",
    }
    if tier == "local":
        fields.update(arch=LOCAL_ARCH, size_gb=9.0)
    fields.update(row)
    upsert_model(db, ModelRow(id=model_id, tier=tier, **fields))
    for task, value in (("code", code), ("chat", code), ("agentic_tools", agentic)):
        if value is not None:
            set_score(db, ScoreRow(model_id, task, seed=value))


def seed_models(tmp_path: Path) -> Database:
    """A free cloud model (best), a warm local model that also clears the floor, and a paid one.

    The cloud model outscores the local one even with the warm bonus, so a plain pick lands on
    the cloud model with the local one as its fallback.
    """
    db = open_database()
    add_model(db, "vendor/free:free", "free_cloud", 98.0, agentic=98.0, ctx=131072)
    add_model(db, "qwen:14b", "local", 80.0, agentic=80.0, warm=True, size_vram_gb=8.0)
    add_model(db, "vendor/big", "paid", 95.0, agentic=95.0, ctx=200000, price_in=3, price_out=15)
    return db


def write_rules(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config" / "rules.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def invoke_json(*args: str) -> tuple[Result, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"])
    return result, json.loads(result.stdout)
````

## tests/conftest.py

````python
import sys

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform == "win32":
        return
    skip = pytest.mark.skip(reason="Windows-only test")
    for item in items:
        if "windows" in item.keywords:
            item.add_marker(skip)
````

## tests/executor_support.py

````python
"""Shared fakes for the executor tests (HDW-115). All HTTP and litellm calls are mocked."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import (
    BackendRequest,
    Delta,
    ExecOptions,
    ExecResult,
    ExecTarget,
    ExecutorConfig,
    StreamEvent,
)
from headwaters.core.policy.scoring import ScoreBreakdown
from headwaters.core.policy.types import RouteDecision
from headwaters.core.rules import Rules
from headwaters.core.scope import ALL_TIERS, LOCAL_TIERS, ScopeDecision, TierName
from headwaters.core.walls import WallClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
WALLS = WallClassifier(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH).wall_patterns)
MESSAGES = [{"role": "user", "content": "hello"}]


def decision(tier: TierName = "free_cloud", model: str = "openrouter/acme/m:free") -> RouteDecision:
    tiers = LOCAL_TIERS if tier == "local" else ALL_TIERS
    scope = ScopeDecision("local" if tier == "local" else "auto", tiers, "cost_saver", "agentic")
    zero = ScoreBreakdown(model, 80, 0, 0, 0, 0, 0, 0, 0, 0, 80, 50, True, 0)
    return RouteDecision(model, tier, 80, 50, False, scope, (), (), (), "cheap", False, zero)


def target_for(d: RouteDecision, **kw: Any) -> ExecTarget:
    local = d.tier == "local"
    base: dict[str, Any] = {
        "model_id": d.model,
        "provider": "ollama" if local else d.model.split("/", 1)[0],
        "route": d.model,
        "num_ctx": 16384 if local else None,
    }
    return ExecTarget(**{**base, **kw})


Step = Delta | ProviderError | tuple[str, float]


class ScriptedBackend:
    """Plays a script of ``Delta``s, ``ProviderError``s and ``("sleep", seconds)`` steps."""

    def __init__(self, script: Sequence[Step]) -> None:
        self.script = list(script)
        self.requests: list[BackendRequest] = []
        self.closed = False

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        self.requests.append(request)
        try:
            for step in self.script:
                if isinstance(step, ProviderError):
                    raise step
                if isinstance(step, tuple):
                    await asyncio.sleep(step[1])
                    continue
                yield step
        finally:
            self.closed = True


def make_executor(
    backend: ScriptedBackend,
    *,
    rules: Rules | None = None,
    config: ExecutorConfig | None = None,
    cold: bool | None = None,
    cloud_calls: list[int] | None = None,
    **kw: Any,
) -> Executor:
    def resolve(d: RouteDecision) -> ExecTarget:
        return target_for(d, cold=cold)

    def cloud_factory() -> ScriptedBackend:
        if cloud_calls is not None:
            cloud_calls.append(1)
        return backend

    local_factory: Callable[[str], ScriptedBackend] = lambda host: backend  # noqa: E731
    return Executor(
        rules or Rules(),
        WALLS,
        resolve,
        config=config,
        cloud_factory=cloud_factory,
        local_factory=local_factory,
        **kw,
    )


def stream_events(executor: Executor, d: RouteDecision) -> list[StreamEvent]:
    async def go() -> list[StreamEvent]:
        return [e async for e in executor.run(d, MESSAGES, None, True)]

    return asyncio.run(go())


def run_once(
    executor: Executor, d: RouteDecision, options: ExecOptions | None = None
) -> ExecResult:
    return asyncio.run(executor.run(d, MESSAGES, None, False, options))


def kinds(events: list[StreamEvent]) -> list[str]:
    return [e.kind for e in events]


def chunk(
    content: str | None = None,
    reasoning: str | None = None,
    finish: str | None = None,
    usage: Any = None,
    tool_calls: Any = None,
) -> Any:
    """A litellm-shaped stream chunk (duck-typed)."""
    delta = SimpleNamespace(
        content=content, reasoning_content=reasoning, reasoning=None, tool_calls=tool_calls
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=usage)
````

## tests/policy_golden_support.py

````python
"""Shared pieces for the HDW-114 golden table: a model pool, a scenario runner, assertions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from headwaters.core.analyzer.requirements import HardRequirements, Requirements
from headwaters.core.plugins import CURRENT_API_VERSION
from headwaters.core.policy.decide import decide
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.types import PolicyContext, PolicyRequest
from headwaters.core.quotas import Availability
from headwaters.core.rules import Rules
from headwaters.core.scope import LOCAL_TIERS, SCOPE_TIERS, ScopeDecision
from headwaters.core.state_models import ModelRow
from tests.test_policy_scoring import fit, hw, soft


class Escalator:
    api_version = CURRENT_API_VERSION

    def should_escalate(self, attempt: int, problems: Any) -> bool:
        return True


def _c(mid: str, tier: str, scores: dict[str, float], **kw: Any) -> Candidate:
    row_kw: dict[str, Any] = {"modalities": ["text"], "tools_ok": True, "json_ok": True}
    row_kw["ctx"] = kw.pop("ctx", 128_000)
    for key in ("price_in", "price_out"):
        if key in kw:
            row_kw[key] = kw.pop(key)
    row = ModelRow(id=mid, tier=tier, provider="prov", **row_kw)
    return Candidate(row, scores, **kw)


def pool() -> dict[str, Candidate]:
    """Default models. Code floor at complexity 2 is 80; at 3 it is 85; at 5 it is 95."""
    base = {"chat": 90.0, "agentic_tools": 60.0}
    return {
        "loc14": _c("loc14", "local", {"code": 85.0, **base}, fit=fit(), expected_tps=35.0),
        "loc7": _c("loc7", "local", {"code": 70.0, **base}, fit=fit(), expected_tps=60.0),
        "free1": _c("free1", "free_cloud", {"code": 82.0, "chat": 90.0, "agentic_tools": 70.0}),
        "free2": _c("free2", "free_cloud", {"code": 75.0, "chat": 90.0, "agentic_tools": 70.0}),
        "paid1": _c(
            "paid1",
            "paid",
            {"code": 95.0, "chat": 95.0, "agentic_tools": 96.0},
            price_in=3.0,
            price_out=15.0,
            is_primary=True,
        ),
        "orauto": _c(
            "orauto",
            "paid",
            {"code": 90.0, "chat": 90.0, "agentic_tools": 90.0},
            price_in=1.0,
            price_out=2.0,
            role="openrouter_auto",
        ),
        "orfree": _c("orfree", "free_cloud", {"code": 60.0, "chat": 60.0}, role="openrouter_free"),
        "prem": _c("prem", "paid", {"code": 99.0, "chat": 99.0}, role="premium"),
    }


def build(
    mods: dict[str, dict[str, Any]] | None, drop: tuple[str, ...], only: tuple[str, ...]
) -> list[Candidate]:
    out: list[Candidate] = []
    for mid, cand in pool().items():
        if mid in drop or (only and mid not in only):
            continue
        change = dict((mods or {}).get(mid, {}))
        row = change.pop("row", None)
        if row:
            cand = replace(cand, row=replace(cand.row, **row))
        out.append(replace(cand, **change))
    for mid, spec in (mods or {}).items():
        if mid.startswith("new:"):  # an extra model: {"tier":..., "scores":...}
            extra = dict(spec)  # never mutate the shared scenario table
            out.append(_c(mid[4:], extra.pop("tier"), extra.pop("scores"), **extra))
    return out


def run(s: dict[str, Any]) -> Any:
    """Run one scenario spec and return the ``RouteDecision`` or the ``NoRouteError``."""
    rules = Rules.model_validate(s.get("rules", {}))
    task = s.get("task", "code")
    reqs = Requirements(
        HardRequirements(
            s.get("vision", False),
            s.get("tools", False),
            s.get("json", False),
            s.get("min_ctx", 1000),
        ),
        soft(task, s.get("complexity", 2), s.get("risk", "low"), s.get("agentic", False)),
        0.9,
        "test",
        s.get("tokens", 1000),
        False,
    )
    name = s.get("scope", "auto")
    locks = tuple(s.get("locks", ()))
    tiers = LOCAL_TIERS if locks else SCOPE_TIERS[name]
    scope = ScopeDecision(name, tiers, s.get("strategy", "cost_saver"), "agentic", locks)
    has_hw = "battery" in s or "busy" in s
    state = hw(s.get("battery", "normal"), s.get("busy", False)) if has_hw else None
    cands = build(s.get("mods"), tuple(s.get("drop", ())), tuple(s.get("only", ())))
    esc = Escalator() if s.get("escalation") else None
    ctx = PolicyContext(cands, rules, state, esc)
    try:
        return decide(PolicyRequest(reqs, scope, s.get("pin"), s.get("force")), ctx)
    except NoRouteError as err:
        return err


def check(s: dict[str, Any], expect: dict[str, Any]) -> None:
    got = run(s)
    if "error" in expect:
        assert isinstance(got, NoRouteError), f"expected error, got {got}"
        assert got.code == expect["error"]
        codes = {x.code for x in got.suggestions}
        assert set(expect.get("sugg", ())) <= codes
        if "etas" in expect:
            assert got.etas == expect["etas"]
        return
    assert not isinstance(got, NoRouteError), f"unexpected error {got.code}"
    assert got.model == expect["model"], got.reasons
    for key, attr in (("rung", "rung"), ("below", "below_floor"), ("verify", "verify_required")):
        if key in expect:
            assert getattr(got, attr) == expect[key]
    if "sugg" in expect:
        assert {x.code for x in got.suggestions} == set(expect["sugg"])
    if "fallbacks" in expect:
        assert list(got.fallbacks[: len(expect["fallbacks"])]) == expect["fallbacks"]


EXHAUSTED = Availability("exhausted", None)
COOLDOWN = Availability("cooldown", 45.0)
````

## tests/router_rig.py

````python
"""The router wired to fakes, with handles on everything a test asserts on (HDW-117)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from headwaters.core.analyzer.requirements import Requirements
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
    ) -> None:
        self.rules = rules or Rules()
        self.db: Database = open_database(tmp_path / "state.db")
        self.dispatch = Dispatch(scripts)
        self.cloud_clients = 0
        self.waits: list[float] = []
        self.analyzer = FixedAnalyzer(reqs)
        pool = list(candidates)

        def cloud_factory() -> Dispatch:
            self.cloud_clients += 1
            return self.dispatch

        executor = Executor(
            self.rules,
            WALLS,
            lambda d: target_for(d, cold=False),
            cloud_factory=cloud_factory,
            local_factory=lambda host: self.dispatch,
        )

        async def sleep(seconds: float) -> None:
            self.waits.append(seconds)

        self.router = Router(
            self.rules,
            self.db,
            self.analyzer,
            lambda _reqs: pool,
            executor,
            QuotaTracker(self.db, self.rules.walls),
            secrets=secrets,
            verifiers=verifiers,
            registry=registry if registry is not None else registry_with(),
            config=config,
            sleep=sleep,
        )

    def run(self, **kw: Any) -> RouteResult:
        kw.setdefault("messages", MESSAGES)
        return asyncio.run(self.router.run(RouteRequest(**kw)))

    def stream(self, **kw: Any) -> list[RouteEvent]:
        kw.setdefault("messages", MESSAGES)

        async def go() -> list[RouteEvent]:
            return [e async for e in self.router.stream(RouteRequest(stream=True, **kw))]

        return asyncio.run(go())
````

## tests/router_support.py

````python
"""Shared fakes for the router tests (HDW-117). All providers are mocked; nothing leaves the box."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from headwaters.core.analyzer.requirements import (
    AnalyzerInput,
    HardRequirements,
    Requirements,
)
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import BackendRequest, Delta
from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.plugins import CURRENT_API_VERSION
from headwaters.core.policy.filters import Candidate
from headwaters.core.router_types import (
    ProgressEvent,
)
from headwaters.core.state_models import ModelRow
from tests.executor_support import ScriptedBackend, Step
from tests.test_policy_scoring import fit, soft

OK = [Delta(content="ok answer"), Delta(finish_reason="stop")]
RATE_LIMITED = ProviderError(429, {"retry-after": "60"}, "")  # long wait: cascade at once
NO_CREDIT = ProviderError(402, {}, "")
MESSAGES = [{"role": "user", "content": "write a function that adds two numbers"}]


def requirements(task: str = "code", complexity: int = 2, agentic: bool = False) -> Requirements:
    hard = HardRequirements(False, False, False, 1000)
    return Requirements(hard, soft(task, complexity, "low", agentic), 0.9, "test", 1000, False)


def candidate(mid: str, tier: str, code: float, **kw: Any) -> Candidate:
    """Prices default to 0; ``provider`` defaults to the model id so walls never overlap."""
    row = ModelRow(
        id=mid,
        tier=tier,
        provider=kw.pop("provider", mid),
        modalities=["text"],
        tools_ok=True,
        json_ok=True,
        ctx=128_000,
        price_in=kw.pop("price_in", None),
        price_out=kw.pop("price_out", None),
    )
    scores = {"code": code, "chat": code, "agentic_tools": code}
    if tier == "local":
        kw.setdefault("fit", fit())
        kw.setdefault("expected_tps", 60.0)
    return Candidate(row, scores, **kw)


class FixedAnalyzer:
    def __init__(self, reqs: Requirements | None = None) -> None:
        self.reqs = reqs or requirements()
        self.inputs: list[AnalyzerInput] = []

    def analyze(self, inp: AnalyzerInput) -> Requirements:
        self.inputs.append(inp)
        return self.reqs


class Dispatch:
    """One ``ScriptedBackend`` per model id; a model with no script fails the test loudly."""

    def __init__(self, scripts: Mapping[str, Sequence[Step]]) -> None:
        self.backends = {mid: ScriptedBackend(script) for mid, script in scripts.items()}
        self.calls: list[str] = []

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        model = request.target.model_id
        self.calls.append(model)
        if model not in self.backends:
            raise AssertionError(f"unexpected call to {model}")
        async for delta in self.backends[model].stream(request):
            yield delta


class Verifier:
    """Fails any answer that contains ``bad``."""

    api_version = CURRENT_API_VERSION

    def verify(self, candidate: str) -> list[str]:
        return ["bad_output: contains the word bad"] if "bad" in candidate else []


class Escalator:
    api_version = CURRENT_API_VERSION

    def __init__(self) -> None:
        self.calls: list[tuple[int, tuple[str, ...]]] = []

    def should_escalate(self, attempt: int, problems: Sequence[str]) -> bool:
        self.calls.append((attempt, tuple(problems)))
        return True


class Ledger:
    api_version = CURRENT_API_VERSION

    def __init__(self) -> None:
        self.events: list[Mapping[str, object]] = []

    def record(self, event: Mapping[str, object]) -> None:
        self.events.append(event)


def registry_with(
    *, paid: bool = False, escalation: Escalator | None = None, ledger: Ledger | None = None
) -> FeatureRegistry:
    registry = FeatureRegistry()
    if paid:
        registry.register(FeatureSpec("paid_scopes", True, "Paid scopes"))
    if escalation is not None:
        spec = FeatureSpec("escalation", True, "Escalation", ("EscalationPlugin",))
        registry.register(spec, {"EscalationPlugin": escalation})
    if ledger is not None:
        spec = FeatureSpec("ledger", True, "Ledger", ("LedgerPlugin",))
        registry.register(spec, {"LedgerPlugin": ledger})
    return registry


def stages(events: Sequence[ProgressEvent]) -> list[str]:
    return [e.stage for e in events]
````

## tests/test_candidates.py

````python
"""HDW-118: the database-backed candidate source and the score lookup under it."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.candidates import CandidateBuilder, fit_for_row
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.hardware.fit import FitResult
from headwaters.core.hardware.residency import ResidencyInputs
from headwaters.core.hardware.state import HardwareState
from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_models import (
    ModelRow,
    ScoreRow,
    effective_scores,
    set_score,
    upsert_model,
)
from tests.cli_support import LOCAL_ARCH, add_model, gpu_state
from tests.router_support import requirements

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state.db")


def builder(db: Database, hw: HardwareState | None, rules: Rules | None = None) -> CandidateBuilder:
    catalog = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
    return CandidateBuilder(db, rules or Rules(), catalog, lambda: hw)


def by_id(candidates: list[Candidate]) -> dict[str, Candidate]:
    return {c.id: c for c in candidates}


def fit_of(candidate: Candidate) -> FitResult:
    assert candidate.fit is not None
    return candidate.fit


def residency_of(candidate: Candidate) -> ResidencyInputs:
    assert candidate.residency is not None
    return candidate.residency


def test_effective_scores_prefer_override_then_learned_then_seed(db: Database) -> None:
    add_model(db, "m", "free_cloud", None)
    set_score(db, ScoreRow("m", "code", seed=50.0))
    set_score(db, ScoreRow("m", "chat", seed=50.0, learned_ema=60.0))
    set_score(db, ScoreRow("m", "reasoning", seed=50.0, learned_ema=60.0, override=90.0))
    set_score(db, ScoreRow("m", "extract"))  # no value at all: left out
    assert effective_scores(db) == {"m": {"code": 50.0, "chat": 60.0, "reasoning": 90.0}}


def test_cloud_candidates_carry_scores_and_no_fit(db: Database) -> None:
    add_model(db, "vendor/free:free", "free_cloud", 77.0)
    found = by_id(builder(db, gpu_state())(requirements()))["vendor/free:free"]
    assert found.scores["code"] == 77.0
    assert found.fit is None
    assert found.tier == "free_cloud"


def test_local_candidates_get_fit_and_residency(db: Database) -> None:
    add_model(db, "qwen:14b", "local", 80.0, warm=True, size_vram_gb=8.0)
    add_model(db, "other:7b", "local", 60.0)
    found = by_id(builder(db, gpu_state(16))(requirements()))
    warm, cold = found["qwen:14b"], found["other:7b"]
    assert fit_of(warm).status == "fits"
    assert residency_of(warm).warm is True
    assert residency_of(cold).warm is False
    assert residency_of(cold).evicts == ("qwen:14b",)  # one slot: loading it evicts the warm one


def test_catalog_speed_is_used_for_known_models(db: Database) -> None:
    add_model(db, "example-local:14b", "local", 70.0)
    found = by_id(builder(db, gpu_state(16))(requirements()))["example-local:14b"]
    assert found.expected_tps == 50.0  # desktop_16gb in the seed
    add_model(db, "mystery:1b", "local", 10.0)
    assert by_id(builder(db, gpu_state(16))(requirements()))["mystery:1b"].expected_tps is None


def test_without_a_gpu_local_models_do_not_fit(db: Database) -> None:
    add_model(db, "qwen:14b", "local", 80.0)
    found = by_id(builder(db, None)(requirements()))["qwen:14b"]
    assert fit_of(found).usable is False


def test_paid_primary_models_are_flagged(db: Database) -> None:
    add_model(db, "vendor/big", "paid", 95.0)
    add_model(db, "vendor/other", "paid", 90.0)
    rules = Rules.model_validate({"tiers": {"paid": {"models": ["vendor/big"]}}})
    found = by_id(builder(db, gpu_state(), rules)(requirements()))
    assert found["vendor/big"].is_primary is True
    assert found["vendor/other"].is_primary is False


def test_fit_for_row_uses_decimal_gb_correctly(db: Database) -> None:
    row = ModelRow("m", "local", "ollama", ["text"], ctx=32768, size_gb=9.0, arch=LOCAL_ARCH)
    assert fit_for_row(row, 14.0, 1000, "q8_0").status == "fits"
    assert fit_for_row(row, 6.0, 1000, "q8_0").status == "offload"
    assert fit_for_row(row, 1.0, 1000, "q8_0").status == "no_fit"


def test_rows_without_architecture_data_do_not_fit(db: Database) -> None:
    row = ModelRow("m", "local", "ollama", ["text"], ctx=32768, size_gb=9.0, arch={"n_layers": 0})
    result = fit_for_row(row, 14.0, 1000, "q8_0")
    assert (result.status, result.reason) == ("no_fit", "missing_catalog_data")
    upsert_model(db, row)  # the builder must tolerate such a row too
    assert fit_of(builder(db, gpu_state())(requirements())[0]).usable is False
````

## tests/test_cli_pick.py

````python
"""HDW-118: ``headwaters pick`` shows the reasons and the fallbacks, and is a pure dry run."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from headwaters.core.state import default_db_path, open_database
from tests.cli_support import (
    CODE_PROMPT,
    FAKE_KEY,
    add_model,
    gpu_state,
    home,
    invoke,
    invoke_json,
    seed_models,
    write_rules,
)


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home(tmp_path, monkeypatch)
    seed_models(tmp_path)
    return tmp_path


def test_pick_shows_model_reasons_and_fallbacks(cli_home: Path) -> None:
    result = invoke("pick", CODE_PROMPT)
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "vendor/free:free" in out  # the pick
    assert "Why:" in out
    assert "floor" in out  # the reasons carry the quality floor
    assert "Fallbacks, in order" in out
    assert "qwen:14b" in out  # a fallback with its tier and availability
    assert "Availability" in out


def test_pick_json_has_reasons_fallbacks_and_scope(cli_home: Path) -> None:
    result, data = invoke_json("pick", CODE_PROMPT)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["model"] == "vendor/free:free"
    assert data["tier"] == "free_cloud"
    assert data["reasons"], "the decision must explain itself"
    assert [f["model"] for f in data["fallbacks"]][:1] == ["qwen:14b"]
    assert data["fallbacks"][0]["status"] == "ok"
    assert data["scope"]["tiers"] == ["free_cloud", "local"]
    assert data["requirements"]["task"] == "code"
    assert "vendor/big" in {r["model"] for r in data["rejected"]}  # paid tiers are out of scope
    assert {"model", "reason"} <= set(data["rejected"][0])


def test_pick_explains_why_a_model_is_not_eligible(cli_home: Path) -> None:
    out = invoke("pick", CODE_PROMPT).stdout
    assert "Not eligible" in out
    assert "vendor/big" in out
    assert "scope" in out


def test_pick_scope_local_is_fail_closed(cli_home: Path) -> None:
    # The only local model is weaker than the code floor. Local scope never falls back to cloud:
    # it reports a structured error that says how to loosen the scope.
    add_model(open_database(), "qwen:14b", "local", 60.0, agentic=60.0)
    result, data = invoke_json("pick", CODE_PROMPT, "--scope", "local")
    assert result.exit_code == 4
    assert data["error"]["code"] == "local_below_floor"
    assert "headwaters/cloud" in data["error"]["fix"]
    rejected = {(r["model"], r["reason"]) for r in data["error"]["rejected"]}
    assert rejected == {("vendor/free:free", "scope"), ("vendor/big", "scope")}


def test_warm_local_model_is_preferred_over_a_cold_stronger_one(cli_home: Path) -> None:
    # Loading a cold model would evict the warm one, so the warm bonus and eviction penalty keep
    # the warm model ahead even though the other scores higher (HDW-109 residency).
    add_model(open_database(), "strong:32b", "local", 95.0, agentic=95.0)
    _, data = invoke_json("pick", CODE_PROMPT, "--scope", "local")
    assert data["model"] == "qwen:14b"
    assert [f["model"] for f in data["fallbacks"]] == ["strong:32b"]


def test_pick_scope_local_uses_a_local_model_that_clears_the_floor(cli_home: Path) -> None:
    db = open_database()
    add_model(db, "qwen:14b", "local", 80.0, agentic=80.0)  # no longer warm
    add_model(db, "strong:32b", "local", 95.0, agentic=95.0)
    result, data = invoke_json("pick", CODE_PROMPT, "--scope", "local")
    assert result.exit_code == 0, data
    assert data["model"] == "strong:32b"
    assert data["tier"] == "local"
    assert data["scope"]["tiers"] == ["local"]
    assert data["below_floor"] is False
    assert {f["model"] for f in data["fallbacks"]} <= {"qwen:14b"}  # never a cloud fallback
    assert {r["model"] for r in data["rejected"]} == {"vendor/free:free", "vendor/big"}


def test_pick_bad_scope_is_a_usage_error(cli_home: Path) -> None:
    assert invoke("pick", "hi", "--scope", "everywhere").exit_code == 2


def test_private_lock_forces_local_and_blocks_cloud_request(cli_home: Path) -> None:
    assert invoke("private", "on").exit_code == 0
    _, data = invoke_json("pick", CODE_PROMPT)
    assert data["scope"]["tiers"] == ["local"]
    assert "private_lock" in data["scope"]["locks"]
    result, error = invoke_json("pick", CODE_PROMPT, "--scope", "cloud")
    assert result.exit_code == 3
    assert error["ok"] is False
    assert error["error"]["code"] == "private_lock_conflict"
    assert "headwaters private off" in error["error"]["fix"]


def test_secret_in_prompt_forces_local(cli_home: Path) -> None:
    _, data = invoke_json("pick", f"{CODE_PROMPT}; my key is {FAKE_KEY}")
    assert data["scope"]["tiers"] == ["local"]
    assert any("secret detected" in r for r in data["reasons"])
    assert FAKE_KEY not in str(data), "a detected secret must never be echoed back"


def test_pick_never_echoes_the_prompt(cli_home: Path) -> None:
    marker = "ZEBRA-MARKER-7731"
    assert marker not in invoke("pick", f"{CODE_PROMPT} {marker}").output
    _, data = invoke_json("pick", f"{CODE_PROMPT} {marker}")
    assert marker not in str(data)


def test_pick_is_a_dry_run_that_writes_nothing(cli_home: Path) -> None:
    def counts() -> list[int]:
        with closing(sqlite3.connect(default_db_path())) as con:
            tables = ("decisions", "outcomes", "sessions")
            return [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables]

    before = counts()
    assert invoke("pick", CODE_PROMPT).exit_code == 0
    assert invoke("pick", CODE_PROMPT, "--scope", "local").exit_code == 0
    assert counts() == before


def test_airplane_mode_limits_pick_to_local(cli_home: Path) -> None:
    write_rules(cli_home, "airplane: true\n")
    _, data = invoke_json("pick", CODE_PROMPT, "--scope", "free")
    assert data["model"] == "qwen:14b"
    assert data["fallbacks"] == []


def test_battery_suggestion_is_shown_for_a_local_pick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home(tmp_path, monkeypatch, gpu_state(on_battery=True))
    db = seed_models(tmp_path)
    # Only a local model clears the floor here; on battery the CLI suggests cloud (and still
    # picks local: it never leaves the local tier on its own).
    add_model(db, "vendor/free:free", "free_cloud", 10.0, agentic=10.0)
    _, data = invoke_json("pick", CODE_PROMPT, "--scope", "free")
    assert data["model"] == "qwen:14b"
    assert data["suggestions"][0]["code"] == "use_cloud"
    assert "headwaters/cloud" in invoke("pick", CODE_PROMPT, "--scope", "local").stdout
````

## tests/test_cli_pick_filters.py

````python
"""HDW-118: the ``pick`` flags (--image, --tools, --tokens) and wall/ETA reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.state import open_database
from headwaters.core.state_limits import record_wall
from tests.cli_support import CODE_PROMPT, add_model, home, invoke, invoke_json, seed_models

FAR_FUTURE = 4_102_444_800.0  # 2100-01-01


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home(tmp_path, monkeypatch)
    seed_models(tmp_path)
    return tmp_path


def test_image_flag_requires_a_vision_model(cli_home: Path) -> None:
    result, data = invoke_json("pick", "what is in this picture", "--image")
    assert result.exit_code == 4
    reasons = {r["reason"] for r in data["error"]["rejected"]}
    assert "no_vision" in reasons
    add_model(open_database(), "vendor/eyes:free", "free_cloud", 90.0, modalities=["text", "image"])
    _, found = invoke_json("pick", "what is in this picture", "--image")
    assert found["model"] == "vendor/eyes:free"
    assert found["requirements"]["vision"] is True


def test_tools_flag_filters_models_without_tool_support(cli_home: Path) -> None:
    add_model(open_database(), "vendor/notools:free", "free_cloud", 99.0, tools_ok=False)
    _, plain = invoke_json("pick", CODE_PROMPT)
    assert plain["model"] == "vendor/notools:free"
    _, with_tools = invoke_json("pick", CODE_PROMPT, "--tools")
    assert with_tools["requirements"]["tools"] is True
    assert with_tools["model"] == "vendor/free:free"
    assert {"model": "vendor/notools:free", "reason": "no_tools"} in with_tools["rejected"]


def test_tokens_flag_sizes_the_request(cli_home: Path) -> None:
    _, small = invoke_json("pick", CODE_PROMPT)
    result, big = invoke_json("pick", CODE_PROMPT, "--tokens", "100000")
    assert result.exit_code == 0, big
    assert big["requirements"]["est_tokens"] == 100000
    assert big["requirements"]["complexity"] > small["requirements"]["complexity"]
    assert big["floor"] > small["floor"]  # a bigger request raises the quality floor
    # The local model's 32k context cannot hold it: it drops out of the fallbacks.
    assert {"model": "qwen:14b", "reason": "no_fit_context_too_large"} in big["rejected"]
    assert big["fallbacks"] == []


def test_tokens_must_be_positive(cli_home: Path) -> None:
    assert invoke("pick", "hi", "--tokens", "0").exit_code == 2


def test_no_models_yet_says_how_to_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home(tmp_path, monkeypatch)
    result = invoke("pick", "hi")
    assert result.exit_code == 4
    assert "headwaters models refresh" in result.output


def test_walled_model_is_skipped_and_status_shows_its_eta(cli_home: Path) -> None:
    record_wall(open_database(), "model:vendor/free:free", "rate_limit", until=FAR_FUTURE)
    _, data = invoke_json("pick", CODE_PROMPT, "--scope", "free")
    assert data["model"] == "qwen:14b"
    assert {"model": "vendor/free:free", "reason": "unavailable_cooldown"} in data["rejected"]
    _, status = invoke_json("status")
    wall = status["walls"][0]
    assert wall["subject"] == "model:vendor/free:free"
    assert wall["kind"] == "rate_limit"
    assert wall["eta_s"] > 0
    assert "rate limited" in invoke("status").stdout
````

## tests/test_executor.py

````python
"""HDW-115: executor streaming, reasoning capture, tool calls and wall-classified errors."""

from __future__ import annotations

import pytest

from headwaters.core.executor.errors import ExecutorError, ProviderError
from headwaters.core.executor.types import Delta, ExecOptions, ToolCallDelta, Usage
from headwaters.core.rules import Rules
from tests.executor_support import (
    ScriptedBackend,
    Step,
    decision,
    kinds,
    make_executor,
    run_once,
    stream_events,
)

STREAM: list[Step] = [
    Delta(reasoning="think "),
    Delta(reasoning="hard"),
    Delta(content="Hel"),
    Delta(content="lo"),
    Delta(finish_reason="stop", usage=Usage(7, 3)),
]


def test_stream_events_order_and_reasoning_separate() -> None:
    events = stream_events(make_executor(ScriptedBackend(STREAM)), decision())
    assert kinds(events) == [
        "first_token",
        "reasoning",
        "reasoning",
        "content",
        "content",
        "done",
    ]
    done = events[-1].result
    assert done is not None
    assert done.content == "Hello"
    assert done.reasoning == "think hard"
    assert done.usage == Usage(7, 3)
    assert done.finish_reason == "stop"
    assert done.first_token_s is not None


def test_first_token_event_is_emitted_exactly_once_and_first() -> None:
    events = stream_events(make_executor(ScriptedBackend(STREAM)), decision())
    assert kinds(events).count("first_token") == 1
    assert events[0].kind == "first_token"


def test_non_streaming_collects_result() -> None:
    result = run_once(make_executor(ScriptedBackend(STREAM)), decision())
    assert (result.content, result.reasoning) == ("Hello", "think hard")
    assert result.model == "openrouter/acme/m:free"


def test_tool_calls_are_assembled_from_fragments() -> None:
    script: list[Step] = [
        Delta(tool_calls=(ToolCallDelta(0, "c1", "read_file", '{"pa'),)),
        Delta(tool_calls=(ToolCallDelta(0, None, None, 'th": "a.py"}'),)),
        Delta(finish_reason="tool_calls"),
    ]
    events = stream_events(make_executor(ScriptedBackend(script)), decision())
    assert kinds(events) == ["first_token", "tool_call", "done"]
    call = events[1].tool_call
    assert call is not None
    assert (call.id, call.name, call.arguments) == ("c1", "read_file", '{"path": "a.py"}')


def test_stream_without_output_has_no_first_token() -> None:
    result = run_once(make_executor(ScriptedBackend([Delta(finish_reason="stop")])), decision())
    assert result.content == ""
    assert result.first_token_s is None


def test_backend_is_closed_after_run() -> None:
    backend = ScriptedBackend(STREAM)
    run_once(make_executor(backend), decision())
    assert backend.closed


def test_local_request_carries_keep_alive_num_ctx_and_schema() -> None:
    backend = ScriptedBackend(STREAM)
    opts = ExecOptions(reasoning=True, json_schema={"type": "object"}, max_tokens=64)
    run_once(make_executor(backend), decision("local", "qwen"), opts)
    request = backend.requests[0]
    assert request.keep_alive == Rules().tiers.local.keep_alive
    assert request.target.num_ctx == 16384
    assert request.options.json_schema == {"type": "object"}


def test_error_before_first_token_raises_with_wall() -> None:
    err = ProviderError(429, {"retry-after": "30"}, "slow down")
    with pytest.raises(ExecutorError) as info:
        stream_events(make_executor(ScriptedBackend([err])), decision())
    exc = info.value
    assert exc.code == "rate_limit"
    assert exc.before_first_token is True
    assert exc.wall is not None
    assert exc.wall.is_wall
    assert exc.wall.retry_after == pytest.approx(30, abs=1)
    assert "slow down" not in str(exc)
    assert "slow down" not in repr(exc.as_dict())


def test_error_after_first_token_is_terminal_event_not_exception() -> None:
    script: list[Step] = [Delta(content="par"), ProviderError(503, None, "overloaded")]
    events = stream_events(make_executor(ScriptedBackend(script)), decision())
    assert kinds(events) == ["first_token", "content", "error"]
    error = events[-1].error
    assert error is not None
    assert error.before_first_token is False
    assert error.code == "overloaded"


def test_non_streaming_error_after_tokens_raises() -> None:
    ex = make_executor(ScriptedBackend([Delta(content="x"), ProviderError(500, None, "boom")]))
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision())
    assert info.value.code == "provider_error"
    assert info.value.before_first_token is False


@pytest.mark.parametrize(
    ("status", "code"),
    [(402, "exhausted"), (503, "overloaded"), (418, "provider_error")],
)
def test_errors_map_through_wall_classifier(status: int, code: str) -> None:
    ex = make_executor(ScriptedBackend([ProviderError(status, None, "")]))
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision())
    assert info.value.code == code
    assert info.value.status == status


def test_connection_error_code_names_ollama_for_local() -> None:
    ex = make_executor(ScriptedBackend([ProviderError(0, None, "", kind="connection")]))
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision("local", "qwen"))
    assert info.value.code == "connection_error"
    assert info.value.provider == "ollama"
````

## tests/test_executor_scope.py

````python
"""HDW-115: local-scope guard (v3.3 section D) and cloud attribution headers."""

from __future__ import annotations

from dataclasses import replace

import pytest

from headwaters.core.executor.errors import LocalScopeViolation, ScopeViolation
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.guard import assert_loopback_url
from headwaters.core.executor.types import Delta, ExecResult
from headwaters.core.policy.types import RouteDecision
from headwaters.core.rules import Rules
from headwaters.core.scope import FREE_TIERS, ScopeDecision
from tests.executor_support import ScriptedBackend, decision, make_executor, run_once

SCRIPT = [Delta(content="Hello"), Delta(finish_reason="stop")]


def run(executor: Executor, d: RouteDecision) -> ExecResult:
    return run_once(executor, d)


def rules_with_host(host: str) -> Rules:
    return Rules.model_validate({"tiers": {"local": {"host": host}}})


def test_local_scope_never_constructs_cloud_backend() -> None:
    cloud_calls: list[int] = []
    ex = make_executor(ScriptedBackend(SCRIPT), cloud_calls=cloud_calls)
    run(ex, decision("local", "qwen"))
    assert cloud_calls == []


def test_cloud_tier_in_local_scope_is_refused_before_any_backend() -> None:
    cloud_calls: list[int] = []
    backend = ScriptedBackend(SCRIPT)
    ex = make_executor(backend, cloud_calls=cloud_calls)
    bad = replace(decision("free_cloud"), scope=decision("local").scope)
    with pytest.raises(LocalScopeViolation):
        run(ex, bad)
    assert cloud_calls == []
    assert backend.requests == []


@pytest.mark.parametrize(
    "host",
    [
        "http://example.com:11434",
        "http://192.168.1.20:11434",
        "http://10.0.0.5:11434",
        "https://localhost:11434",
        "http://localhost.evil.com:11434",
        "http://0.0.0.0:11434",
        "http://[::2]:11434",
        "ftp://127.0.0.1",
        "http://127.0.0.1:notaport",
        "localhost:11434",
        "",
    ],
)
def test_non_loopback_ollama_host_raises_in_local_scope(host: str) -> None:
    backend = ScriptedBackend(SCRIPT)
    ex = make_executor(backend, rules=rules_with_host(host))
    with pytest.raises(LocalScopeViolation):
        run(ex, decision("local", "qwen"))
    assert backend.requests == []


@pytest.mark.parametrize(
    "host",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://[::1]:11434",
        "http://127.5.5.5:11434",
        "http://LOCALHOST:11434/",
    ],
)
def test_loopback_ollama_hosts_are_accepted(host: str) -> None:
    ex = make_executor(ScriptedBackend(SCRIPT), rules=rules_with_host(host))
    assert run(ex, decision("local", "qwen")).content == "Hello"


def test_assert_loopback_url_strips_trailing_slash() -> None:
    assert assert_loopback_url("http://localhost:11434/") == "http://localhost:11434"


def test_airplane_mode_blocks_cloud_but_allows_local() -> None:
    backend = ScriptedBackend(SCRIPT)
    ex = make_executor(backend, rules=Rules(airplane=True))
    with pytest.raises(ScopeViolation):
        run(ex, decision("free_cloud"))
    assert backend.requests == []
    assert run(ex, decision("local", "qwen")).content == "Hello"


def test_tier_outside_scope_is_refused() -> None:
    free = ScopeDecision("free", FREE_TIERS, "cost_saver", "agentic")
    bad = replace(decision("paid", "anthropic/claude-x"), scope=free)
    with pytest.raises(ScopeViolation):
        run(make_executor(ScriptedBackend(SCRIPT)), bad)


def test_attribution_is_off_by_default_and_openrouter_only() -> None:
    backend = ScriptedBackend(SCRIPT)
    run(make_executor(backend), decision())
    assert backend.requests[0].headers == {}  # no telemetry by default

    rules = Rules.model_validate({"attribution": {"referer": "https://hw.example", "title": "HW"}})
    on = ScriptedBackend(SCRIPT)
    run(make_executor(on, rules=rules), decision())
    assert on.requests[0].headers == {
        "HTTP-Referer": "https://hw.example",
        "X-OpenRouter-Title": "HW",
    }
    other = ScriptedBackend(SCRIPT)
    run(make_executor(other, rules=rules), decision("paid", "anthropic/claude-x"))
    assert other.requests[0].headers == {}


def test_api_key_comes_from_the_key_resolver() -> None:
    backend = ScriptedBackend(SCRIPT)
    ex = make_executor(backend, api_key_for=lambda provider: f"key-for-{provider}")
    run(ex, decision())
    assert backend.requests[0].api_key == "key-for-openrouter"
````

## tests/test_hardware_fit.py

````python
"""HDW-109: KV math, fit, num_ctx and weights calibration (worked example from the ticket)."""

from __future__ import annotations

import pytest

from headwaters.core.catalog.schema import Arch, ModelEntry
from headwaters.core.hardware.fit import (
    GIB,
    FitConfig,
    WeightsCalibration,
    evaluate_fit,
    kv_bytes,
    kv_bytes_per_token,
    max_ctx_tokens,
)

ARCH = Arch(n_layers=48, n_kv_heads=8, head_dim=128)


def coder14b(**kw: object) -> ModelEntry:
    base: dict[str, object] = {
        "id": "qwen-coder-14b",
        "provider": "ollama",
        "size_gb": 9.0,
        "arch": ARCH,
        "expected_tps": {"laptop_12gb": 35.0, "desktop_16gb": 50.0},
    }
    base.update(kw)
    return ModelEntry(**base)  # type: ignore[arg-type]


def test_kv_bytes_formula() -> None:
    assert kv_bytes_per_token(ARCH, "f16") == 2 * 48 * 8 * 128 * 2  # ~0.19 MB
    assert kv_bytes_per_token(ARCH, "q8_0") == 2 * 48 * 8 * 128  # ~0.1 MB
    assert kv_bytes_per_token(ARCH, "q4_0") == 2 * 48 * 8 * 128 * 0.5
    assert kv_bytes_per_token(ARCH, "weird") == kv_bytes_per_token(ARCH, "f16")
    assert kv_bytes(ARCH, 32768, "q8_0") / GIB == pytest.approx(3.0)  # ~3.2 GB decimal


def test_worked_example_laptop_budget_10_5() -> None:
    res = evaluate_fit(coder14b(), 10.5, est_tokens=1000)
    assert res.status == "fits"
    assert 12_000 <= res.max_ctx_tokens <= 14_000
    assert res.num_ctx == 8192  # largest step <= the ~12k that fit
    assert evaluate_fit(coder14b(), 10.5, est_tokens=12_000).status == "offload"


def test_worked_example_desktop_budget_14_5() -> None:
    res = evaluate_fit(coder14b(), 14.5, est_tokens=20_000)
    assert res.status == "fits"
    assert res.num_ctx is not None
    assert res.num_ctx >= 32768
    assert res.max_ctx_tokens >= 32768


def test_request_larger_than_largest_step_is_no_fit() -> None:
    res = evaluate_fit(coder14b(), 14.5, est_tokens=60_000)  # x1.2 = 72k > 64k
    assert (res.status, res.reason) == ("no_fit", "context_too_large")
    assert not res.usable


def test_headroom_factor_applies() -> None:
    # 7000 tokens x 1.2 = 8400 > 8192, so 8k is not enough on a roomy budget.
    res = evaluate_fit(coder14b(), 14.5, est_tokens=7000)
    assert res.num_ctx is not None
    assert res.num_ctx >= 16384


def test_model_ctx_limit_caps_steps() -> None:
    res = evaluate_fit(coder14b(ctx=16384), 14.5, est_tokens=1000)
    assert res.num_ctx == 16384
    assert evaluate_fit(coder14b(ctx=4096), 14.5, est_tokens=1000).status == "no_fit"


def test_offload_vs_no_fit_by_weights() -> None:
    big = evaluate_fit(coder14b(size_gb=15.0), 10.5, est_tokens=1000)
    assert (big.status, big.reason) == ("offload", "weights_exceed_budget")
    huge = evaluate_fit(coder14b(size_gb=40.0), 10.5, est_tokens=1000)
    assert (huge.status, huge.reason) == ("no_fit", "weights_too_large")
    assert evaluate_fit(coder14b(), 0.0, est_tokens=10).status == "no_fit"  # no GPU: excluded


def test_missing_catalog_data_is_no_fit() -> None:
    res = evaluate_fit(coder14b(arch=None), 10.5, est_tokens=10)
    assert (res.status, res.reason) == ("no_fit", "missing_catalog_data")


def test_kv_type_changes_capacity() -> None:
    q8 = evaluate_fit(coder14b(), 10.5, 1000, FitConfig(kv_type="q8_0"))
    f16 = evaluate_fit(coder14b(), 10.5, 1000, FitConfig(kv_type="f16"))
    assert f16.max_ctx_tokens < q8.max_ctx_tokens
    assert max_ctx_tokens(ARCH, 20.0, 10.5, FitConfig()) == 0


def test_calibration_replaces_catalog_weights() -> None:
    cal = WeightsCalibration()
    assert cal.get("qwen-coder-14b") is None
    ps_size = int(8.0 * GIB + kv_bytes(ARCH, 8192, "q8_0"))
    weights = cal.observe("qwen-coder-14b", ps_size, 8192, ARCH, "q8_0")
    assert weights == pytest.approx(8.0)
    res = evaluate_fit(coder14b(), 10.5, 1000, calibration=cal)
    assert res.weights_calibrated
    assert res.weights_gb == pytest.approx(8.0)
    assert res.max_ctx_tokens > evaluate_fit(coder14b(), 10.5, 1000).max_ctx_tokens
    assert cal.observe("x", 1, 8192, ARCH, "q8_0") is None  # size smaller than KV: bogus


def test_sysmem_flag_forces_offload() -> None:
    res = evaluate_fit(coder14b(), 14.5, 1000, sysmem_flagged=True)
    assert (res.status, res.reason) == ("offload", "sysmem_fallback_detected")
````

## tests/test_import_contract.py

````python
"""The import-linter contract must pass on the real repo and fail on a deliberate violation."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ["features", "gateway", "mcp", "control", "installer"]


def _lint_imports(cwd: Path, config: Path) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("lint-imports", path=str(Path(sys.executable).parent))
    assert exe, "lint-imports not installed in the active environment"
    return subprocess.run(
        [exe, "--config", str(config)], cwd=cwd, capture_output=True, text=True, check=False
    )


def test_repo_contract_passes() -> None:
    result = _lint_imports(REPO_ROOT, REPO_ROOT / ".importlinter")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("target", FORBIDDEN)
def test_deliberate_violation_fails(tmp_path: Path, target: str) -> None:
    # A throwaway package with the real contract's shape; the real tree is never touched.
    pkg = tmp_path / "hwfake"
    for sub in ["core", *FORBIDDEN]:
        (pkg / sub).mkdir(parents=True)
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "core" / "bad.py").write_text(f"from hwfake import {target}\n")

    config = tmp_path / ".importlinter"
    forbidden = "\n".join(f"    hwfake.{name}" for name in FORBIDDEN)
    config.write_text(
        "[importlinter]\nroot_package = hwfake\n\n"
        "[importlinter:contract:core-is-a-leaf]\nname = core is a leaf\ntype = forbidden\n"
        f"source_modules =\n    hwfake.core\nforbidden_modules =\n{forbidden}\n"
    )

    result = _lint_imports(tmp_path, config)
    assert result.returncode != 0, result.stdout
    assert "BROKEN" in result.stdout
````

## tests/test_policy_floor.py

````python
"""HDW-113: quality floor, filter on battery, and agentic-tools requirement."""

from __future__ import annotations

import pytest

from headwaters.core.policy.filters import hard_filter
from headwaters.core.policy.scoring import floor_for, meets_floor
from headwaters.core.rules import Rules
from tests.test_policy_scoring import RULES, cand, hard, hw, local, scope, soft, why


def test_filter_battery_local_only_drops_cloud() -> None:
    res = hard_filter([cand("c"), local("l")], hard(), scope(), RULES, hw("local_only"))
    assert [c.id for c in res.kept] == ["l"]
    assert why(res, "c") == "on_battery_local_only"


def test_floor_base_plus_k_times_complexity() -> None:
    f = floor_for(soft("code", 3), RULES)  # 70 + 5*3
    assert (f.value, f.task_base, f.complexity_part) == (85, 70, 15)
    assert floor_for(soft("chat", 2), RULES).value == 50 + 3 * 2
    assert floor_for(soft("creative", 1), RULES).value == 53


def test_floor_high_risk_and_agentic_bonuses() -> None:
    assert floor_for(soft("code", 2, "high"), RULES).value == 70 + 10 + 10
    assert floor_for(soft("code", 2, "medium"), RULES).value == 80
    both = floor_for(soft("code", 2, "high", True), RULES)
    assert (both.risk_part, both.agentic_part, both.value) == (10, 15, 105)


def test_floor_values_come_from_rules() -> None:
    rules = Rules.model_validate(
        {
            "floors": {
                "code": {"base": 10, "per_complexity": 1},
                "agentic_bonus": 2,
                "high_risk_bonus": 3,
            }
        }
    )
    assert floor_for(soft("code", 4, "high", True), rules).value == 10 + 4 + 3 + 2


def test_floor_unknown_task_raises() -> None:
    with pytest.raises(ValueError, match="no floor"):
        floor_for(soft("nonsense"), RULES)


def test_meets_floor_and_agentic_tools() -> None:
    c = cand(scores={"code": 90.0, "agentic_tools": 80.0})
    assert meets_floor(c, soft("code", 2), 80)
    assert not meets_floor(c, soft("code", 2), 91)
    assert meets_floor(c, soft(agentic=True), 80)  # agentic_tools == floor passes
    assert not meets_floor(c, soft(agentic=True), 81)
    assert not meets_floor(cand(scores={"code": 99.0}), soft(agentic=True), 50)  # no tools score
    assert meets_floor(cand(scores={"code": 99.0}), soft(agentic=False), 50)
````

## tests/test_policy_golden_a.py

````python
"""HDW-114 golden table A: base ladder, strategies, optimistic rung, walls.

Pool (tests/policy_golden_support.py): code floor is 80 at complexity 2. loc14 85 (total 82),
free1 82 (79), free2 75, loc7 70, paid1 95 (primary), orauto 90, orfree 60, prem 99 (premium).
"""

from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.quotas import Availability
from tests.policy_golden_support import COOLDOWN, EXHAUSTED, check

NO_LOW = ("loc14", "free1")
FREE_ROUTER = {"tiers": {"openrouter_free_router": {"enabled": True, "max_complexity": 2}}}
WALL_PAID = {"paid1": {"availability": COOLDOWN}, "orauto": {"availability": COOLDOWN}}
ROUTERS = ["orfree", "paid1", "orauto"]
Case = tuple[str, dict[str, Any], dict[str, Any]]

CASES: list[Case] = [
    (
        "default-local-wins",
        {},
        {
            "model": "loc14",
            "rung": "cheap",
            "below": False,
            "fallbacks": ["free1", "paid1", "orauto"],
        },
    ),
    ("no-local-free-cloud", {"drop": ["loc14"]}, {"model": "free1", "rung": "cheap"}),
    (
        "cheap-short-goes-paid",
        {"drop": NO_LOW},
        {"model": "paid1", "rung": "paid", "fallbacks": ["orauto", "free2", "loc7"]},
    ),
    (
        "optimistic-with-plugin",
        {"drop": NO_LOW, "escalation": True},
        {"model": "free2", "rung": "optimistic", "below": True, "verify": True},
    ),
    (
        "optimistic-too-far",
        {"drop": NO_LOW, "escalation": True, "mods": {"free2": {"scores": {"code": 60.0}}}},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "optimistic-margin-from-rules",
        {
            "drop": NO_LOW,
            "escalation": True,
            "mods": {"free2": {"scores": {"code": 60.0}}},
            "rules": {"floors": {"optimistic_margin": 12}},
        },
        {"model": "loc7", "rung": "optimistic"},
    ),
    (
        "optimistic-needs-verifiable",
        {
            "task": "reasoning",
            "escalation": True,
            "drop": ["loc14"],
            "mods": {
                "free1": {"scores": {"reasoning": 78.0}},
                "paid1": {"scores": {"reasoning": 95.0}},
            },
        },
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "optimistic-json-counts",
        {
            "task": "chat",
            "json": True,
            "escalation": True,
            "only": ["free1", "paid1"],
            "mods": {"free1": {"scores": {"chat": 50.0}}},
        },
        {"model": "free1", "rung": "optimistic", "verify": True},
    ),
    (
        "no-json-goes-paid",
        {
            "task": "chat",
            "escalation": True,
            "only": ["free1", "paid1"],
            "mods": {"free1": {"scores": {"chat": 50.0}}},
        },
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "tie-goes-local",
        {"mods": {"free1": {"scores": {"code": 85.0}}}},
        {"model": "loc14", "fallbacks": ["free1"]},
    ),
    ("complexity-1-lower-floor", {"complexity": 1, "drop": ["loc14"]}, {"model": "free1"}),
    ("complexity-5-needs-paid", {"complexity": 5}, {"model": "paid1", "rung": "paid"}),
    ("high-risk-raises-floor", {"risk": "high"}, {"model": "paid1", "below": False}),
    ("chat-easy-stays-local", {"task": "chat"}, {"model": "loc14", "rung": "cheap"}),
    ("quality-first-still-cheap-first", {"strategy": "quality_first"}, {"model": "loc14"}),
    (
        "primary-first",
        {"strategy": "primary_first"},
        {"model": "paid1", "rung": "paid", "fallbacks": ["orauto", "loc14", "free1"]},
    ),
    (
        "primary-first-walled-uses-auto",
        {"strategy": "primary_first", "mods": {"paid1": {"availability": COOLDOWN}}},
        {"model": "orauto", "rung": "paid"},
    ),
    (
        "primary-first-local-scope",
        {"strategy": "primary_first", "scope": "local"},
        {"model": "loc14"},
    ),
    ("wall-local-exhausted", {"mods": {"loc14": {"availability": EXHAUSTED}}}, {"model": "free1"}),
    (
        "wall-free-cooldown-paid",
        {"drop": ["loc14"], "mods": {"free1": {"availability": COOLDOWN}}},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "wall-soft-still-usable",
        {"drop": ["loc14"], "mods": {"free1": {"availability": Availability("soft")}}},
        {"model": "free1", "rung": "cheap"},
    ),
    ("wall-paid-only-keeps-cheap", {"mods": WALL_PAID}, {"model": "loc14"}),
    (
        "wall-paid-best-effort",
        {"drop": NO_LOW, "mods": WALL_PAID},
        {
            "model": "free2",
            "rung": "best_effort",
            "below": True,
            "verify": True,
            "fallbacks": ["loc7"],
        },
    ),
    (
        "wall-paid-free-router-appended",
        {"drop": NO_LOW, "mods": WALL_PAID, "rules": FREE_ROUTER},
        {"model": "free2", "fallbacks": ["loc7", "orfree"]},
    ),
    (
        "wall-free-router-only",
        {"only": ROUTERS, "mods": WALL_PAID, "rules": FREE_ROUTER},
        {"model": "orfree", "rung": "free_router", "below": True, "verify": True},
    ),
    (
        "wall-free-router-complexity",
        {"only": ROUTERS, "mods": WALL_PAID, "rules": FREE_ROUTER, "complexity": 3},
        {"error": "no_candidates", "sugg": ["wait"]},
    ),
    (
        "wall-free-router-disabled",
        {"only": ROUTERS, "mods": WALL_PAID},
        {"error": "no_candidates", "sugg": ["wait"]},
    ),
    (
        "wall-all-cheap-and-paid1",
        {
            "mods": {
                k: {"availability": COOLDOWN} for k in ("loc14", "free1", "free2", "loc7", "paid1")
            }
        },
        {"model": "orauto", "rung": "paid"},
    ),
    (
        "wall-everything",
        {
            "mods": {
                k: {"availability": COOLDOWN}
                for k in ("loc14", "loc7", "free1", "free2", "paid1", "orauto", "orfree", "prem")
            }
        },
        {"error": "no_candidates", "sugg": ["wait"]},
    ),
    (
        "wall-no-eta-no-wait",
        {
            "only": ["paid1", "orauto"],
            "mods": {"paid1": {"availability": EXHAUSTED}, "orauto": {"availability": EXHAUSTED}},
        },
        {"error": "no_candidates", "etas": {}},
    ),
]


@pytest.mark.parametrize(("spec", "expect"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_golden_a(spec: dict[str, Any], expect: dict[str, Any]) -> None:
    check(spec, expect)
````

## tests/test_policy_golden_b.py

````python
"""HDW-114 golden table B: vision, agentic, scopes, private lock. Same pool as table A."""

from __future__ import annotations

from typing import Any

import pytest

from tests.policy_golden_support import check
from tests.test_policy_scoring import fit

Case = tuple[str, dict[str, Any], dict[str, Any]]
VISION = {"modalities": ["text", "image"]}
NO_FIT = fit("no_fit", "weights_too_large")


def img(mid: str) -> dict[str, Any]:
    return {mid: {"row": VISION}}


CASES: list[Case] = [
    ("vision-needs-image-model", {"vision": True, "mods": img("free1")}, {"model": "free1"}),
    ("vision-local-vision-model", {"vision": True, "mods": img("loc14")}, {"model": "loc14"}),
    (
        "vision-paid-fallback",
        {"vision": True, "mods": img("paid1")},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "vision-none-capable",
        {"vision": True},
        {"error": "no_candidates", "sugg": ["add_capable_model"]},
    ),
    (
        "vision-local-scope-none",
        {"vision": True, "scope": "local", "mods": img("paid1")},
        {"error": "no_candidates"},
    ),
    (
        "agentic-needs-tools-score",
        {"agentic": True, "complexity": 1},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "agentic-free-with-tools",
        {
            "agentic": True,
            "complexity": 1,
            "mods": {"free1": {"scores": {"code": 90.0, "agentic_tools": 90.0}}},
        },
        {"model": "free1", "rung": "cheap"},
    ),
    (
        "agentic-tools-flag-required",
        {
            "agentic": True,
            "tools": True,
            "complexity": 1,
            "mods": {"free1": {"row": {"tools_ok": False}}},
        },
        {"model": "paid1"},
    ),
    (
        "agentic-local-scope-errors",
        {"agentic": True, "scope": "local"},
        {"error": "local_below_floor", "sugg": ["split_task", "pull_model"]},
    ),
    # loc7 is below the floor, and local scope never falls back below it.
    ("scope-local-picks-local", {"scope": "local"}, {"model": "loc14", "fallbacks": []}),
    (
        "scope-local-too-hard-errors",
        {"scope": "local", "complexity": 5},
        {"error": "local_below_floor", "sugg": ["pull_model", "split_task", "use_cloud"]},
    ),
    (
        "scope-local-no-model-fits",
        {"scope": "local", "mods": {"loc14": {"fit": NO_FIT}, "loc7": {"fit": NO_FIT}}},
        {"error": "no_candidates", "sugg": ["pull_model", "use_cloud"]},
    ),
    (
        "scope-local-ctx-too-big",
        {"scope": "local", "min_ctx": 200_000},
        {"error": "no_candidates", "sugg": ["split_task"]},
    ),
    (
        "scope-free-skips-paid",
        {"scope": "free", "complexity": 5},
        {"model": "loc14", "rung": "best_effort", "below": True, "verify": True},
    ),
    ("scope-free-picks-local", {"scope": "free"}, {"model": "loc14"}),
    ("scope-cloud-skips-local", {"scope": "cloud"}, {"model": "free1", "rung": "cheap"}),
    (
        "scope-cloud-escalates-to-paid",
        {"scope": "cloud", "complexity": 5},
        {"model": "paid1", "rung": "paid"},
    ),
    ("scope-auto-paid-last", {"scope": "auto", "drop": ["loc14", "free1"]}, {"model": "paid1"}),
    ("private-lock-local-only", {"locks": ["private_lock"]}, {"model": "loc14", "sugg": []}),
    (
        "private-lock-never-cloud",
        {"locks": ["private_lock"], "complexity": 5},
        {"error": "local_below_floor"},
    ),
    (
        "private-lock-no-pin-escape",
        {"locks": ["private_lock"], "pin": "paid1"},
        {"error": "pin_rejected"},
    ),
    (
        "private-lock-battery-no-cloud-hint",
        {"locks": ["private_lock"], "battery": "prefer_cloud"},
        {"model": "loc14", "sugg": []},
    ),
]


@pytest.mark.parametrize(("spec", "expect"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_golden_b(spec: dict[str, Any], expect: dict[str, Any]) -> None:
    check(spec, expect)
````

## tests/test_policy_golden_c.py

````python
"""HDW-114 golden table C: GPU profiles, battery, GPU busy, unscored models, pin, premium."""

from __future__ import annotations

from typing import Any

import pytest

from tests.policy_golden_support import COOLDOWN, check
from tests.test_policy_golden_a import CASES as TABLE_A
from tests.test_policy_golden_b import CASES as TABLE_B
from tests.test_policy_scoring import fit

Case = tuple[str, dict[str, Any], dict[str, Any]]
MYSTERY = {"new:mystery": {"tier": "free_cloud", "scores": {}}}

CASES: list[Case] = [
    (
        "gpu-12gb-offload-still-ok",
        {"mods": {"loc14": {"fit": fit("offload", "kv_exceeds_budget")}}},
        {"model": "loc14"},
    ),
    (
        "gpu-12gb-big-model-excluded",
        {"mods": {"loc14": {"fit": fit("no_fit", "weights_too_large")}}},
        {"model": "free1"},
    ),
    (
        "gpu-16gb-both-fit",
        {"scope": "local", "mods": {"loc7": {"scores": {"code": 81.0}}}},
        {"model": "loc14", "fallbacks": ["loc7"]},
    ),
    (
        "gpu-small-model-cannot-beat-big",
        {"scope": "local", "mods": {"loc7": {"scores": {"code": 82.0}}}},
        {"model": "loc14"},
    ),
    (
        "battery-suggests-cloud",
        {"battery": "prefer_cloud", "drop": ["free1"]},
        {"model": "loc14", "sugg": ["use_cloud"]},
    ),
    (
        "battery-never-leaves-local",
        {"battery": "prefer_cloud", "scope": "local"},
        {"model": "loc14", "sugg": ["use_cloud"]},
    ),
    ("battery-normal-no-hint", {"battery": "normal"}, {"model": "loc14", "sugg": []}),
    (
        "battery-local-only-drops-cloud",
        {"battery": "local_only", "complexity": 5},
        {"error": "local_below_floor"},
    ),
    (
        "gpu-busy-suggests-cloud",
        {"busy": True, "drop": ["free1"]},
        {"model": "loc14", "sugg": ["use_cloud"]},
    ),
    (
        "battery-penalty-flips-to-free",
        {"battery": "prefer_cloud", "mods": {"free1": {"scores": {"code": 95.0}}}},
        {"model": "free1", "sugg": []},
    ),
    (
        "unscored-model-never-clears",
        {"only": ["new:mystery", "paid1"], "mods": MYSTERY},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "unscored-only-best-effort",
        {"only": ["new:mystery"], "mods": MYSTERY},
        {"model": "mystery", "rung": "best_effort", "below": True, "verify": True},
    ),
    ("pin-wins", {"pin": "free2"}, {"model": "free2", "rung": "pin"}),
    (
        "pin-below-floor-flagged",
        {"pin": "loc7"},
        {"model": "loc7", "rung": "pin", "below": True, "verify": True},
    ),
    (
        "pin-walled-errors",
        {"pin": "free2", "mods": {"free2": {"availability": COOLDOWN}}},
        {"error": "pin_rejected", "sugg": ["wait"]},
    ),
    ("pin-unknown-errors", {"pin": "nope"}, {"error": "pin_rejected"}),
    ("pin-beats-force", {"pin": "free1", "force": "premium"}, {"model": "free1", "rung": "pin"}),
    (
        "premium-never-automatic",
        {"complexity": 5, "drop": ["paid1", "orauto"]},
        {"model": "loc14", "below": True},
    ),
    ("premium-force", {"force": "premium"}, {"model": "prem", "rung": "force"}),
    (
        "premium-force-local-scope-denied",
        {"force": "premium", "scope": "local"},
        {"error": "premium_unavailable"},
    ),
]


@pytest.mark.parametrize(("spec", "expect"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_golden_c(spec: dict[str, Any], expect: dict[str, Any]) -> None:
    check(spec, expect)


def test_no_route_error_as_dict_is_structured() -> None:
    from tests.policy_golden_support import run

    err = run({"scope": "local", "complexity": 5})
    payload = err.as_dict()
    assert payload["code"] == "local_below_floor"
    assert {s["code"] for s in payload["suggestions"]} >= {"split_task", "pull_model"}
    assert isinstance(payload["rejected"], list)


def test_golden_table_has_at_least_40_scenarios() -> None:
    assert len(TABLE_A) + len(TABLE_B) + len(CASES) >= 40
````

## tests/test_policy_scoring.py

````python
"""HDW-113: hard filter, quality floor and score terms. Every weight comes from rules."""

from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.analyzer.requirements import HardRequirements, SoftRequirements
from headwaters.core.hardware.fit import FitResult
from headwaters.core.hardware.state import BatteryState, GpuBusy, HardwareState
from headwaters.core.policy.filters import Candidate, FilterResult, hard_filter
from headwaters.core.quotas import Availability
from headwaters.core.rules import Rules, ScoringRules
from headwaters.core.scope import ALL_TIERS, FREE_TIERS, LOCAL_TIERS, ScopeDecision, TierName
from headwaters.core.state_models import ModelRow

RULES = Rules()
_ROW_KEYS = ("modalities", "tools_ok", "json_ok", "ctx", "price_in", "price_out")


def soft(
    task: str = "code", complexity: int = 2, risk: str = "low", agentic: bool = False
) -> SoftRequirements:
    return SoftRequirements(task, complexity, risk, agentic, (), False, False)


def hard(
    vision: bool = False, tools: bool = False, json: bool = False, min_ctx: int = 1000
) -> HardRequirements:
    return HardRequirements(vision, tools, json, min_ctx)


def scope(tiers: frozenset[TierName] = ALL_TIERS) -> ScopeDecision:
    return ScopeDecision("auto", tiers, "cost_saver", "agentic")


def cand(
    mid: str = "m", tier: str = "free_cloud", scores: dict[str, float] | None = None, **kw: Any
) -> Candidate:
    row_kw: dict[str, Any] = {
        "modalities": ["text"],
        "tools_ok": True,
        "json_ok": True,
        "ctx": 100_000,
    }
    for key in _ROW_KEYS:
        if key in kw:
            row_kw[key] = kw.pop(key)
    row = ModelRow(id=mid, tier=tier, provider="p", **row_kw)
    return Candidate(row, scores if scores is not None else {"code": 80.0}, **kw)


def hw(policy: str = "normal", busy: bool = False) -> HardwareState:
    return HardwareState(
        gpu=None,
        profile="custom",
        reserve_gb=2.0,
        reserve_source="override",
        budget_gb=10.0,
        battery=BatteryState(True, policy != "normal"),
        battery_policy=policy,  # type: ignore[arg-type]
        gpu_busy=GpuBusy(busy, 0, 0),
        local_suggestion="ok",
    )


def fit(status: str = "fits", reason: str = "ok") -> FitResult:
    return FitResult("m", status, None if status == "no_fit" else 8192, reason)  # type: ignore[arg-type]


def local(mid: str = "loc", **kw: Any) -> Candidate:
    kw.setdefault("fit", fit())
    return cand(mid, "local", **kw)


def why(res: FilterResult, mid: str) -> str:
    return next(r.reason for r in res.rejected if r.model_id == mid)


# ---- hard filter -------------------------------------------------------------------------


def test_filter_keeps_a_model_meeting_everything() -> None:
    res = hard_filter([cand("a")], hard(), scope(), RULES)
    assert [c.id for c in res.kept] == ["a"]
    assert not res.rejected


@pytest.mark.parametrize(
    ("need", "kw", "code"),
    [
        (hard(vision=True), {}, "no_vision"),
        (hard(tools=True), {"tools_ok": False}, "no_tools"),
        (hard(tools=True), {"tools_ok": None}, "no_tools"),
        (hard(json=True), {"json_ok": False}, "no_json"),
    ],
)
def test_filter_requirements(need: HardRequirements, kw: dict[str, Any], code: str) -> None:
    assert why(hard_filter([cand("a", **kw)], need, scope(), RULES), "a") == code


def test_filter_vision_accepts_image_modality() -> None:
    c = cand("a", modalities=["text", "image"])
    assert hard_filter([c], hard(vision=True), scope(), RULES).kept


def test_filter_context_needs_headroom() -> None:
    need = hard(min_ctx=10_000)  # needs 12_000 at the default 1.2x
    assert why(hard_filter([cand("a", ctx=11_999)], need, scope(), RULES), "a") == "ctx_too_small"
    assert hard_filter([cand("a", ctx=12_000)], need, scope(), RULES).kept
    assert why(hard_filter([cand("a", ctx=None)], need, scope(), RULES), "a") == "ctx_too_small"


def test_filter_headroom_comes_from_rules() -> None:
    rules = Rules(scoring=ScoringRules(ctx_headroom=2.0))
    res = hard_filter([cand("a", ctx=12_000)], hard(min_ctx=10_000), scope(), rules)
    assert why(res, "a") == "ctx_too_small"


def test_filter_availability() -> None:
    cooling = cand("w", availability=Availability("cooldown", 30.0))
    spent = cand("x", availability=Availability("exhausted"))
    soft_ = cand("s", availability=Availability("soft"))
    res = hard_filter([cooling, spent, soft_], hard(), scope(), RULES)
    assert [c.id for c in res.kept] == ["s"]
    assert why(res, "w") == "unavailable_cooldown"
    assert why(res, "x") == "unavailable_exhausted"


def test_filter_scope_tiers() -> None:
    paid, free, loc = cand("p", "paid"), cand("f", "free_cloud"), local("l")
    res = hard_filter([paid, free, loc], hard(), scope(FREE_TIERS), RULES)
    assert {c.id for c in res.kept} == {"f", "l"}
    assert why(res, "p") == "scope"
    res = hard_filter([paid, free, loc], hard(), scope(LOCAL_TIERS), RULES)
    assert [c.id for c in res.kept] == ["l"]


def test_filter_openrouter_auto_tier_uses_scope_tier() -> None:
    auto = cand("or/auto", "paid", scope_tier="openrouter_auto")
    assert hard_filter([auto], hard(), scope(frozenset({"paid"})), RULES).rejected
    assert hard_filter([auto], hard(), scope(frozenset({"openrouter_auto"})), RULES).kept


def test_filter_hardware_fit() -> None:
    no_data = cand("n", "local")
    too_big = local("b", fit=fit("no_fit", "weights_too_large"))
    offload = local("o", fit=fit("offload", "kv_exceeds_budget"))
    res = hard_filter([no_data, too_big, offload], hard(), scope(), RULES)
    assert [c.id for c in res.kept] == ["o"]
    assert why(res, "n") == "no_fit_data"
    assert why(res, "b") == "no_fit_weights_too_large"
````

## tests/test_policy_strategies.py

````python
"""HDW-113: strategies (cost_saver, quality_first, primary_first), breakdown and ranking."""

from __future__ import annotations

import pytest

from headwaters.core.hardware.residency import ResidencyInputs
from headwaters.core.policy.scoring import rank_candidates
from headwaters.core.rules import Rules, StrategyWeights
from tests.test_policy_scoring import RULES, cand, hw, local, soft
from tests.test_policy_terms import total


def test_strategy_cost_weight_differs() -> None:
    c = cand("p", "paid", scores={"code": 95.0}, price_in=3.0, price_out=15.0)
    saver = total(c, strategy="cost_saver", tokens=10_000)
    quality = total(c, strategy="quality_first", tokens=10_000)
    primary = total(c, strategy="primary_first", tokens=10_000)
    assert saver.cost_penalty == pytest.approx(400 * 0.045)
    assert quality.cost_penalty == pytest.approx(40 * 0.045)
    assert primary.cost_penalty == 0.0
    assert saver.total < quality.total < primary.total


def test_strategy_primary_first_bonus_only_for_primary() -> None:
    assert total(cand(is_primary=True), strategy="primary_first").primary_bonus == 30.0
    assert total(cand(is_primary=False), strategy="primary_first").primary_bonus == 0.0
    assert total(cand(is_primary=True), strategy="cost_saver").primary_bonus == 0.0


def test_strategy_quality_first_halves_latency_and_residency() -> None:
    state = hw("prefer_cloud")
    c = local(residency=ResidencyInputs(False, 0.0, ("x",), 0.25))
    assert total(c, state=state, strategy="cost_saver").latency_penalty == 20.0
    assert total(c, state=state, strategy="quality_first").latency_penalty == 10.0
    assert total(c, strategy="cost_saver").evict_penalty == 25.0
    assert total(c, strategy="quality_first").evict_penalty == 12.5
    warm = local(residency=ResidencyInputs(True, 0.15, (), 0.0))
    assert total(warm, strategy="quality_first").warm_bonus == 7.5


def test_strategy_weights_come_from_rules() -> None:
    rules = Rules.model_validate(
        {"scoring": {"strategies": {"cost_saver": {"cost_weight": 1, "primary_bonus": 5}}}}
    )
    c = cand("p", "paid", price_in=1_000_000.0, price_out=0.0, is_primary=True)
    b = total(c, strategy="cost_saver", rules=rules, tokens=1)
    assert b.cost_penalty == 1.0
    assert b.primary_bonus == 5.0
    assert isinstance(rules.scoring.strategies.quality_first, StrategyWeights)


def test_breakdown_reports_floor_and_gap() -> None:
    ok = total(cand(scores={"code": 90.0}), soft("code", 2))  # floor 80
    assert (ok.floor, ok.meets_floor, ok.below_floor_by) == (80, True, 0.0)
    low = total(cand(scores={"code": 75.0}), soft("code", 2))
    assert (low.meets_floor, low.below_floor_by) == (False, 5.0)
    agentic = total(cand(scores={"code": 95.0, "agentic_tools": 60.0}), soft(agentic=True))
    assert agentic.meets_floor is False
    assert agentic.below_floor_by == 35.0  # floor 95


def test_rank_orders_by_total_then_local_then_id() -> None:
    a = cand("a", scores={"code": 80.0})
    b = cand("b", scores={"code": 90.0})
    loc = local("z-local", scores={"code": 80.0})
    s = soft(complexity=1)
    ranked = rank_candidates([a, b, loc], s, 100, "cost_saver", RULES)
    assert [c.id for c, _ in ranked] == ["b", "z-local", "a"]  # tie at 80: local first
    ranked = rank_candidates([cand("y"), cand("x")], s, 100, "cost_saver", RULES)
    assert [c.id for c, _ in ranked] == ["x", "y"]


def test_rules_schema_has_scoring() -> None:
    assert "scoring" in Rules.model_json_schema()["properties"]
````

## tests/test_policy_terms.py

````python
"""HDW-113: every score term (quality, complexity, warm, evict, latency, battery, GPU, cost)."""

from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.analyzer.requirements import SoftRequirements
from headwaters.core.hardware.residency import ResidencyInputs
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.scoring import ScoreBreakdown, estimate_cost_usd, score_candidate
from headwaters.core.quotas import Availability
from headwaters.core.rules import Rules, ScoringRules
from tests.test_policy_scoring import RULES, cand, hw, local, soft


def total(
    c: Candidate,
    s: SoftRequirements | None = None,
    strategy: Any = "cost_saver",
    rules: Rules = RULES,
    state: Any = None,
    tokens: int = 1000,
) -> ScoreBreakdown:
    return score_candidate(c, s or soft(complexity=1), tokens, strategy, rules, state)


def test_score_quality_only() -> None:
    b = total(cand(scores={"code": 77.0}))
    assert b.total == 77.0
    assert b.quality == 77.0
    assert (b.warm_bonus, b.latency_penalty, b.evict_penalty, b.cost_penalty) == (0, 0, 0, 0)


def test_score_missing_task_score_is_zero() -> None:
    assert total(cand(scores={}), soft("code")).quality == 0.0


def test_score_complexity_penalty() -> None:
    b = total(cand(scores={"code": 80.0}), soft(complexity=4))
    assert b.complexity_penalty == 9.0  # 3.0 * (4 - 1)
    assert b.total == 71.0
    rules = Rules(scoring=ScoringRules(complexity_penalty_per_level=1.0))
    assert total(cand(scores={"code": 80.0}), soft(complexity=4), rules=rules).total == 77.0


def test_score_warm_bonus_and_evict_penalty() -> None:
    warm = local(residency=ResidencyInputs(True, 0.15, (), 0.0))
    cold = local(residency=ResidencyInputs(False, 0.0, ("x", "y"), 0.5))
    assert total(warm).warm_bonus == 15.0
    assert total(warm).total == 95.0
    assert total(cold).evict_penalty == 50.0
    assert total(cold).total == 30.0


def test_score_residency_scale_from_rules() -> None:
    rules = Rules(scoring=ScoringRules(residency_scale=10.0))
    warm = local(residency=ResidencyInputs(True, 0.15, (), 0.0))
    assert total(warm, rules=rules).warm_bonus == pytest.approx(1.5)


def test_score_latency_penalty_from_expected_tps() -> None:
    assert total(cand(expected_tps=60.0)).latency_penalty == 0.0  # faster than reference
    assert total(cand(expected_tps=30.0)).latency_penalty == 0.0
    assert total(cand(expected_tps=15.0)).latency_penalty == 5.0  # weight 5 * (30/15 - 1)
    assert total(cand(expected_tps=0.1)).latency_penalty == 20.0  # capped
    assert total(cand(expected_tps=None)).latency_penalty == 0.0


def test_score_battery_penalty_local_only_and_per_rule() -> None:
    state = hw("prefer_cloud")
    assert total(local(), state=state).latency_penalty == 20.0
    assert total(cand(), state=state).latency_penalty == 0.0  # cloud unaffected
    assert total(local(), state=hw("normal")).latency_penalty == 0.0  # AC or on_battery=normal
    assert total(local(), state=hw("local_only")).latency_penalty == 0.0
    rules = Rules(scoring=ScoringRules(on_battery_local_penalty=7.0))
    assert total(local(), state=state, rules=rules).latency_penalty == 7.0


def test_score_gpu_busy_penalty_local_only() -> None:
    assert total(local(), state=hw(busy=True)).latency_penalty == 15.0
    assert total(cand(), state=hw(busy=True)).latency_penalty == 0.0
    assert total(local(), state=hw("prefer_cloud", busy=True)).latency_penalty == 35.0


def test_score_cost_term() -> None:
    c = cand("paid1", "paid", price_in=3.0, price_out=15.0)
    assert estimate_cost_usd(c, 100_000, RULES) == pytest.approx(0.3 + 0.015)
    b = total(c, tokens=100_000)
    assert b.cost_penalty == pytest.approx(400 * 0.315)
    assert b.total < 0
    assert estimate_cost_usd(cand(), 100_000, RULES) == 0.0  # free / unpriced


def test_score_soft_quota_penalty() -> None:
    assert total(cand(availability=Availability("soft"))).soft_penalty == 10.0
    assert total(cand(availability=Availability("ok"))).soft_penalty == 0.0
````

## tests/test_quotas.py

````python
"""HDW-111: quota tracker and cooldowns, with a frozen clock for every transition."""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from freezegun import freeze_time
from freezegun.api import FrozenDateTimeFactory

from headwaters.core.catalog.http import SourceError
from headwaters.core.catalog.schema import FreeLimit
from headwaters.core.quotas import (
    KeyStatus,
    QuotaTracker,
    key_subject,
    model_subject,
    parse_key_status,
    provider_subject,
)
from headwaters.core.rules import WallsRules
from headwaters.core.state import Database, open_database
from headwaters.core.state_limits import active_walls
from headwaters.core.walls import WallEvent

START = "2027-01-15 12:00:00"
MODEL = "m/free:1"
PROVIDER = "openrouter"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state.db")


@pytest.fixture
def clock() -> Iterator[FrozenDateTimeFactory]:
    with freeze_time(START) as frozen:
        assert isinstance(frozen, FrozenDateTimeFactory)
        yield frozen


def tracker(db: Database, limits: list[FreeLimit] | None = None, **walls: Any) -> QuotaTracker:
    return QuotaTracker(db, WallsRules(**walls), limits or [], rng=random.Random(7))


def rate(retry_after: float | None = None, scope: str = "model") -> WallEvent:
    return WallEvent(kind="rate_limit", scope=scope, retry_after=retry_after)  # type: ignore[arg-type]


def exhausted(reset_at: float | None = None, scope: str = "key") -> WallEvent:
    return WallEvent(kind="exhausted", scope=scope, reset_at=reset_at)  # type: ignore[arg-type]


def overloaded() -> WallEvent:
    return WallEvent(kind="overloaded", scope="model")


def test_defaults_are_ok(db: Database, clock: FrozenDateTimeFactory) -> None:
    assert tracker(db).availability(MODEL, PROVIDER).status == "ok"


def test_non_wall_events_change_nothing(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    for kind in ("context", "other"):
        event = WallEvent(kind=kind, scope="model")  # type: ignore[arg-type]
        assert t.record_wall(event, model=MODEL, provider=PROVIDER).action == "none"
    assert active_walls(db) == []


def test_rate_limit_cooldown_then_ok_after_reset(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    decision = t.record_wall(rate(5.0), model=MODEL, provider=PROVIDER)
    assert (decision.action, decision.state) == ("retry", "cooldown")
    assert decision.wait_s >= 5.0
    status = t.availability(MODEL, PROVIDER)
    assert status.status == "cooldown"
    assert status.eta is not None
    assert 4.9 <= status.eta <= decision.wait_s
    clock.tick(decision.wait_s + 1)
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_long_wait_cascades_immediately(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, cascade_if_wait_over_s=8)
    decision = t.record_wall(rate(60.0), model=MODEL, provider=PROVIDER)
    assert decision.action == "cascade"
    assert t.availability(MODEL, PROVIDER).status == "cooldown"


def test_wait_exactly_at_threshold_still_retries(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, cascade_if_wait_over_s=8, backoff_base_s=0.001)
    assert t.record_wall(rate(8.0), model=MODEL, provider=PROVIDER).action == "retry"


def test_repeated_429s_back_off_exponentially_with_full_jitter(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, backoff_base_s=2, backoff_max_s=1000, cascade_if_wait_over_s=10_000)
    waits = []
    for _ in range(6):
        decision = t.record_wall(rate(None), model=MODEL, provider=PROVIDER)
        waits.append((decision.consecutive, decision.wait_s))
        clock.tick(decision.wait_s + 0.01)
    for attempt, wait in waits:
        assert 0.0 <= wait <= 2 * 2 ** (attempt - 1)
    assert [a for a, _ in waits] == [1, 2, 3, 4, 5, 6]
    assert len({round(w, 6) for _, w in waits}) > 1  # jittered, not constant


def test_backoff_is_capped(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, backoff_base_s=2, backoff_max_s=5, cascade_if_wait_over_s=10_000)
    for _ in range(12):
        decision = t.record_wall(rate(None), model=MODEL, provider=PROVIDER)
        assert decision.wait_s <= 5
        clock.tick(decision.wait_s + 0.01)


def test_provider_hint_is_a_floor_for_the_wait(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, backoff_base_s=0.001, cascade_if_wait_over_s=100)
    assert t.record_wall(rate(30.0), model=MODEL, provider=PROVIDER).wait_s >= 30.0


def test_success_clears_cooldown_and_resets_streak(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, cascade_if_wait_over_s=10_000)
    t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER)
    t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER)
    t.record_success(model=MODEL, provider=PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "ok"
    assert t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER).consecutive == 1


def test_old_streak_is_forgotten(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, cascade_if_wait_over_s=10_000)
    t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER)
    t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER)
    clock.tick(2 * 3600)
    assert t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER).consecutive == 1


def test_scope_provider_cools_every_model_of_it(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    t.record_wall(rate(5.0, scope="provider"), model=MODEL, provider=PROVIDER)
    assert t.availability("other/model", PROVIDER).status == "cooldown"
    assert t.availability("other/model", "openai").status == "ok"


def test_scope_model_leaves_other_models_alone(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    t.record_wall(rate(5.0), model=MODEL, provider=PROVIDER)
    assert t.availability("other/model", PROVIDER).status == "ok"


def test_scope_key_uses_alias_and_falls_back_to_provider(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(rate(5.0, scope="key"), model=MODEL, provider=PROVIDER, key="work")
    assert t.availability("x", PROVIDER, key="work").status == "cooldown"
    assert t.availability("x", PROVIDER, key="home").status == "ok"
    t.record_wall(rate(5.0, scope="key"), model=MODEL, provider="openai")
    assert t.availability("x", "openai").status == "cooldown"


def test_exhausted_never_expires_without_reset_or_check(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    decision = t.record_wall(exhausted(), model=MODEL, provider=PROVIDER, key="work")
    assert (decision.action, decision.state) == ("cascade", "exhausted")
    clock.tick(30 * 24 * 3600)
    status = t.availability(MODEL, PROVIDER, key="work")
    assert (status.status, status.eta) == ("exhausted", None)
    t.record_success(model=MODEL, provider=PROVIDER, key="work")  # success does not lift it
    assert t.availability(MODEL, PROVIDER, key="work").status == "exhausted"


def test_exhausted_lifts_at_reset_time(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    reset = time.time() + 3600
    t.record_wall(exhausted(reset), model=MODEL, provider=PROVIDER, key="work")
    status = t.availability(MODEL, PROVIDER, key="work")
    assert status.status == "exhausted"
    assert status.eta is not None
    assert 3599 <= status.eta <= 3600
    clock.tick(3601)
    assert t.availability(MODEL, PROVIDER, key="work").status == "ok"


def test_exhausted_beats_cooldown(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    t.record_wall(rate(5.0), model=MODEL, provider=PROVIDER)
    t.record_wall(exhausted(), model=MODEL, provider=PROVIDER, key="work")
    assert t.availability(MODEL, PROVIDER, key="work").status == "exhausted"


def test_overloaded_gets_short_retries_then_cascades(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, overloaded_max_retries=2, overloaded_backoff_s=1, overloaded_cooldown_s=30)
    first = t.record_wall(overloaded(), model=MODEL, provider=PROVIDER)
    second = t.record_wall(overloaded(), model=MODEL, provider=PROVIDER)
    third = t.record_wall(overloaded(), model=MODEL, provider=PROVIDER)
    assert (first.action, second.action, third.action) == ("retry", "retry", "cascade")
    assert first.wait_s <= 1
    assert second.wait_s <= 2
    assert third.wait_s == 30
    assert t.availability(MODEL, PROVIDER).status == "cooldown"
    clock.tick(31)
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_overloaded_zero_retries_cascades_at_once(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, overloaded_max_retries=0)
    assert t.record_wall(overloaded(), model=MODEL, provider=PROVIDER).action == "cascade"


LIMIT = FreeLimit(provider=PROVIDER, model=None, rpm=10, rpd=50)


def test_counters_soft_exhaust_at_percentage(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, [LIMIT], soft_exhaust_pct=90)
    for _ in range(8):
        t.count_request(MODEL, PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "ok"
    t.count_request(MODEL, PROVIDER)  # 9 of 10 rpm = 90%
    assert t.availability(MODEL, PROVIDER).status == "soft"
    assert t.availability(MODEL, PROVIDER).usable
    assert t.availability("other/free:2", PROVIDER).status == "ok"  # counters are per model


def test_rpm_window_rolls_over_each_minute(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, [LIMIT])
    for _ in range(9):
        t.count_request(MODEL, PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "soft"
    clock.tick(60)
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_rpd_window_rolls_over_at_utc_midnight(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, [FreeLimit(provider=PROVIDER, rpd=10)], soft_exhaust_pct=50)
    for _ in range(5):
        t.count_request(MODEL, PROVIDER)
        clock.tick(61)  # spread over minutes so rpm never matters
    assert t.availability(MODEL, PROVIDER).status == "soft"
    clock.move_to("2027-01-16 00:00:01")
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_model_specific_limit_beats_provider_wide(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    specific = FreeLimit(provider=PROVIDER, model=MODEL, rpm=2)
    t = tracker(db, [LIMIT, specific], soft_exhaust_pct=100)
    t.count_request(MODEL, PROVIDER)
    t.count_request(MODEL, PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "soft"


def test_no_limit_means_no_counting(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    for _ in range(100):
        t.count_request(MODEL, PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_state_persists_across_tracker_instances(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    tracker(db).record_wall(rate(5.0), model=MODEL, provider=PROVIDER)
    assert tracker(db).availability(MODEL, PROVIDER).status == "cooldown"


def test_subject_names_are_namespaced() -> None:
    assert model_subject("a") == "model:a"
    assert provider_subject("OpenRouter") == "provider:openrouter"
    assert key_subject("work") == "key:work"


# --- key polling ------------------------------------------------------------------------------


class FakeHttp:
    def __init__(self, payload: Any = None, error: bool = False) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, url, headers))
        if self.error:
            raise SourceError("down")
        return self.payload


def key_payload(remaining: float | None, budget_left: float | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"label": "k", "limit_remaining": remaining, "usage": 3.5}
    if budget_left is not None:
        data["effective_budget"] = {"remaining_usd": budget_left}
    return {"data": data}


def test_parse_key_status() -> None:
    assert parse_key_status(key_payload(2.0)) == KeyStatus(False, 2.0, 3.5)
    assert parse_key_status(key_payload(0.0)).exhausted
    assert parse_key_status(key_payload(None)).exhausted is False  # unlimited key
    assert parse_key_status(key_payload(5.0, budget_left=0.0)).exhausted
    assert parse_key_status(key_payload(5.0, budget_left=1.0)).exhausted is False
    with pytest.raises(SourceError):
        parse_key_status({"nope": 1})


def test_poll_marks_exhausted_then_lifts_when_balance_returns(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, key_poll_minutes=10)
    http = FakeHttp(key_payload(0.0))
    status = t.poll_key(http, "sk-secret", alias="work")
    assert status is not None
    assert status.exhausted
    assert t.availability(MODEL, PROVIDER, key="work").status == "exhausted"
    assert http.calls[0][2] == {"Authorization": "Bearer sk-secret"}
    http.payload = key_payload(12.0)
    assert t.poll_key(http, "sk-secret", alias="work") is None  # not due yet
    clock.tick(10 * 60)
    status = t.poll_key(http, "sk-secret", alias="work")
    assert status is not None
    assert not status.exhausted
    assert t.availability(MODEL, PROVIDER, key="work").status == "ok"


def test_poll_every_n_minutes_and_force_after_paid_call(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, key_poll_minutes=5)
    http = FakeHttp(key_payload(9.0))
    assert t.poll_key(http, "k", alias="a") is not None
    clock.tick(299)
    assert t.poll_key(http, "k", alias="a") is None
    assert t.poll_key(http, "k", alias="a", force=True) is not None
    clock.tick(300)
    assert t.poll_key(http, "k", alias="a") is not None
    assert len(http.calls) == 3


@pytest.mark.parametrize("flag", ["airplane", "local_lock"])
def test_poll_skipped_in_airplane_mode_or_local_lock(
    db: Database, clock: FrozenDateTimeFactory, flag: str
) -> None:
    http = FakeHttp(key_payload(0.0))
    result = tracker(db).poll_key(
        http,
        "k",
        alias="a",
        force=True,
        airplane=flag == "airplane",
        local_lock=flag == "local_lock",
    )
    assert result is None
    assert http.calls == []
    assert active_walls(db) == []


def test_failed_poll_changes_nothing_and_retries_next_time(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(exhausted(), model=MODEL, provider=PROVIDER, key="work")
    assert t.poll_key(FakeHttp(error=True), "k", alias="work") is None
    assert t.availability(MODEL, PROVIDER, key="work").status == "exhausted"
    assert t.poll_key(FakeHttp(key_payload(5.0)), "k", alias="work") is not None  # still due
    assert t.availability(MODEL, PROVIDER, key="work").status == "ok"
````

## tests/test_router.py

````python
"""HDW-117: router façade cascades and scope, end to end with mocked providers."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.executor.errors import ProviderError
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Candidate
from headwaters.core.router import RouterConfig
from headwaters.core.router_types import RouteFailed
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeError
from headwaters.core.state_decisions import recent_decisions
from tests.router_rig import Rig
from tests.router_support import NO_CREDIT, OK, RATE_LIMITED, candidate, registry_with

FREE = "openrouter/free-model:free"
LOCAL = "qwen-local"
PAID = "anthropic/paid-model"


def pool(free: float = 90.0, local: float = 86.0, paid: float = 96.0) -> list[Candidate]:
    return [
        candidate(FREE, "free_cloud", free),
        candidate(LOCAL, "local", local),
        candidate(PAID, "paid", paid, price_in=3.0, price_out=15.0, is_primary=True),
    ]


def test_free_429_cascades_to_local(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    result = rig.run()
    assert result.decision.model == LOCAL
    assert result.first_choice == FREE
    assert [a.status for a in result.attempts] == ["rate_limit", "ok"]
    assert rig.dispatch.calls == [FREE, LOCAL]


def test_free_429_leaves_a_cooldown_for_the_next_request(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    rig.run()
    again = rig.run(messages=[{"role": "user", "content": "another question entirely"}])
    assert again.decision.model == LOCAL  # the free model is cooling down, not retried
    assert rig.dispatch.calls == [FREE, LOCAL, LOCAL]


def test_paid_402_cascades_to_free(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {PAID: [NO_CREDIT], FREE: OK},
        pool(),
        registry=registry_with(paid=True),
        rules=Rules.model_validate({"strategy": "primary_first"}),
    )
    result = rig.run()
    assert result.decision.model == FREE
    assert result.first_choice == PAID
    assert [a.status for a in result.attempts] == ["exhausted", "ok"]


def test_local_scope_never_touches_the_cloud(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {LOCAL: OK}, pool())
    result = rig.run(scope="local")
    assert result.decision.model == LOCAL
    assert rig.cloud_clients == 0
    assert rig.dispatch.calls == [LOCAL]


def test_local_scope_failure_is_structured_not_a_cloud_fallback(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {LOCAL: [ProviderError(500, {}, "", kind="http")]}, pool())
    with pytest.raises(RouteFailed) as info:
        rig.run(scope="local")
    assert info.value.code == "no_route_left"
    assert info.value.as_dict()["attempts"] == [
        {"model": LOCAL, "tier": "local", "status": "provider_error"}
    ]
    assert rig.cloud_clients == 0
    assert rig.dispatch.calls == [LOCAL]


def test_private_lock_keeps_an_auto_request_local(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), rules=Rules(private_lock=True))
    assert rig.run().decision.model == LOCAL
    assert rig.cloud_clients == 0


def test_airplane_mode_removes_every_cloud_candidate(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), rules=Rules(airplane=True))
    assert rig.run().decision.model == LOCAL
    assert rig.cloud_clients == 0


def test_scope_error_is_raised_and_logged(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, pool(), rules=Rules(private_lock=True))
    with pytest.raises(ScopeError) as info:
        rig.run(scope="cloud")
    assert info.value.code == "private_lock_conflict"
    assert [r.chosen for r in recent_decisions(rig.db)] == [None]
    assert rig.dispatch.calls == []


def test_no_route_is_raised_as_is(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, pool())
    with pytest.raises(NoRouteError) as info:
        rig.run(pin="no-such-model")
    assert info.value.code == "pin_rejected"
    assert rig.dispatch.calls == []
    assert [r.chosen for r in recent_decisions(rig.db)] == [None]


def test_attempts_are_bounded(tmp_path: Path) -> None:
    models = [candidate(f"m{i}:free", "free_cloud", 90.0 - i) for i in range(5)]
    scripts = {m.id: [RATE_LIMITED] for m in models}
    rig = Rig(tmp_path, scripts, models, config=RouterConfig(max_attempts=2))
    with pytest.raises(RouteFailed) as info:
        rig.run()
    assert info.value.code == "attempts_exhausted"
    assert len(rig.dispatch.calls) == 2
````

## tests/test_router_plan.py

````python
"""HDW-118: ``Router.plan`` is a dry run of steps 1-3 with no model call and no stored state."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.policy.errors import NoRouteError
from headwaters.core.router_plan import RoutePlan
from headwaters.core.router_types import RouteRequest
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeError
from headwaters.core.state_decisions import recent_decisions
from tests.router_rig import Rig
from tests.router_support import MESSAGES, OK, candidate


def rig(tmp_path: Path, rules: Rules | None = None) -> Rig:
    return Rig(
        tmp_path,
        {"free/a": OK, "local/b": OK},
        [candidate("free/a", "free_cloud", 90.0), candidate("local/b", "local", 85.0)],
        rules=rules,
    )


def plan(r: Rig, **kw: Any) -> RoutePlan:
    return asyncio.run(r.router.plan(RouteRequest(messages=MESSAGES, **kw)))


def test_plan_picks_without_calling_any_model(tmp_path: Path) -> None:
    r = rig(tmp_path)
    result = plan(r)
    assert result.decision.model == "free/a"
    assert result.decision.fallbacks == ("local/b",)
    assert r.dispatch.calls == []  # nothing was sent anywhere
    assert r.cloud_clients == 0  # and no cloud client was built


def test_plan_stores_nothing(tmp_path: Path) -> None:
    r = rig(tmp_path)
    plan(r)
    assert recent_decisions(r.db) == []


def test_plan_matches_what_run_would_pick(tmp_path: Path) -> None:
    r = rig(tmp_path)
    planned = plan(r)
    ran = r.run()
    assert planned.decision.model == ran.decision.model
    assert planned.decision.fallbacks == ran.decision.fallbacks


def test_plan_lists_what_the_filter_rejected(tmp_path: Path) -> None:
    result = plan(rig(tmp_path), scope="local")
    assert result.decision.model == "local/b"
    assert [(x.model_id, x.reason) for x in result.rejected] == [("free/a", "scope")]
    assert result.scope.tiers == frozenset({"local"})
    assert result.candidate("free/a") is not None
    assert result.candidate("nope") is None


def test_plan_raises_no_route_when_nothing_is_known(tmp_path: Path) -> None:
    r = Rig(tmp_path, {}, [])
    with pytest.raises(NoRouteError):
        plan(r)
    assert recent_decisions(r.db) == []


def test_plan_raises_scope_errors_and_does_not_log_them(tmp_path: Path) -> None:
    r = rig(tmp_path, Rules(private_lock=True))
    with pytest.raises(ScopeError) as caught:
        plan(r, scope="cloud")
    assert caught.value.code == "private_lock_conflict"
    assert recent_decisions(r.db) == []  # only ``handle`` logs early failures
    with pytest.raises(ScopeError):
        asyncio.run(r.router.run(RouteRequest(messages=MESSAGES, scope="cloud")))
    assert len(recent_decisions(r.db)) == 1  # the real path still logs, as before
````

## tests/test_router_session.py

````python
"""HDW-117: session keying and warm preference, progress hook, secret detection."""

from __future__ import annotations

from pathlib import Path

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.router_session import session_key
from headwaters.core.router_types import ProgressEvent
from headwaters.core.secrets_detect import SecretDetector
from headwaters.core.state_misc import get_session
from tests.executor_support import REPO_ROOT
from tests.router_rig import Rig
from tests.router_support import OK, RATE_LIMITED, candidate, stages
from tests.test_router import FREE, LOCAL, pool

SK = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"


def test_session_key_prefers_the_header_then_hashes() -> None:
    msgs = [{"role": "user", "content": "first message"}]
    assert session_key(messages=msgs, headers={"X-Session-Id": "abc"}) == "id:abc"
    assert session_key(messages=msgs, explicit="zzz") == "id:zzz"
    hashed = session_key(messages=msgs, system="be brief")
    later = [*msgs, {"role": "assistant", "content": "x"}]
    assert hashed == session_key(messages=later, system="be brief")  # only the first turn counts
    assert hashed != session_key(messages=msgs, system="be long")
    assert hashed.startswith("h:")
    assert "first message" not in hashed


def test_session_key_handles_openai_system_messages_and_content_parts() -> None:
    plain = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    parts = [
        {"role": "system", "content": [{"type": "text", "text": "sys"}]},
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    ]
    anthropic = [{"role": "user", "content": "hi"}]
    assert session_key(messages=plain) == session_key(messages=parts)
    assert session_key(messages=plain) == session_key(messages=anthropic, system="sys")


def test_the_answering_model_is_remembered_and_preferred_warm(tmp_path: Path) -> None:
    models = [candidate("loc-a", "local", 86.0), candidate("loc-b", "local", 86.0)]
    rig = Rig(tmp_path, {"loc-a": OK, "loc-b": OK}, models)
    assert rig.run(session_id="s1").decision.model == "loc-a"  # a tie ranks by id
    session = get_session(rig.db, "id:s1")
    assert session is not None
    assert session.pinned_model == "loc-a"
    # A fresh session has no preference; the remembered one earns the warm bonus.
    assert rig.run(session_id="s1").decision.breakdown.warm_bonus > 0
    assert rig.run(session_id="other").decision.breakdown.warm_bonus == 0


def test_progress_callback_reports_each_stage_without_request_text(tmp_path: Path) -> None:
    seen: list[ProgressEvent] = []
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    rig.run(on_progress=seen.append)
    assert stages(seen) == [
        "analyzing",
        "scope",
        "deciding",
        "executing",
        "cascading",
        "executing",
        "done",
    ]
    assert seen[-1].model == LOCAL
    assert "write a function" not in repr(seen)


def test_a_failing_progress_callback_does_not_break_routing(tmp_path: Path) -> None:
    def boom(event: ProgressEvent) -> None:
        raise RuntimeError("callback bug")

    rig = Rig(tmp_path, {FREE: OK}, pool())
    assert rig.run(on_progress=boom).decision.model == FREE


def test_a_secret_in_the_prompt_forces_local(tmp_path: Path) -> None:
    detector = SecretDetector.from_catalog(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH))
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), secrets=detector)
    result = rig.run(messages=[{"role": "user", "content": f"my key is {SK}, debug this"}])
    assert result.decision.model == LOCAL
    assert rig.cloud_clients == 0
    assert SK not in repr(result.reasons)
````

## tests/test_router_stream.py

````python
"""HDW-117: streaming failover and the handle() entry point."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import Delta
from headwaters.core.router_types import RouteEvent, RouteRequest
from tests.executor_support import Step
from tests.router_rig import Rig
from tests.router_support import OK, RATE_LIMITED
from tests.test_router import FREE, LOCAL, pool


def kinds(events: list[RouteEvent]) -> list[str]:
    return [e.kind for e in events]


def test_stream_fails_over_before_the_first_token(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    events = rig.stream()
    assert rig.dispatch.calls == [FREE, LOCAL]
    assert kinds(events).count("route") == 1
    route = next(e for e in events if e.kind == "route")
    assert route.decision is not None
    assert route.decision.model == LOCAL
    final = events[-1]
    assert final.kind == "final"
    assert final.result is not None
    assert final.result.result.content == "ok answer"
    assert [a.status for a in final.result.attempts] == ["rate_limit", "ok"]


def test_stream_never_switches_models_after_the_first_token(tmp_path: Path) -> None:
    broken: list[Step] = [Delta(content="par"), ProviderError(500, {}, "", kind="stream_error")]
    rig = Rig(tmp_path, {FREE: broken, LOCAL: OK}, pool(local=84.0))
    events = rig.stream()
    assert rig.dispatch.calls == [FREE]
    final = events[-1]
    assert final.kind == "final"
    assert final.error is not None
    assert final.result is None
    assert final.error.before_first_token is False


def test_stream_success_yields_content_and_one_decision_row(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    events = rig.stream()
    final = events[-1]
    assert final.result is not None
    assert final.result.decision_id is not None
    texts = [e.event.text for e in events if e.event is not None and e.event.kind == "content"]
    assert texts == ["ok answer"]


def test_handle_returns_an_awaitable_result_or_an_event_stream(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    messages = [{"role": "user", "content": "hello there"}]

    async def one_shot() -> str:
        pending = rig.router.handle(RouteRequest(messages))
        assert asyncio.iscoroutine(pending)
        result = await pending
        return result.decision.model

    async def streamed() -> list[str]:
        events = rig.router.handle(RouteRequest(messages, stream=True))
        assert not asyncio.iscoroutine(events)
        assert isinstance(events, AsyncIterator)
        return [e.kind async for e in events]

    assert asyncio.run(one_shot()) == FREE
    assert asyncio.run(streamed())[-1] == "final"
````

## tests/test_router_verifiers.py

````python
"""HDW-117: the router's verification helpers (BasicVerifier wiring, broken verifiers)."""

from __future__ import annotations

from headwaters.core.executor.types import ExecResult, ToolCall, Usage
from headwaters.core.router_verify import run_verifiers, schema_of, wants_verify
from headwaters.core.verify_basic import BasicVerifier
from tests.router_support import Verifier


def answer(content: str, tool_calls: tuple[ToolCall, ...] = ()) -> ExecResult:
    return ExecResult("m", "local", content, "", tool_calls, "stop", Usage(), 0.1)


class Crashing:
    api_version = 1

    def verify(self, candidate: str) -> list[str]:
        raise RuntimeError("verifier bug")


def test_wants_verify_follows_the_mode_and_the_mandatory_flag() -> None:
    assert wants_verify("always", agentic=False, required=False)
    assert not wants_verify("off", agentic=True, required=False)
    assert wants_verify("agentic", agentic=True, required=False)
    assert not wants_verify("agentic", agentic=False, required=False)
    assert wants_verify("off", agentic=False, required=True)  # below the floor: mandatory


def test_schema_of_reads_an_openai_response_format() -> None:
    schema = {"type": "object", "required": ["a"]}
    fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": schema}}
    assert schema_of(fmt) == schema
    assert schema_of({"type": "json_object"}) is None
    assert schema_of("json") is None
    assert schema_of(None) is None


def test_basic_verifier_checks_the_schema_and_tool_calls() -> None:
    schema = {"type": "object", "required": ["a"]}
    fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": schema}}
    outcome = run_verifiers([BasicVerifier()], answer('{"b": 1}'), response_format=fmt)
    assert outcome.ran
    assert outcome.passed is False
    assert any(p.startswith("json_schema_violation") for p in outcome.problems)

    tools = [{"type": "function", "function": {"name": "run", "parameters": {"type": "object"}}}]
    bad_call = (ToolCall("1", "missing_tool", "{}"),)
    outcome = run_verifiers([BasicVerifier()], answer("", bad_call), tools=tools)
    assert outcome.passed is False
    assert any(p.startswith("tool_unknown") for p in outcome.problems)


def test_a_crashing_verifier_is_skipped_not_fatal() -> None:
    outcome = run_verifiers([Crashing(), Verifier()], answer("fine"))
    assert outcome.ran
    assert outcome.passed is True
    assert outcome.verifiers == 1


def test_only_crashing_verifiers_means_not_run() -> None:
    outcome = run_verifiers([Crashing()], answer("fine"))
    assert outcome.ran is False
    assert outcome.passed is None
    assert not outcome.failed


def test_duplicate_problems_are_reported_once() -> None:
    outcome = run_verifiers([Verifier(), Verifier()], answer("bad"))
    assert outcome.problems == ("bad_output: contains the word bad",)
    assert outcome.verifiers == 2
````

## tests/test_router_verify_log.py

````python
"""HDW-117: router verification, escalation and decision/outcome logging."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.executor.types import Delta
from headwaters.core.router_types import RouteFailed
from headwaters.core.state_decisions import get_outcome, recent_decisions
from tests.router_rig import Rig
from tests.router_support import (
    OK,
    RATE_LIMITED,
    Escalator,
    Ledger,
    Verifier,
    candidate,
    registry_with,
)
from tests.test_router import FREE, LOCAL, pool

BAD = [Delta(content="this is bad output"), Delta(finish_reason="stop")]
PROBLEM = "bad_output: contains the word bad"


def test_verify_failure_is_reported_without_a_plugin(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: BAD, LOCAL: OK}, pool(), verifiers=[Verifier()])
    result = rig.run(verify="always")
    assert result.decision.model == FREE  # no EscalationPlugin: nothing else is tried
    assert result.verify_failed
    assert result.verify is not None
    assert result.verify.problems == (PROBLEM,)
    assert rig.dispatch.calls == [FREE]
    assert [a.status for a in result.attempts] == ["verify_failed"]
    outcome = get_outcome(rig.db, result.decision_id or 0)
    assert outcome is not None
    assert (outcome.status, outcome.verify_result) == ("verify_failed", "fail")


def test_verify_failure_escalates_when_a_plugin_is_registered(tmp_path: Path) -> None:
    plugin = Escalator()
    rig = Rig(
        tmp_path,
        {FREE: BAD, LOCAL: OK},
        pool(local=84.0),
        verifiers=[Verifier()],
        registry=registry_with(escalation=plugin),
    )
    result = rig.run(verify="always")
    assert result.decision.model == LOCAL
    assert not result.verify_failed
    assert plugin.calls == [(1, (PROBLEM,))]
    assert [a.status for a in result.attempts] == ["verify_failed", "ok"]
    outcome = get_outcome(rig.db, result.decision_id or 0)
    assert outcome is not None
    assert outcome.escalated_to == LOCAL


def test_escalation_with_nothing_left_reports_the_failed_answer(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {FREE: BAD},
        [candidate(FREE, "free_cloud", 90.0)],
        verifiers=[Verifier()],
        registry=registry_with(escalation=Escalator()),
    )
    result = rig.run(verify="always")
    assert result.verify_failed
    assert len(recent_decisions(rig.db)) == 1  # one row, not an extra failure row


def test_verify_off_skips_the_gate(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: BAD}, pool(), verifiers=[Verifier()])
    result = rig.run(verify="off")
    assert result.verify is None
    assert not result.verify_failed


def test_requested_verify_without_any_verifier_is_reported_as_not_run(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    result = rig.run(verify="always")
    assert result.verify is not None
    assert result.verify.ran is False
    assert not result.verify_failed


def test_decision_and_outcome_rows_are_written(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    result = rig.run(surface="mcp")
    rows = recent_decisions(rig.db)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == result.decision_id
    assert (row.surface, row.chosen, row.scope) == ("mcp", LOCAL, "free")
    assert row.session == result.session_key
    outcome = get_outcome(rig.db, row.id or 0)
    assert outcome is not None
    assert outcome.status == "ok"
    assert outcome.latency_ms is not None


def test_logged_rows_never_contain_the_prompt_or_answer(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    rig.run(messages=[{"role": "user", "content": "please refactor zebra_widget_frobnicator"}])
    dump = repr(recent_decisions(rig.db))
    assert "zebra_widget_frobnicator" not in dump
    assert "ok answer" not in dump


def test_failed_requests_are_logged_too(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED]}, [candidate(FREE, "free_cloud", 90.0)])
    with pytest.raises(RouteFailed):
        rig.run()
    rows = recent_decisions(rig.db)
    assert len(rows) == 1
    outcome = get_outcome(rig.db, rows[0].id or 0)
    assert outcome is not None
    assert outcome.status == "no_route_left"


def test_ledger_plugins_get_metadata_only(tmp_path: Path) -> None:
    ledger = Ledger()
    rig = Rig(tmp_path, {FREE: OK}, pool(), registry=registry_with(ledger=ledger))
    result = rig.run()
    assert len(ledger.events) == 1
    event = ledger.events[0]
    assert event["decision_id"] == result.decision_id
    assert event["model"] == FREE
    assert "ok answer" not in repr(event)
````

## tests/test_scope.py

````python
from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.project_config import ProjectConfig
from headwaters.core.rules import Rules
from headwaters.core.scope import (
    ALL_TIERS,
    PAID_SCOPES_FEATURE_ID,
    SCOPE_TIERS,
    ScopeError,
    paid_scopes_available,
    parse_model_name,
    resolve_scope,
)
from headwaters.core.secrets_detect import SecretMatch

SECRET = (SecretMatch("sk-style-api-key", 0, 40),)
AUTO = ALL_TIERS
FREE = frozenset({"free_cloud", "local"})
LOCAL = frozenset({"local"})
CLOUD = frozenset({"paid", "openrouter_auto", "free_cloud"})
FREE_CLOUD_ONLY = frozenset({"free_cloud"})

PLC = "private_lock_conflict"
PRC = "project_rule_conflict"

# (id, default_scope, private_lock, privacy, secret, secrets_action, requested, paid, expected)
# ``expected`` is a tier set, or an error code string.
Row = tuple[str, str, bool, str, bool, str, str | None, bool, Any]
GOLDEN: list[Row] = [
    # Plain scopes, paid-scopes feature on.
    ("auto", "auto", False, "any", False, "local", None, True, AUTO),
    ("free", "free", False, "any", False, "local", None, True, FREE),
    ("local", "local", False, "any", False, "local", None, True, LOCAL),
    ("cloud", "cloud", False, "any", False, "local", None, True, CLOUD),
    # Requested beats the default (both directions).
    ("req-local-over-auto", "auto", False, "any", False, "local", "local", True, LOCAL),
    ("req-cloud-over-local", "local", False, "any", False, "local", "cloud", True, CLOUD),
    ("req-free-over-cloud", "cloud", False, "any", False, "local", "free", True, FREE),
    # Without the paid-scopes feature, paid tiers drop out.
    ("auto-no-paid", "auto", False, "any", False, "local", None, False, FREE),
    ("cloud-no-paid", "cloud", False, "any", False, "local", None, False, FREE_CLOUD_ONLY),
    ("free-no-paid", "free", False, "any", False, "local", None, False, FREE),
    ("local-no-paid", "local", False, "any", False, "local", None, False, LOCAL),
    # Private lock.
    ("lock-auto", "auto", True, "any", False, "local", None, True, LOCAL),
    ("lock-free", "free", True, "any", False, "local", None, True, LOCAL),
    ("lock-local", "local", True, "any", False, "local", None, True, LOCAL),
    ("lock-req-local", "auto", True, "any", False, "local", "local", True, LOCAL),
    ("lock-req-cloud", "auto", True, "any", False, "local", "cloud", True, PLC),
    ("lock-req-cloud-no-paid", "auto", True, "any", False, "local", "cloud", False, PLC),
    ("lock-req-free", "auto", True, "any", False, "local", "free", True, LOCAL),
    # Project rule (tighten only).
    ("proj-free-on-auto", "auto", False, "free", False, "local", None, True, FREE),
    ("proj-local-on-auto", "auto", False, "local", False, "local", None, True, LOCAL),
    ("proj-local-on-free", "free", False, "local", False, "local", None, True, LOCAL),
    ("proj-any-keeps-local", "local", False, "any", False, "local", None, True, LOCAL),
    ("proj-free-keeps-local", "local", False, "free", False, "local", None, True, LOCAL),
    ("proj-free-req-cloud", "auto", False, "free", False, "local", "cloud", True, FREE_CLOUD_ONLY),
    ("proj-local-req-cloud", "auto", False, "local", False, "local", "cloud", True, PRC),
    ("proj-local-req-cloud-no-paid", "auto", False, "local", False, "local", "cloud", False, PRC),
    # Secret detection.
    ("secret-auto", "auto", False, "any", True, "local", None, True, LOCAL),
    ("secret-free", "free", False, "any", True, "local", None, True, LOCAL),
    ("secret-cloud", "cloud", False, "any", True, "local", None, True, "secret_conflict"),
    ("secret-refuse", "auto", False, "any", True, "refuse", None, True, "secret_refused"),
    ("secret-off", "auto", False, "any", True, "off", None, True, AUTO),
    ("secret-off-cloud", "cloud", False, "any", True, "off", None, True, CLOUD),
    ("secret-local-scope", "local", False, "any", True, "local", None, True, LOCAL),
    ("secret-refuse-local", "local", False, "any", True, "refuse", None, True, "secret_refused"),
    # Combined locks: tighter always wins.
    ("lock+proj+secret", "auto", True, "free", True, "local", None, True, LOCAL),
    ("proj-free+secret", "auto", False, "free", True, "local", None, True, LOCAL),
    ("lock-req-cloud-secret-off", "auto", True, "any", True, "off", "cloud", True, PLC),
    ("proj-local+secret-req-cloud", "auto", False, "local", True, "local", "cloud", True, PRC),
]


@pytest.mark.parametrize(
    ("case", "default", "lock", "privacy", "secret", "action", "requested", "paid", "expected"),
    GOLDEN,
    ids=[row[0] for row in GOLDEN],
)
def test_golden_precedence_table(
    case: str,
    default: str,
    lock: bool,
    privacy: str,
    secret: bool,
    action: str,
    requested: str | None,
    paid: bool,
    expected: Any,
) -> None:
    rules = Rules.model_validate(
        {"default_scope": default, "private_lock": lock, "secrets": {"action": action}}
    )
    project = ProjectConfig.model_validate({"privacy": privacy})
    kwargs: dict[str, Any] = {
        "requested": requested,
        "secret_matches": SECRET if secret else (),
        "paid_scopes": paid,
    }
    if isinstance(expected, str):
        with pytest.raises(ScopeError) as caught:
            resolve_scope(rules, project, **kwargs)
        assert caught.value.code == expected
        assert caught.value.message
        assert caught.value.fix_hint
    else:
        decision = resolve_scope(rules, project, **kwargs)
        assert decision.tiers == expected, decision.reasons


def test_golden_table_has_at_least_25_unique_rows() -> None:
    assert len(GOLDEN) >= 25
    assert len({row[0] for row in GOLDEN}) == len(GOLDEN)


def test_scope_tier_definitions_match_the_ticket() -> None:
    assert SCOPE_TIERS["auto"] == {"paid", "openrouter_auto", "free_cloud", "local"}
    assert SCOPE_TIERS["free"] == {"free_cloud", "local"}
    assert SCOPE_TIERS["local"] == {"local"}
    assert SCOPE_TIERS["cloud"] == {"paid", "openrouter_auto", "free_cloud"}


def test_project_rules_can_only_tighten() -> None:
    """For every scope/lock combination, adding a project rule never adds tiers."""
    for default in ("auto", "free", "local", "cloud"):
        for lock in (False, True):
            for requested in (None, "auto", "free", "local", "cloud"):
                rules = Rules.model_validate({"default_scope": default, "private_lock": lock})
                try:
                    base = resolve_scope(rules, None, requested=requested, paid_scopes=True)
                except ScopeError:
                    continue
                for privacy in ("any", "free", "local"):
                    project = ProjectConfig.model_validate({"privacy": privacy})
                    try:
                        with_rule = resolve_scope(
                            rules, project, requested=requested, paid_scopes=True
                        )
                    except ScopeError:
                        continue  # refusing is tighter than anything
                    assert with_rule.tiers <= base.tiers


def test_nothing_ever_exceeds_the_requested_scope() -> None:
    for default in ("auto", "free", "local", "cloud"):
        rules = Rules.model_validate({"default_scope": default})
        for secret in (SECRET, ()):
            try:
                decision = resolve_scope(rules, secret_matches=secret, paid_scopes=True)
            except ScopeError:
                continue
            assert decision.tiers <= SCOPE_TIERS[default]


def test_override_lifts_secret_lock_only() -> None:
    rules = Rules.model_validate({"default_scope": "cloud"})
    decision = resolve_scope(rules, secret_matches=SECRET, secret_override=True, paid_scopes=True)
    assert decision.tiers == CLOUD
    assert any("override" in r for r in decision.reasons)
    locked = Rules.model_validate({"default_scope": "cloud", "private_lock": True})
    with pytest.raises(ScopeError) as caught:
        resolve_scope(locked, secret_matches=SECRET, secret_override=True, paid_scopes=True)
    assert caught.value.code == PLC
    with pytest.raises(ScopeError) as caught:
        resolve_scope(
            rules,
            ProjectConfig.model_validate({"privacy": "local"}),
            secret_matches=SECRET,
            secret_override=True,
            paid_scopes=True,
        )
    assert caught.value.code == PRC


def test_override_lets_the_user_proceed_past_refuse() -> None:
    rules = Rules.model_validate({"secrets": {"action": "refuse"}})
    decision = resolve_scope(rules, secret_matches=SECRET, secret_override=True, paid_scopes=True)
    assert decision.tiers == AUTO


def test_paid_exclusion_is_explained() -> None:
    decision = resolve_scope(Rules.model_validate({"default_scope": "cloud"}), paid_scopes=False)
    assert decision.tiers == FREE_CLOUD_ONLY
    assert any("paid tiers excluded" in r and "not enabled" in r for r in decision.reasons)


def test_no_paid_note_when_nothing_to_exclude() -> None:
    decision = resolve_scope(Rules.model_validate({"default_scope": "local"}), paid_scopes=False)
    assert not any("paid" in r for r in decision.reasons)


def test_locks_are_recorded() -> None:
    rules = Rules.model_validate({"default_scope": "auto", "private_lock": True})
    decision = resolve_scope(rules, ProjectConfig.model_validate({"privacy": "free"}))
    assert decision.locks == ("private_lock",)
    assert decision.local_only
    assert not decision.allows_cloud


def test_decision_helpers() -> None:
    decision = resolve_scope(Rules(), paid_scopes=True)
    assert decision.scope == "auto"
    assert decision.allows_cloud
    assert not decision.local_only
    assert (decision.strategy, decision.verify) == ("cost_saver", "agentic")
    assert resolve_scope(Rules.model_validate({"default_scope": "free"})).scope == "free"


@pytest.mark.parametrize(
    ("model", "scope", "strategy", "verify"),
    [
        ("headwaters/auto", "auto", None, None),
        ("headwaters/free", "free", None, None),
        ("headwaters/local", "local", None, None),
        ("headwaters/cloud", "cloud", None, None),
        ("headwaters/free+verify", "free", None, "always"),
        ("headwaters/auto+quality", "auto", "quality_first", None),
        ("headwaters/cloud+primary+noverify", "cloud", "primary_first", "off"),
        ("Headwaters/Local", "local", None, None),
        ("headwaters/quality", "auto", "quality_first", None),
        ("headwaters/auto+quality+verify", "auto", "quality_first", "always"),
    ],
)
def test_parse_model_name(model: str, scope: str, strategy: str | None, verify: str | None) -> None:
    parsed = parse_model_name(model)
    assert parsed is not None
    assert (parsed.scope, parsed.strategy, parsed.verify) == (scope, strategy, verify)


@pytest.mark.parametrize("model", ["gpt-4o", "openai/gpt-4o", "", "claude-sonnet"])
def test_parse_ignores_foreign_models(model: str) -> None:
    assert parse_model_name(model) is None


@pytest.mark.parametrize(
    ("model", "code"),
    [
        ("headwaters/bogus", "unknown_model"),
        ("headwaters/", "unknown_model"),
        ("headwaters/free+turbo", "unknown_suffix"),
        ("headwaters/free+", "unknown_suffix"),
    ],
)
def test_parse_rejects_unknown(model: str, code: str) -> None:
    with pytest.raises(ScopeError) as caught:
        parse_model_name(model)
    assert caught.value.code == code
    assert caught.value.fix_hint


def test_suffix_cannot_loosen_a_lock() -> None:
    parsed = parse_model_name("headwaters/auto+quality")
    assert parsed is not None
    decision = resolve_scope(
        Rules.model_validate({"private_lock": True}),
        requested=parsed.scope,
        strategy=parsed.strategy,
    )
    assert decision.tiers == LOCAL
    assert decision.strategy == "quality_first"


def test_paid_scopes_feature_flag() -> None:
    registry = FeatureRegistry()
    assert not paid_scopes_available(registry)
    registry.register(FeatureSpec(id=PAID_SCOPES_FEATURE_ID, star=True, title="Paid scopes"))
    assert paid_scopes_available(registry)
    registry.set_flags({PAID_SCOPES_FEATURE_ID: False})
    assert not paid_scopes_available(registry)

    cloud = resolve_scope(Rules.model_validate({"default_scope": "cloud"}), paid_scopes=False)
    assert cloud.scope == "cloud"


def test_reasons_never_contain_secret_text() -> None:
    secret_text = "sk-" + "Zq9" * 12
    match = SecretMatch("sk-style-api-key", 0, len(secret_text))
    decision = resolve_scope(Rules(), secret_matches=[match], paid_scopes=True)
    assert secret_text not in " ".join(decision.reasons)
    assert decision.secret_patterns == ("sk-style-api-key",)


def test_local_unavailable_gives_fix_hint() -> None:
    rules = Rules.model_validate({"default_scope": "local"})
    with pytest.raises(ScopeError) as caught:
        resolve_scope(rules, local_available=False, local_fix_hint="pull qwen2.5vl:7b")
    assert caught.value.code == "local_unavailable"
    assert caught.value.fix_hint == "pull qwen2.5vl:7b"
    assert caught.value.as_dict()["code"] == "local_unavailable"
    with pytest.raises(ScopeError) as default_hint:
        resolve_scope(rules, local_available=False)
    assert "Ollama" in default_hint.value.fix_hint


def test_local_unavailable_irrelevant_when_cloud_allowed() -> None:
    decision = resolve_scope(Rules(), local_available=False, paid_scopes=True)
    assert "local" in decision.tiers


def test_private_lock_error_names_the_fix() -> None:
    with pytest.raises(ScopeError) as caught:
        resolve_scope(Rules.model_validate({"private_lock": True}), requested="cloud")
    assert "Private lock" in caught.value.fix_hint


def test_strategy_and_verify_overrides() -> None:
    decision = resolve_scope(Rules(), strategy="quality_first", verify="always")
    assert (decision.strategy, decision.verify) == ("quality_first", "always")
````

## tests/test_secrets_detect.py

````python
import random
import string
from pathlib import Path

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.secrets_detect import SecretDetector

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def detector() -> SecretDetector:
    return SecretDetector.from_catalog(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH))


def _rand(rng: random.Random, alphabet: str, n: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


ALNUM = string.ascii_letters + string.digits


def _real_secrets(rng: random.Random) -> list[tuple[str, str]]:
    """(expected pattern name, secret text) built from random material."""
    return [
        ("sk-style-api-key", "sk-" + _rand(rng, ALNUM, 32)),
        ("sk-style-api-key", "sk-proj-" + _rand(rng, ALNUM, 40)),
        ("aws-access-key-id", "AKIA" + _rand(rng, string.ascii_uppercase + string.digits, 16)),
        ("github-token", "ghp_" + _rand(rng, ALNUM, 36)),
        ("github-token", "github_pat_" + _rand(rng, ALNUM + "_", 60)),
        ("slack-token", "xoxb-" + _rand(rng, string.digits, 12) + "-" + _rand(rng, ALNUM, 24)),
        ("google-api-key", "AIza" + _rand(rng, ALNUM + "_-", 35)),
        (
            "json-web-token",
            ".".join(
                [
                    "eyJ" + _rand(rng, ALNUM, 20),
                    "eyJ" + _rand(rng, ALNUM, 20),
                    _rand(rng, ALNUM, 20),
                ]
            ),
        ),
        ("bearer-token", "Bearer " + _rand(rng, ALNUM, 40)),
        ("private-key-block", "-----BEGIN RSA PRIVATE KEY-----\n" + _rand(rng, ALNUM, 60)),
        ("private-key-block", "-----BEGIN PRIVATE KEY-----"),
    ]


def test_detects_each_family(detector: SecretDetector) -> None:
    rng = random.Random(1)
    for name, secret in _real_secrets(rng):
        matches = detector.scan(f"here you go: {secret} thanks")
        assert name in {m.pattern for m in matches}, name


@pytest.mark.parametrize(
    "text",
    [
        "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
        "sk-...",
        "sk-your-key-goes-here-1234567890",
        "export OPENAI_API_KEY=YOUR_API_KEY",
        "token = 'your_token'",
        "AKIAEXAMPLEEXAMPLE12",
        "Bearer xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "sk-ant-api03-EXAMPLEKEYEXAMPLEKEYEXAMPLE",
        "ghp_" + "0" * 36,
    ],
)
def test_placeholders_are_ignored(detector: SecretDetector, text: str) -> None:
    assert detector.scan(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "def add(a, b):\n    return a + b\n",
        "Please refactor the skill tree and the sketch module.",
        "The task-list is sk-short.",
        "see https://example.com/docs#section",
        "AKIA is a prefix",
    ],
)
def test_clean_text_has_no_matches(detector: SecretDetector, text: str) -> None:
    assert not detector.contains_secret(text)


def test_matches_carry_no_secret_text(detector: SecretDetector) -> None:
    secret = "sk-" + "Qz8" * 12
    (match,) = detector.scan(f"key {secret}")
    assert secret not in repr(match)
    assert not hasattr(match, "text")
    assert (match.start, match.end) == (4, 4 + len(secret))


def test_scan_all_covers_every_text(detector: SecretDetector) -> None:
    found = detector.scan_all(["clean", "sk-" + "Ab1" * 10, "also clean"])
    assert [m.pattern for m in found] == ["sk-style-api-key"]


def test_fuzz_real_secrets_always_found_in_noise(detector: SecretDetector) -> None:
    rng = random.Random(20261030)
    noise = string.ascii_letters + string.digits + " \n\t.,;:(){}[]=<>/\\\"'`#"
    for _ in range(300):
        for name, secret in _real_secrets(rng):
            left = _rand(rng, noise, rng.randint(0, 80))
            right = _rand(rng, noise, rng.randint(0, 80))
            # Keep the secret delimited the way real prompts do, so word boundaries hold.
            text = f"{left}\n{secret}\n{right}"
            if any(w in (left + right).lower() for w in ("example", "dummy", "placeholder")):
                continue
            assert name in {m.pattern for m in detector.scan(text)}, (name, text)


def test_fuzz_random_noise_has_no_false_positives(detector: SecretDetector) -> None:
    rng = random.Random(7)
    alphabet = string.ascii_lowercase + " \n.,;(){}=_-"
    for _ in range(500):
        text = _rand(rng, alphabet, rng.randint(0, 400))
        assert not detector.contains_secret(text), text


def test_fuzz_never_raises_on_arbitrary_text(detector: SecretDetector) -> None:
    rng = random.Random(99)
    for _ in range(300):
        text = "".join(chr(rng.randint(0, 0x2FFF)) for _ in range(rng.randint(0, 300)))
        detector.scan(text)


def test_fuzz_placeholder_wrapped_keys_are_ignored(detector: SecretDetector) -> None:
    rng = random.Random(3)
    for _ in range(200):
        filler = rng.choice(["xxxxxxxxxxxxxxxx", "example", "dummy", "0" * 20])
        text = f"sk-{filler}{_rand(rng, ALNUM, 10)}"
        assert detector.scan(text) == [], text
````

## tests/test_walls.py

````python
"""HDW-110: wall classifier, with realistically shaped bodies for each provider."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import WallPattern
from headwaters.core.walls import WallClassifier, WallEvent, classify, parse_wait

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000.0  # fixed clock (epoch seconds)

CATALOG = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
CLASSIFIER = WallClassifier(CATALOG.wall_patterns)


def run(
    status: int, body: object, provider: str, headers: dict[str, str] | None = None
) -> WallEvent:
    return CLASSIFIER.classify(status, headers, body, provider, now=NOW)


def err(kind: str, message: str, **extra: object) -> str:
    return json.dumps({"error": {"message": message, "type": kind, **extra}})


# --- OpenAI -----------------------------------------------------------------------------------

OPENAI_QUOTA = err(
    "insufficient_quota",
    "You exceeded your current quota, please check your plan and billing details.",
    code="insufficient_quota",
)
OPENAI_RATE = err(
    "requests",
    "Rate limit reached for gpt-4o in organization org-x on requests per min (RPM): Limit 3.",
    code="rate_limit_exceeded",
)
OPENAI_CONTEXT = err(
    "invalid_request_error",
    "This model's maximum context length is 128000 tokens. However, your messages resulted in "
    "130012 tokens. Please reduce the length of the messages.",
    code="context_length_exceeded",
)


def test_openai_insufficient_quota_is_exhausted_key_scope() -> None:
    event = run(429, OPENAI_QUOTA, "openai", {"retry-after": "20"})
    assert (event.kind, event.scope) == ("exhausted", "key")
    assert event.is_wall


def test_openai_rate_limit_reads_reset_headers() -> None:
    headers = {"x-ratelimit-reset-requests": "20s", "x-ratelimit-reset-tokens": "6m0s"}
    event = run(429, OPENAI_RATE, "openai", headers)
    assert (event.kind, event.scope) == ("rate_limit", "model")
    assert event.retry_after == pytest.approx(360.0)  # the latest reset wins
    assert event.reset_at == pytest.approx(NOW + 360.0)


def test_openai_context_length() -> None:
    event = run(400, OPENAI_CONTEXT, "openai")
    assert (event.kind, event.scope) == ("context", "model")
    assert not event.is_wall
    assert event.retry_after is None


# --- Anthropic --------------------------------------------------------------------------------


def anthropic(kind: str, message: str) -> str:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}})


def test_anthropic_rate_limit_error_with_retry_after() -> None:
    body = anthropic("rate_limit_error", "Number of request tokens has exceeded your limit.")
    event = run(429, body, "anthropic", {"Retry-After": "17"})
    assert (event.kind, event.scope) == ("rate_limit", "model")
    assert event.retry_after == pytest.approx(17.0)


def test_anthropic_reset_header_is_rfc3339() -> None:
    body = anthropic("rate_limit_error", "slow down")
    headers = {"anthropic-ratelimit-requests-reset": "2027-01-15T08:00:30Z"}
    event = run(429, body, "anthropic", headers)
    assert event.reset_at == 1_800_000_030.0  # 2027-01-15T08:00:30Z
    assert event.retry_after == pytest.approx(30.0)


def test_anthropic_overloaded_529() -> None:
    body = anthropic("overloaded_error", "Overloaded")
    event = run(529, body, "anthropic")
    assert (event.kind, event.scope) == ("overloaded", "provider")


def test_anthropic_credit_balance_too_low_is_exhausted() -> None:
    body = anthropic(
        "invalid_request_error",
        "Your credit balance is too low to access the Anthropic API. Please go to Plans & "
        "Billing to upgrade or purchase credits.",
    )
    event = run(400, body, "anthropic")
    assert (event.kind, event.scope) == ("exhausted", "key")


def test_anthropic_prompt_too_long_is_context() -> None:
    body = anthropic("invalid_request_error", "prompt is too long: 215000 tokens > 200000 maximum")
    assert run(400, body, "anthropic").kind == "context"


# --- Gemini -----------------------------------------------------------------------------------


def gemini(quota_id: str, retry: str | None) -> str:
    violation = {"quotaMetric": "generate_content_free_tier_requests", "quotaId": quota_id}
    details: list[dict[str, object]] = [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [violation]}
    ]
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    return json.dumps(
        {
            "error": {
                "code": 429,
                "message": f"You exceeded your current quota. Quota exceeded for {quota_id}.",
                "status": "RESOURCE_EXHAUSTED",
                "details": details,
            }
        }
    )


def test_gemini_daily_quota_is_exhausted() -> None:
    body = gemini("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "41s")
    event = run(429, body, "gemini")
    assert (event.kind, event.scope) == ("exhausted", "model")


def test_gemini_per_minute_is_rate_limit_with_retry_delay_from_body() -> None:
    body = gemini("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "30s")
    event = run(429, body, "gemini")
    assert (event.kind, event.scope) == ("rate_limit", "model")
    assert event.retry_after == pytest.approx(30.0)


def test_gemini_rate_limit_without_hint_has_no_wait() -> None:
    event = run(429, gemini("GenerateRequestsPerMinutePerProjectPerModel", None), "gemini")
    assert event.kind == "rate_limit"
    assert event.retry_after is None
    assert event.reset_at is None


def test_gemini_context_length() -> None:
    body = json.dumps(
        {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "The input token count (1200000) exceeds the maximum number of "
                "tokens allowed (1048576).",
            }
        }
    )
    assert run(400, body, "gemini").kind == "context"


# --- OpenRouter -------------------------------------------------------------------------------


def openrouter(code: int, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}})


def test_openrouter_insufficient_credits_is_provider_scope() -> None:
    body = openrouter(
        402, "Insufficient credits. Add more using https://openrouter.ai/settings/credits"
    )
    event = run(402, body, "openrouter")
    assert (event.kind, event.scope) == ("exhausted", "provider")


def test_openrouter_402_cannot_afford_max_tokens() -> None:
    body = openrouter(
        402,
        "This request requires more credits, or fewer max_tokens. You can only afford 4000.",
    )
    assert run(402, body, "openrouter").scope == "provider"


def test_openrouter_free_model_rate_limit_uses_epoch_ms_reset() -> None:
    body = openrouter(429, "Provider returned error: rate-limited upstream")
    reset_ms = str(int((NOW + 45) * 1000))
    event = run(429, body, "openrouter", {"X-RateLimit-Limit": "20", "X-RateLimit-Reset": reset_ms})
    assert (event.kind, event.scope) == ("rate_limit", "model")
    assert event.retry_after == pytest.approx(45.0)


def test_openrouter_free_models_per_day_is_exhausted() -> None:
    body = openrouter(
        429,
        "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model "
        "requests per day",
    )
    reset_ms = str(int((NOW + 3600) * 1000))
    event = run(429, body, "openrouter", {"X-RateLimit-Reset": reset_ms})
    assert (event.kind, event.scope) == ("exhausted", "key")
    assert event.retry_after == pytest.approx(3600.0)


def test_openrouter_context_length() -> None:
    body = openrouter(400, "This endpoint's maximum context length is 32768 tokens.")
    assert run(400, body, "openrouter").kind == "context"


# --- Ollama -----------------------------------------------------------------------------------


def test_ollama_busy_queue_is_overloaded() -> None:
    body = json.dumps({"error": "server busy, please try again. maximum pending requests exceeded"})
    event = run(503, body, "ollama")
    assert (event.kind, event.scope) == ("overloaded", "provider")


def test_ollama_missing_model_is_not_a_wall() -> None:
    event = run(404, json.dumps({"error": "model 'nope:7b' not found"}), "ollama")
    assert event.kind == "other"
    assert not event.is_wall
    assert event.pattern_index is None


# --- OpenCode ---------------------------------------------------------------------------------


def opencode(kind: str, message: str) -> str:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}})


def test_opencode_free_usage_limit_is_rate_limit_provider_scope() -> None:
    body = opencode("FreeUsageLimitError", "Rate limit exceeded")
    event = run(429, body, "opencode", {"retry-after": "3600"})
    assert (event.kind, event.scope) == ("rate_limit", "provider")
    assert event.retry_after == pytest.approx(3600.0)


def test_opencode_credits_error_is_exhausted() -> None:
    event = run(402, opencode("CreditsError", "Insufficient balance"), "opencode")
    assert (event.kind, event.scope) == ("exhausted", "provider")


# --- Generic behaviour ------------------------------------------------------------------------


def test_generic_402_503_429_fall_back_to_wildcard_patterns() -> None:
    assert run(402, "payment required", "some-new-provider").kind == "exhausted"
    assert run(503, "", "some-new-provider").kind == "overloaded"
    assert run(429, "", "some-new-provider").kind == "rate_limit"


def test_overloaded_body_with_unusual_status() -> None:
    body = anthropic("overloaded_error", "Overloaded")
    assert run(200, body, "some-new-provider").kind == "overloaded"


@pytest.mark.parametrize("status", [200, 400, 401, 404, 500])
def test_non_wall_statuses_are_other(status: int) -> None:
    event = run(status, '{"error": "boom"}', "openai")
    assert event.kind == "other"
    assert event.retry_after is None
    assert event.reset_at is None


def test_other_ignores_reset_headers() -> None:
    event = run(500, "oops", "openai", {"Retry-After": "30"})
    assert event.kind == "other"
    assert event.retry_after is None


def test_body_types_bytes_dict_none() -> None:
    assert run(429, OPENAI_QUOTA.encode(), "openai").kind == "exhausted"
    assert run(429, json.loads(OPENAI_QUOTA), "openai").kind == "exhausted"
    assert run(429, None, "openai").kind == "rate_limit"
    assert run(429, b"\xff\xfe insufficient_quota", "openai").kind == "exhausted"
    assert run(429, object(), "openai").kind == "rate_limit"


def test_provider_match_is_case_insensitive() -> None:
    assert run(429, OPENAI_QUOTA, "OpenAI").kind == "exhausted"


def test_priority_body_beats_status_and_specific_beats_wildcard() -> None:
    patterns = [
        WallPattern(provider="*", status_codes=[429], kind="rate_limit", scope="model"),
        WallPattern(provider="p", status_codes=[429], kind="overloaded", scope="provider"),
        WallPattern(provider="*", body_regex="special", kind="exhausted", scope="key"),
    ]
    classifier = WallClassifier(patterns)
    assert classifier.classify(429, None, "x", "p", now=NOW).kind == "overloaded"
    assert classifier.classify(429, None, "x", "q", now=NOW).kind == "rate_limit"
    assert classifier.classify(429, None, "special", "p", now=NOW).kind == "exhausted"


def test_patterns_come_from_the_catalog_only() -> None:
    empty = WallClassifier([])
    assert empty.classify(429, None, OPENAI_QUOTA, "openai", now=NOW).kind == "other"


def test_module_level_classify_uses_catalog_and_requires_a_source() -> None:
    assert classify(402, {}, "", "x", catalog=CATALOG, now=NOW).kind == "exhausted"
    assert classify(429, {}, "", "x", patterns=CATALOG.wall_patterns).kind == "rate_limit"
    with pytest.raises(ValueError, match="catalog or patterns"):
        classify(429, {}, "", "x")


def test_event_carries_no_body_text() -> None:
    assert "quota" not in repr(run(429, OPENAI_QUOTA, "openai"))


# --- Header and wait parsing ------------------------------------------------------------------


def test_retry_after_seconds_and_fractions() -> None:
    assert run(429, "", "x", {"retry-after": "12"}).retry_after == pytest.approx(12.0)
    assert run(429, "", "x", {"retry-after": "1.5"}).retry_after == pytest.approx(1.5)


def test_retry_after_http_date() -> None:
    event = run(429, "", "x", {"Retry-After": "Fri, 15 Jan 2027 08:00:30 GMT"})
    assert event.reset_at == 1_800_000_030.0
    assert event.retry_after == pytest.approx(30.0)


def test_retry_after_in_the_past_clamps_to_zero() -> None:
    event = run(429, "", "x", {"Retry-After": "Mon, 01 Jan 2024 00:00:00 GMT"})
    assert event.retry_after == 0.0


def test_retry_after_ms_header() -> None:
    assert run(429, "", "x", {"retry-after-ms": "2500"}).retry_after == pytest.approx(2.5)


def test_retry_after_wins_over_reset_headers() -> None:
    headers = {"retry-after": "5", "x-ratelimit-reset": "9999"}
    assert run(429, "", "x", headers).retry_after == pytest.approx(5.0)


def test_unparseable_headers_are_ignored() -> None:
    headers = {"retry-after": "soon", "x-ratelimit-reset": "later", "retry-after-ms": "nan"}
    event = run(429, "", "x", headers)
    assert event.kind == "rate_limit"
    assert event.retry_after is None


def test_reset_header_epoch_seconds_and_relative_seconds() -> None:
    assert run(429, "", "x", {"x-ratelimit-reset": str(int(NOW) + 30)}).retry_after == 30.0
    assert run(429, "", "x", {"x-ratelimit-reset": "30"}).retry_after == 30.0
    assert run(429, "", "x", {"ratelimit-reset": "7"}).retry_after == 7.0


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30", 30.0), ("1.5s", 1.5), ("6m0s", 360.0), ("1h2m3s", 3723.0), ("250ms", 0.25)],
)
def test_parse_wait_durations(text: str, seconds: float) -> None:
    assert parse_wait(text, NOW) == pytest.approx(NOW + seconds)


@pytest.mark.parametrize("text", ["", "  ", "abc", "-5", "inf", "5x", "12 parsecs"])
def test_parse_wait_rejects_garbage(text: str) -> None:
    assert parse_wait(text, NOW) is None


def test_parse_wait_naive_timestamp_is_utc() -> None:
    assert parse_wait("2027-01-15T08:00:30", NOW) == parse_wait("2027-01-15T08:00:30Z", NOW)


def test_default_clock_is_used_when_now_is_omitted() -> None:
    event = CLASSIFIER.classify(429, {"retry-after": "10"}, "", "x")
    assert event.retry_after == pytest.approx(10.0, abs=1.0)
````
