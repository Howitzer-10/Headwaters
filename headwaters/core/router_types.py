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
    # HDW-304: ``pin`` is an explicit model the user asked for by name; it passes through like
    # any other pick and fails over when walls or a failed call stop it. ``pin_strict`` (the
    # gateway's ``x-headwaters-pin: true``) turns that failover off: the answer comes from that
    # model or the request fails.
    pin_strict: bool = False
    force: str | None = None
    session_id: str | None = None
    project: ProjectConfig | None = None
    secret_override: bool = False
    options: ExecOptions | None = None
    on_progress: ProgressCallback | None = None
    # HDW-140 (D12). ``task_hint`` forces ``soft.task`` (one of the analyzer tasks) without
    # touching the hard filter. ``reasoning`` becomes ``ExecOptions.reasoning`` (None = provider
    # default, False = off, True or an effort string = on); switched on, it also requires a
    # reasoning-capable model. Reasoning text is never returned or stored (see ``RouteResult``).
    task_hint: str | None = None
    reasoning: bool | str | None = None


@dataclass(frozen=True)
class Attempt:
    """One model call. ``status`` is ``ok``, ``verify_failed`` or an error code."""

    model: str
    tier: TierName
    status: str
    wait_s: float = 0.0


@dataclass(frozen=True)
class VerifyOutcome:
    """What the gate found. ``checked`` counts checks that really ran (skips excluded).

    ``passed`` is None when nothing was established: no verifier could run, or every check was
    skipped (F11). ``unchecked`` is set by the router when the gate was mandatory and ``checked``
    is 0: the answer was not verified and must never be called verified.
    """

    ran: bool
    passed: bool | None
    problems: tuple[str, ...] = ()
    verifiers: int = 0
    checked: int = 0
    unchecked: bool = False

    @property
    def failed(self) -> bool:
        return self.ran and self.passed is False

    @property
    def status(self) -> str:
        """``unchecked``, ``fail``, ``pass`` or ``skipped`` (nothing established)."""
        if self.unchecked:
            return "unchecked"
        if self.failed:
            return "fail"
        return "pass" if self.passed else "skipped"


@dataclass(frozen=True)
class RouteResult:
    """The answer plus how it was routed. ``decision`` is the one that produced ``result``.

    Reasoning rule (D12): ``result.reasoning`` is always empty. The router drops the model's
    reasoning text before it reaches a caller, a stream event, a log row or a ledger event.
    """

    result: ExecResult
    decision: RouteDecision
    session_key: str
    attempts: tuple[Attempt, ...]
    decision_id: int | None = None
    verify: VerifyOutcome | None = None
    first_choice: str | None = None  # what the policy picked first, when different from model
    reasons: tuple[str, ...] = field(default=())
    latency_ms: int | None = None  # wall clock from request start to answer (footer)
    cost_usd: float | None = None  # priced from catalog rates, None when unpriced (footer)

    @property
    def verify_failed(self) -> bool:
        return self.verify is not None and self.verify.failed

    @property
    def verify_status(self) -> str | None:
        """``unchecked`` when a mandatory gate checked nothing; None when no gate ran."""
        return None if self.verify is None else self.verify.status

    @property
    def below_floor(self) -> bool:
        """Stays visible to engines: a below-floor answer is never silently verified."""
        return self.decision.below_floor


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
