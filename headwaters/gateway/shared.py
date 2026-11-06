"""Plumbing the two protocol faces share (HDW-303): routing headers and error details.

``openai_routes`` (chat completions) and ``anthropic_routes`` (messages) answer with different
envelopes, but the facts behind them are the router's, not the protocol's: the headers of
v3.3 §5.1 and the meaning of a refusal are computed once here, so the two faces cannot disagree
about which model answered or why a request was refused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Coroutine, Sequence
from typing import Any, Protocol

from headwaters.core.advice import (
    BELOW_FLOOR_WARNING,
    REMOTE_IMAGE_ADVICE,
    STREAMED_VERIFY_NOTE,
    UNCHECKED_NOTE,
)
from headwaters.core.executor.errors import ExecutorError
from headwaters.core.policy.errors import NoRouteError, Suggestion
from headwaters.core.policy.types import RouteDecision
from headwaters.core.router_types import RouteEvent, RouteFailed, RouteRequest, RouteResult
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeError

__all__ = [
    "ADVICE_HEADER",
    "BELOW_FLOOR_HEADER",
    "IMAGE_URL_NOT_LOCAL",
    "SERVING_ERRORS",
    "SUGGESTION_HEADER",
    "VERIFY_HEADER",
    "VERIFY_PENDING",
    "RouterLike",
    "advice_note",
    "error_details",
    "headwaters_meta",
    "meta_headers",
    "suggestion_code",
    "verify_status",
]

IMAGE_URL_NOT_LOCAL = "image_url_not_local"
BELOW_FLOOR_HEADER = "x-headwaters-below-floor"
SUGGESTION_HEADER = "x-headwaters-suggestion"
VERIFY_HEADER = "x-headwaters-verify"
ADVICE_HEADER = "x-headwaters-advice"
VERIFY_PENDING = "pending"

# What ``Router.handle`` can raise before or instead of an answer: a policy refusal, an upstream
# failure or an executor error. A streaming call raises these at the first event, not on the call.
SERVING_ERRORS: tuple[type[Exception], ...] = (
    ScopeError,
    NoRouteError,
    RouteFailed,
    ExecutorError,
)


class RouterLike(Protocol):
    """The one method both faces need of ``core.router.Router``."""

    def handle(
        self, request: RouteRequest
    ) -> Coroutine[Any, Any, RouteResult] | AsyncIterator[RouteEvent]: ...


def meta_headers(
    decision: RouteDecision | None,
    rules: Rules,
    *,
    result: RouteResult | None = None,
    pending: bool = False,
) -> dict[str, str]:
    """The routing headers (v3.3 §5.1); absent when nothing was decided."""
    if decision is None:
        return {}
    headers = {
        "x-headwaters-model": decision.model,
        "x-headwaters-tier": decision.tier,
        "x-headwaters-scope": str(decision.scope.scope),
        "x-headwaters-reason": decision.rung,
    }
    if decision.below_floor:
        headers[BELOW_FLOOR_HEADER] = "true"
        code = suggestion_code(decision)
        if code is not None:
            headers[SUGGESTION_HEADER] = code
    status = verify_status(decision, result, pending=pending)
    if status is not None:
        headers[VERIFY_HEADER] = status
    note = advice_note(decision, result, rules, pending=pending)
    if note:
        headers[ADVICE_HEADER] = note
    return headers


def headwaters_meta(
    decision: RouteDecision, result: RouteResult | None, rules: Rules
) -> dict[str, Any]:
    """The routing facts of an answer, plus usage, cost and latency (HDW-512).

    The stream's ``headwaters`` chunk carries this, and so does the optional answer footer, so
    cost is priced once (in the router) and read from here rather than re-derived per face.
    """
    usage = None if result is None else result.result.usage
    return {
        "model": decision.model,
        "tier": decision.tier,
        "scope": str(decision.scope.scope),
        "reason": decision.rung,
        "below_floor": decision.below_floor,
        "suggestion": suggestion_code(decision),
        "verify_status": verify_status(decision, result, pending=False),
        "advice": advice_note(decision, result, rules, pending=False)
        if rules.advice == "inline"
        else None,
        "prompt_tokens": 0 if usage is None else usage.prompt_tokens or 0,
        "completion_tokens": 0 if usage is None else usage.completion_tokens or 0,
        "latency_ms": None if result is None else result.latency_ms,
        "cost_usd": None if result is None else result.cost_usd,
    }


def suggestion_code(decision: RouteDecision) -> str | None:
    return decision.suggestions[0].code if decision.suggestions else None


def verify_status(
    decision: RouteDecision | None, result: RouteResult | None, *, pending: bool
) -> str | None:
    """``pending`` while the check has not run yet (a stream), else the gate's own outcome.

    ``None`` means no check was due at all; an outcome is never invented, so an answer no gate
    looked at is reported ``unchecked`` by the router and never as verified.
    """
    if pending:
        return VERIFY_PENDING if decision is not None and decision.verify_required else None
    return None if result is None else result.verify_status


def advice_note(
    decision: RouteDecision | None, result: RouteResult | None, rules: Rules, *, pending: bool
) -> str | None:
    """The note that explains a refusal-shaped answer; only when advice is on at all."""
    if decision is None or rules.advice == "off":
        return None
    parts: list[str] = []
    # HDW-502: the ★ feature's handback line leads, since it is the one that names a way forward.
    if decision.advisory:
        parts.append(decision.advisory)
    if decision.below_floor:
        parts.append(BELOW_FLOOR_WARNING)
    if pending:
        if decision.verify_required:
            parts.append(STREAMED_VERIFY_NOTE)
    elif result is not None and result.verify_status == "unchecked":
        parts.append(UNCHECKED_NOTE)
    return " ".join(parts) if parts else None


def error_details(exc: Exception) -> tuple[int, str, str, str | None]:
    """Status, code, message and advice for anything ``Router.handle`` can refuse with."""
    if isinstance(exc, ScopeError):
        return 400, exc.code, exc.message, exc.fix_hint
    if isinstance(exc, NoRouteError):
        reasons = [rejection.reason for rejection in exc.rejected]
        message = _with_suggestions(exc.message, exc.suggestions)
        return 400, exc.code, message, _image_advice(reasons)
    if isinstance(exc, RouteFailed):
        reasons = [rejection.reason for rejection in exc.no_route.rejected] if exc.no_route else []
        if exc.last_error.get("code") == IMAGE_URL_NOT_LOCAL:
            reasons.append(IMAGE_URL_NOT_LOCAL)
        return 502, exc.code, exc.message, _image_advice(reasons)
    if isinstance(exc, ExecutorError):
        advice = REMOTE_IMAGE_ADVICE if exc.code == IMAGE_URL_NOT_LOCAL else None
        return 502, exc.code, exc.message, advice
    return 500, "internal_error", "The gateway could not serve the request.", None


def _with_suggestions(message: str, suggestions: Sequence[Suggestion]) -> str:
    texts = [suggestion.text for suggestion in suggestions if suggestion.text]
    return message if not texts else f"{message} Next: {' '.join(texts)}"


def _image_advice(reasons: Sequence[str]) -> str | None:
    """FU-135-3: a refusal caused by a remote image URL says to send a ``data:`` URL instead."""
    return REMOTE_IMAGE_ADVICE if IMAGE_URL_NOT_LOCAL in reasons else None
