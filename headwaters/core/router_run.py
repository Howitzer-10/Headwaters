"""Per-request working state of the router (HDW-117). Holds the request and routing metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.executor.types import ExecOptions, Message
from headwaters.core.hardware.state import HardwareState
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.types import RouteDecision
from headwaters.core.router_failures import FailureMemory
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
    failures: FailureMemory = field(default_factory=FailureMemory)  # HDW-513, this request only
    notes: list[str] = field(default_factory=list)
    escalations: int = 0

    @property
    def excluded(self) -> set[str]:
        """Models this request may not choose again (kept as a set for existing callers)."""
        return self.failures.excluded

    def candidate(self, model_id: str) -> Candidate | None:
        return next((c for c in self.candidates if c.id == model_id), None)
