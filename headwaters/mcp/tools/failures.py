"""Turn a routing failure into a structured tool error (HDW-204).

Every refusal the router can raise already has a stable code and a fix; this maps them onto
``ErrorInfo``. Local scope stays fail-closed: an error here never suggests or performs a cloud
fallback (``use_cloud`` appears only when the policy produced it, i.e. no lock applies).
"""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.router_types import RouteFailed
from headwaters.core.scope import ScopeError
from headwaters.mcp.tools.context import ToolFailure
from headwaters.mcp.tools.schemas import ErrorInfo, FileNote, SuggestionOut

__all__ = ["Failure", "failure_info"]

_DEFAULT_FIX = "Run `headwaters status` to see what is blocking, then try again."


@dataclass(frozen=True)
class Failure:
    error: ErrorInfo
    suggestions: tuple[SuggestionOut, ...] = ()
    files: tuple[FileNote, ...] = ()


def failure_info(exc: BaseException) -> Failure | None:
    """The structured form of a known failure, or ``None`` for anything unexpected."""
    if isinstance(exc, ToolFailure):
        tips = tuple(SuggestionOut(code=c, text=t) for c, t in exc.suggestions)
        return Failure(ErrorInfo(code=exc.code, message=exc.message, fix=exc.fix), tips, exc.files)
    if isinstance(exc, ScopeError):
        return Failure(ErrorInfo(code=exc.code, message=exc.message, fix=exc.fix_hint))
    if isinstance(exc, NoRouteError):
        tips = tuple(SuggestionOut(code=s.code, text=s.text) for s in exc.suggestions)
        fix = tips[0].text if tips else _DEFAULT_FIX
        return Failure(ErrorInfo(code=exc.code, message=exc.message, fix=fix), tips)
    if isinstance(exc, RouteFailed):
        inner = exc.no_route
        tips = (
            tuple(SuggestionOut(code=s.code, text=s.text) for s in inner.suggestions)
            if inner is not None
            else ()
        )
        return Failure(ErrorInfo(code=exc.code, message=exc.message, fix=_DEFAULT_FIX), tips)
    if isinstance(exc, ExecutorError):
        return Failure(ErrorInfo(code=exc.code, message=exc.message, fix=_DEFAULT_FIX))
    return None
