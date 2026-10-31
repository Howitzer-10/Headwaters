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
