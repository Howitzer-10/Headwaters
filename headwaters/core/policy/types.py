"""Request, context and result types of the decision ladder (HDW-114)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.hardware.state import HardwareState
from headwaters.core.plugins import EscalationPlugin
from headwaters.core.policy.errors import Suggestion
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.scoring import ScoreBreakdown
from headwaters.core.rules import Rules, TierDirection, TierOrder
from headwaters.core.scope import ScopeDecision, TierName

Rung = Literal["pin", "force", "cheap", "optimistic", "paid", "best_effort", "free_router"]
FORCE_PREMIUM = "premium"


@dataclass(frozen=True)
class PolicyRequest:
    requirements: Requirements
    scope: ScopeDecision
    pin: str | None = None  # a model id the user pinned
    force: str | None = None  # "premium" (Fusion) is the only force flag in the core
    # HDW-513 (D17): tiers already attempted in this request, in order. The ladder rotates its
    # round-robin so a tier that just failed does not take the next attempt ahead of a tier that
    # has never been tried. Empty for the first decision of a request.
    tier_attempts: tuple[TierName, ...] = ()
    # HDW-516: the order the fallbacks are built in (``router.tier_order`` /
    # ``router.tier_direction``). Filled from the live rules by ``decide``; the defaults keep the
    # HDW-513 behaviour, so a caller that never sets them gets exactly the old ladder.
    tier_order: TierOrder = "interleave"
    tier_direction: TierDirection = "cloud_first"


@dataclass(frozen=True)
class PolicyContext:
    candidates: Sequence[Candidate]
    rules: Rules
    hw: HardwareState | None = None
    escalation: EscalationPlugin | None = None
    pull_hint: str | None = None  # "pull <catalog model>" for this GPU and role (HDW-137)


@dataclass(frozen=True)
class RouteDecision:
    model: str
    tier: TierName
    score: float
    floor: float
    below_floor: bool
    scope: ScopeDecision
    reasons: tuple[str, ...]
    fallbacks: tuple[str, ...]
    suggestions: tuple[Suggestion, ...]
    rung: Rung
    verify_required: bool  # the verification gate is mandatory (below floor or optimistic)
    breakdown: ScoreBreakdown
    # HDW-516: the tier order the fallbacks were built with, so the surfaces can print it
    # without re-reading the rules (``headwaters pick``, status).
    tier_order: TierOrder = "interleave"
    tier_direction: TierDirection = "cloud_first"
    # HDW-502: the ★ paid-scopes feature's handback line for an answer that had to be served
    # below the floor with no paid tier. ``None`` without the feature, so no surface prints one.
    advisory: str | None = None


Entry = tuple[Candidate, ScoreBreakdown, Rung]
