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
        reasoning_ok=kw.pop("reasoning_ok", None),
        ctx=128_000,
        price_in=kw.pop("price_in", None),
        price_out=kw.pop("price_out", None),
    )
    scores = {"code": code, "chat": code, "agentic_tools": code}
    if "reasoning" in kw:
        scores["reasoning"] = kw.pop("reasoning")
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
