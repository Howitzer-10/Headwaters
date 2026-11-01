"""How each model tool shapes its result (HDW-206): ``do``, ``ask`` and ``think``.

One ``Responder`` per tool tells the shared guard (``guarded.execute``) which pipeline to run,
which result model to fill, how a finished outcome becomes a result plus its text rendering, and
how a failure is shown. Foreground calls and background jobs use the same shapers, so a job's
stored result is exactly what the foreground call would have returned.
"""

from __future__ import annotations

from headwaters.mcp.tools.background import Shaper
from headwaters.mcp.tools.guarded import Responder
from headwaters.mcp.tools.outcome import Outcome
from headwaters.mcp.tools.render import render_ask, render_do
from headwaters.mcp.tools.results import ask_error, ask_result, do_error, do_result
from headwaters.mcp.tools.schemas import AskResult, DoResult
from headwaters.mcp.tools.schemas_more import ThinkResult

__all__ = ["ASK", "DO", "THINK"]


def _shape_do(outcome: Outcome) -> tuple[DoResult, str]:
    result = do_result(outcome)
    return result, render_do(result)


def _shape_ask(outcome: Outcome) -> tuple[AskResult, str]:
    result = ask_result(outcome)
    return result, render_ask(result)


def _shape_think(outcome: Outcome) -> tuple[ThinkResult, str]:
    result = ThinkResult(**ask_result(outcome).model_dump())
    return result, render_ask(AskResult(**result.model_dump()))


_DO: Shaper = _shape_do
_ASK: Shaper = _shape_ask
_THINK: Shaper = _shape_think

DO = Responder("do", "do", DoResult, _DO, do_error)
ASK = Responder("ask", "ask", AskResult, _ASK, ask_error)
THINK = Responder("think", "ask", ThinkResult, _THINK, ask_error)
