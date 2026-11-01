"""KV-cache math, model fit and ``num_ctx`` selection (HDW-109, v3 section 7).

All sizes are GiB (1024^3), matching ``HardwareState.budget_gb``; catalog ``size_gb`` is read
as GiB too. The KV formula is an upper bound for sliding-window / shared-KV architectures
(assumption R7), so calibrate weights against ``/api/ps`` after the first load.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from headwaters.core.catalog.schema import Arch, ModelEntry
from headwaters.core.rules import Rules

GIB = 1024**3
KV_BYTES_PER_ELEM: Mapping[str, float] = {"f16": 2.0, "q8_0": 1.0, "q4_0": 0.5}
DEFAULT_KV_TYPE = "f16"  # unknown type: assume the biggest (safe)
GB_TO_GIB = 1e9 / 1024**3

FitStatus = Literal["fits", "offload", "no_fit"]


@dataclass(frozen=True)
class FitConfig:
    """Fit tunables. Defaults match ``rules.tiers.local`` / ``rules.scoring`` (HDW-138)."""

    kv_type: str = "q8_0"
    overhead_gb: float = 1.0
    offload_weight_factor: float = 2.0
    ctx_steps: tuple[int, ...] = (8192, 16384, 24576, 32768, 49152, 65536)
    ctx_headroom: float = 1.2  # num_ctx must be >= headroom x estimated prompt tokens

    @classmethod
    def from_rules(cls, rules: Rules) -> FitConfig:
        local = rules.tiers.local
        return cls(
            kv_type=local.kv_cache_type,
            overhead_gb=local.overhead_gb,
            offload_weight_factor=local.offload_weight_factor,
            ctx_steps=tuple(sorted(local.ctx_steps)),
            ctx_headroom=rules.scoring.ctx_headroom,
        )


@dataclass(frozen=True)
class FitResult:
    model_id: str
    status: FitStatus
    num_ctx: int | None  # None for no_fit
    reason: str
    weights_gb: float = 0.0
    kv_gb: float = 0.0
    budget_gb: float = 0.0
    max_ctx_tokens: int = 0  # continuous maximum that fits (not rounded to a step)
    weights_calibrated: bool = False

    @property
    def usable(self) -> bool:
        return self.status != "no_fit"


def kv_bytes_per_token(arch: Arch, kv_type: str = "q8_0") -> float:
    elem = KV_BYTES_PER_ELEM.get(kv_type, KV_BYTES_PER_ELEM[DEFAULT_KV_TYPE])
    return 2.0 * arch.n_layers * arch.n_kv_heads * arch.head_dim * elem


def kv_bytes(arch: Arch, ctx: int, kv_type: str = "q8_0") -> float:
    """``2 * layers * kv_heads * head_dim * ctx * bytes(kv_type)``."""
    return kv_bytes_per_token(arch, kv_type) * ctx


def max_ctx_tokens(arch: Arch, weights_gb: float, budget_gb: float, cfg: FitConfig) -> int:
    free_bytes = (budget_gb - weights_gb - cfg.overhead_gb) * GIB
    if free_bytes <= 0:
        return 0
    return int(free_bytes // kv_bytes_per_token(arch, cfg.kv_type))


class WeightsCalibration:
    """Real weight sizes learned from ``/api/ps`` (``size`` is weights + KV + buffers)."""

    def __init__(self) -> None:
        self._weights_gb: dict[str, float] = {}
        self._lock = threading.Lock()

    def observe(
        self, model_id: str, ps_size_bytes: int, loaded_ctx: int, arch: Arch, kv_type: str
    ) -> float | None:
        """Record ``size - kv(loaded_ctx)`` as the weights size. Returns it, or None if bogus."""
        weights = (ps_size_bytes - kv_bytes(arch, loaded_ctx, kv_type)) / GIB
        if weights <= 0:
            return None
        with self._lock:
            self._weights_gb[model_id] = weights
        return weights

    def get(self, model_id: str) -> float | None:
        with self._lock:
            return self._weights_gb.get(model_id)


def _need_tokens(est_tokens: int, headroom: float) -> int:
    return math.ceil(headroom * max(0, est_tokens))


def evaluate_fit(
    entry: ModelEntry,
    budget_gb: float,
    est_tokens: int,
    cfg: FitConfig | None = None,
    calibration: WeightsCalibration | None = None,
    sysmem_flagged: bool = False,
) -> FitResult:
    """Classify ``entry`` as fits / offload / no_fit and pick ``num_ctx`` for the request."""
    cfg = cfg or FitConfig()
    mid = entry.id
    if entry.arch is None or entry.size_gb is None:
        return FitResult(mid, "no_fit", None, "missing_catalog_data", budget_gb=budget_gb)
    arch = entry.arch
    observed = calibration.get(mid) if calibration else None
    weights = observed if observed is not None else entry.size_gb * GB_TO_GIB
    calibrated = observed is not None
    need = _need_tokens(est_tokens, cfg.ctx_headroom)
    ctx_cap = entry.ctx
    steps = [s for s in cfg.ctx_steps if ctx_cap is None or s <= ctx_cap]
    cont = max_ctx_tokens(arch, weights, budget_gb, cfg)

    def result(
        status: FitStatus, ctx: int | None, reason: str, kv_ctx: int | None = None
    ) -> FitResult:
        kv_gb = kv_bytes(arch, kv_ctx or ctx or 0, cfg.kv_type) / GIB
        return FitResult(mid, status, ctx, reason, weights, kv_gb, budget_gb, cont, calibrated)

    if not steps or need > steps[-1]:
        return result("no_fit", None, "context_too_large")
    fitting = [s for s in steps if s <= cont]
    spill_ctx = next(s for s in steps if s >= need)  # smallest step that holds the request
    if sysmem_flagged:
        return result("offload", spill_ctx, "sysmem_fallback_detected")
    if fitting and fitting[-1] >= need:
        return result("fits", fitting[-1], "ok")
    if weights + cfg.overhead_gb <= budget_gb:
        return result("offload", spill_ctx, "kv_exceeds_budget")
    if weights <= budget_gb * cfg.offload_weight_factor:
        return result("offload", spill_ctx, "weights_exceed_budget")
    return result("no_fit", None, "weights_too_large")
