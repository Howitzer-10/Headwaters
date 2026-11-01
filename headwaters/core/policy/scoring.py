"""Policy engine A, part 2: quality floor and score (HDW-113, v3 section 4.5).

    floor = base[task] + k[task] * complexity + high_risk_bonus * (risk == high)
            + agentic_bonus * agentic          (agentic also needs agentic_tools >= floor)
    score = quality[task] - complexity_penalty + session_bonus + warm_bonus - latency_penalty
            - evict_penalty - cost_weight * cost

Every number comes from ``rules.yaml`` (``floors`` and ``scoring``); nothing is hard-coded here.
Strategies (cost_saver, quality_first, primary_first) only change the weights. Pure functions,
no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.analyzer.requirements import SoftRequirements
from headwaters.core.hardware.state import HardwareState
from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import FloorRule, Rules, Strategy, StrategyWeights

AGENTIC_TASK = "agentic_tools"
HIGH_RISK = "high"
_TOKENS_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class FloorResult:
    value: float
    task_base: float
    complexity_part: float
    risk_part: float
    agentic_part: float


@dataclass(frozen=True)
class ScoreBreakdown:
    """Every term of the score, so decisions can be explained and tested term by term."""

    model_id: str
    quality: float
    complexity_penalty: float
    warm_bonus: float
    latency_penalty: float
    evict_penalty: float
    cost_usd: float
    cost_penalty: float
    soft_penalty: float
    primary_bonus: float
    total: float
    floor: float
    meets_floor: bool
    below_floor_by: float  # 0 when the floor is met
    session_bonus: float = 0.0  # D11: the session's last good model; separate from residency


def strategy_weights(rules: Rules, strategy: Strategy) -> StrategyWeights:
    return getattr(rules.scoring.strategies, strategy)  # type: ignore[no-any-return]


def floor_for(soft: SoftRequirements, rules: Rules) -> FloorResult:
    """``base[task] + k[task]*complexity + high_risk_bonus + agentic_bonus``."""
    rule = getattr(rules.floors, soft.task, None)
    if not isinstance(rule, FloorRule):
        raise ValueError(f"no floor configured for task {soft.task!r}")
    complexity = rule.per_complexity * soft.complexity
    risk = rules.floors.high_risk_bonus if soft.risk == HIGH_RISK else 0
    agentic = rules.floors.agentic_bonus if soft.agentic else 0
    return FloorResult(
        float(rule.base + complexity + risk + agentic), rule.base, complexity, risk, agentic
    )


def meets_floor(c: Candidate, soft: SoftRequirements, floor: float) -> bool:
    """Task quality must reach the floor; agentic requests also need ``agentic_tools``."""
    if c.scores.get(soft.task, 0.0) < floor:
        return False
    return not (soft.agentic and c.scores.get(AGENTIC_TASK, 0.0) < floor)


def estimate_cost_usd(c: Candidate, est_tokens: int, rules: Rules) -> float:
    """USD for the request using catalog prices (per million tokens). Unpriced models cost 0."""
    price_in = c.row.price_in or 0.0
    price_out = c.row.price_out or 0.0
    out = rules.scoring.assumed_output_tokens
    return (est_tokens * price_in + out * price_out) / _TOKENS_PER_MILLION


def _latency_penalty(c: Candidate, hw: HardwareState | None, rules: Rules) -> float:
    s = rules.scoring
    penalty = 0.0
    if c.expected_tps is not None and c.expected_tps > 0:
        slowdown = max(0.0, s.latency_ref_tps / c.expected_tps - 1.0)
        penalty += min(s.latency_max_penalty, s.latency_weight * slowdown)
    if c.is_local and hw is not None:
        if hw.battery_policy == "prefer_cloud":  # already "normal" on AC (HDW-108)
            penalty += s.on_battery_local_penalty
        if hw.gpu_busy.busy:
            penalty += s.gpu_busy_local_penalty
    return penalty


def score_candidate(
    c: Candidate,
    soft: SoftRequirements,
    est_tokens: int,
    strategy: Strategy,
    rules: Rules,
    hw: HardwareState | None = None,
) -> ScoreBreakdown:
    s = rules.scoring
    w = strategy_weights(rules, strategy)
    floor = floor_for(soft, rules).value
    quality = float(c.scores.get(soft.task, 0.0))
    complexity_penalty = s.complexity_penalty_per_level * max(0, soft.complexity - 1)
    res = c.residency
    warm = res.warm_bonus * s.residency_scale * w.warm_mult if res else 0.0
    evict = res.eviction_penalty * s.residency_scale * w.evict_mult if res else 0.0
    latency = _latency_penalty(c, hw, rules) * w.latency_mult
    cost = estimate_cost_usd(c, est_tokens, rules)
    cost_penalty = w.cost_weight * cost
    soft_penalty = s.soft_quota_penalty if c.availability.status == "soft" else 0.0
    primary = w.primary_bonus if c.is_primary else 0.0
    session = s.session_bonus if c.is_session else 0.0
    total = (
        quality
        - complexity_penalty
        + session
        + warm
        - latency
        - evict
        - cost_penalty
        - soft_penalty
        + primary
    )
    ok = meets_floor(c, soft, floor)
    gap = max(0.0, floor - c.scores.get(soft.task, 0.0))
    if soft.agentic:
        gap = max(gap, floor - c.scores.get(AGENTIC_TASK, 0.0), 0.0)
    return ScoreBreakdown(
        c.id, quality, complexity_penalty, warm, latency, evict, cost, cost_penalty,
        soft_penalty, primary, total, floor, ok, 0.0 if ok else gap, session,
    )  # fmt: skip


def rank_candidates(
    candidates: list[Candidate],
    soft: SoftRequirements,
    est_tokens: int,
    strategy: Strategy,
    rules: Rules,
    hw: HardwareState | None = None,
) -> list[tuple[Candidate, ScoreBreakdown]]:
    """Best first. Ties go to local, then to the model id (stable, deterministic)."""
    scored = [(c, score_candidate(c, soft, est_tokens, strategy, rules, hw)) for c in candidates]
    scored.sort(key=lambda p: (-p[1].total, not p[0].is_local, p[0].id))
    return scored
