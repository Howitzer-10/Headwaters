"""Policy engine A, part 1: candidates and the hard filter (HDW-113, v3 section 4.5).

Hard filter = requirements (vision, tools, json, context >= headroom x tokens) intersected with
availability (walls/quotas), hardware fit (local models) and the effective scope tiers.
Pure functions: the router gathers candidates (catalog merge, quota tracker, hardware fit) and
this module only decides. No I/O, never touches request text.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from headwaters.core.analyzer.requirements import HardRequirements
from headwaters.core.hardware.fit import FitResult
from headwaters.core.hardware.residency import ResidencyInputs
from headwaters.core.hardware.state import HardwareState
from headwaters.core.quotas import Availability
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeDecision, TierName
from headwaters.core.state_models import ModelRow

_DEFAULT_TIER: dict[str, TierName] = {"local": "local", "free_cloud": "free_cloud", "paid": "paid"}

# ``normal`` models are ranked by score. The other roles are routes the router names (their
# slugs come from the catalog/discovery, never from this module): the OpenRouter auto router,
# the OpenRouter free router, and the premium (Fusion) route that is never picked automatically.
Role = Literal["normal", "openrouter_auto", "openrouter_free", "premium"]
_ROLE_TIER: dict[str, TierName] = {
    "openrouter_auto": "openrouter_auto",
    "openrouter_free": "free_cloud",
}


@dataclass(frozen=True)
class Candidate:
    """One model the router could call, with everything the policy needs to judge it.

    ``scores`` are 0..100 per task (user override already applied by the catalog merge).
    ``scope_tier`` tells the scope check which tier this model belongs to; it defaults from
    ``row.tier`` (a paid model is ``paid``; the router sets ``openrouter_auto`` for that route).
    ``fit`` is required for local models. ``expected_tps`` is the catalog speed for this GPU
    class. ``is_primary`` marks the user's configured paid primary model(s).
    """

    row: ModelRow
    scores: Mapping[str, float]
    availability: Availability = field(default_factory=lambda: Availability("ok"))
    fit: FitResult | None = None
    residency: ResidencyInputs | None = None
    expected_tps: float | None = None
    is_primary: bool = False
    scope_tier: TierName | None = None
    role: Role = "normal"
    is_session: bool = False  # the session's last successful model (D11); not a residency fact

    @property
    def id(self) -> str:
        return self.row.id

    @property
    def is_local(self) -> bool:
        return self.row.tier == "local"

    @property
    def tier(self) -> TierName:
        if self.scope_tier is not None:
            return self.scope_tier
        if self.role in _ROLE_TIER:
            return _ROLE_TIER[self.role]
        return _DEFAULT_TIER.get(self.row.tier, "paid")


@dataclass(frozen=True)
class Rejection:
    model_id: str
    reason: str  # stable code, e.g. no_vision, no_tools, ctx_too_small, walled, scope


@dataclass(frozen=True)
class FilterResult:
    kept: tuple[Candidate, ...]
    rejected: tuple[Rejection, ...]


def _first_failure(
    c: Candidate,
    hard: HardRequirements,
    scope: ScopeDecision,
    hw: HardwareState | None,
    rules: Rules,
) -> str | None:
    if c.tier not in scope.tiers:
        return "scope"
    if not c.availability.usable:
        return f"unavailable_{c.availability.status}"
    if c.is_local:
        if c.fit is None:
            return "no_fit_data"
        if not c.fit.usable:
            return f"no_fit_{c.fit.reason}"
    elif hw is not None and hw.battery_policy == "local_only":
        return "on_battery_local_only"
    if hard.vision and "image" not in c.row.modalities:
        return "no_vision"
    if hard.tools and c.row.tools_ok is not True:
        return "no_tools"
    if hard.json and c.row.json_ok is not True:
        return "no_json"
    if hard.reasoning and c.row.reasoning_ok is not True:
        return "no_reasoning"
    needed = math.ceil(rules.scoring.ctx_headroom * hard.min_ctx)
    if c.row.ctx is None or c.row.ctx < needed:
        return "ctx_too_small"
    return None


def hard_filter(
    candidates: Iterable[Candidate],
    hard: HardRequirements,
    scope: ScopeDecision,
    rules: Rules,
    hw: HardwareState | None = None,
) -> FilterResult:
    """Keep candidates that meet every hard requirement; say why the others were dropped."""
    kept: list[Candidate] = []
    rejected: list[Rejection] = []
    for c in candidates:
        why = _first_failure(c, hard, scope, hw, rules)
        if why is None:
            kept.append(c)
        else:
            rejected.append(Rejection(c.id, why))
    return FilterResult(tuple(kept), tuple(rejected))
