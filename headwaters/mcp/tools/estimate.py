"""How long a call will take and what it may cost, before it runs (HDW-206).

``Router.plan`` is a dry run of the first three router steps: no model is called and nothing is
written. Its first pick gives the numbers that two rules need: the call goes to the background when
the expected time is over ``mcp.async_threshold_s``, and the user is asked first when the expected
spend is over ``budget.confirm_above_usd``.

Both numbers are estimates of the FIRST pick only. A cascade or a verified retry may call another
model (that is bounded by ``max_attempts`` and by the scope), and a model's real speed is not known
until it runs. Prices come from the model rows (catalog data); the token and speed numbers are
first-draft tuning in ``ToolSettings``, never a model, a price or a provider limit.
"""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.router_types import RouteRequest
from headwaters.core.state_models import ModelRow
from headwaters.mcp.tools.context import ToolSettings
from headwaters.mcp.tools.flow import Prepared

__all__ = ["Estimate", "estimate_call"]


@dataclass(frozen=True)
class Estimate:
    model: str
    tier: str
    duration_s: float  # expected wall time of the first model call, load included
    cost_usd: float  # expected spend of that call; 0 for local and free models
    cold: bool  # a local model that is not loaded yet
    input_tokens: int
    output_tokens: int


def _unit_cost(row: ModelRow, tokens_in: int, tokens_out: int) -> float:
    """Catalog prices are per million tokens; an unpriced model costs 0."""
    per_m = 1_000_000.0
    return (tokens_in * (row.price_in or 0.0) + tokens_out * (row.price_out or 0.0)) / per_m


def _output_tokens(request: RouteRequest, settings: ToolSettings) -> int:
    base = settings.est_output_tokens
    if request.max_output_tokens:
        base = min(base, request.max_output_tokens)
    if request.reasoning:
        base = int(base * settings.reasoning_factor)
    return max(base, 1)


async def estimate_call(prepared: Prepared, settings: ToolSettings) -> Estimate:
    """Plan the request (a dry run) and estimate time and spend of the model it picks.

    Raises what ``Router.handle`` would raise for a request that has no route, so a refusal is
    reported before anything else happens.
    """
    plan = await prepared.engine.router.plan(prepared.request)
    pick = plan.decision
    candidate = next((c for c in plan.candidates if c.id == pick.model), None)
    tokens_in = plan.requirements.est_tokens
    tokens_out = _output_tokens(prepared.request, settings)
    if candidate is None:  # cannot happen for a real plan; stay quiet and cheap rather than guess
        return Estimate(pick.model, pick.tier, 0.0, 0.0, False, tokens_in, tokens_out)
    row = candidate.row
    if candidate.is_local:
        tps = candidate.expected_tps or settings.local_tps_fallback
        cold = not row.warm
        load = (row.size_gb or 0.0) * settings.cold_load_s_per_gb if cold else 0.0
        duration = load + tokens_out / tps
        # A local model costs no money: its price columns are empty, but say 0 outright.
        return Estimate(pick.model, pick.tier, duration, 0.0, cold, tokens_in, tokens_out)
    duration = settings.cloud_overhead_s + tokens_out / settings.cloud_tps
    cost = _unit_cost(row, tokens_in, tokens_out)
    return Estimate(pick.model, pick.tier, duration, cost, False, tokens_in, tokens_out)
