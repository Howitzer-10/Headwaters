"""Warm bonus and eviction penalty inputs (HDW-109, v3 section 7.3).

Pure functions: the router (HDW-1xx) turns these into score terms. The numeric weights are
tunable inputs, not catalog facts; defaults are a first draft.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class LoadedModel:
    """One entry of Ollama ``/api/ps``. ``size_gb`` is what it holds on the GPU."""

    name: str
    size_gb: float


@dataclass(frozen=True)
class ResidencyConfig:
    max_loaded_models: int = 1  # 12 GB profile: OLLAMA_MAX_LOADED_MODELS=1
    warm_bonus: float = 0.15
    eviction_penalty_per_model: float = 0.25


@dataclass(frozen=True)
class ResidencyInputs:
    warm: bool
    warm_bonus: float
    evicts: tuple[str, ...]
    eviction_penalty: float


def default_max_loaded(profile: str) -> int:
    return 2 if profile == "desktop_16gb" else 1


def residency_inputs(
    model_name: str,
    needed_gb: float,
    loaded: Sequence[LoadedModel],
    budget_gb: float,
    cfg: ResidencyConfig | None = None,
) -> ResidencyInputs:
    """Warm models earn a bonus; loading a cold one may evict others (oldest first)."""
    cfg = cfg or ResidencyConfig()
    if any(m.name == model_name for m in loaded):
        return ResidencyInputs(True, cfg.warm_bonus, (), 0.0)
    keep = list(loaded)
    evicted: list[str] = []
    while keep and (
        len(keep) >= cfg.max_loaded_models or sum(m.size_gb for m in keep) + needed_gb > budget_gb
    ):
        evicted.append(keep.pop(0).name)
    return ResidencyInputs(
        False, 0.0, tuple(evicted), cfg.eviction_penalty_per_model * len(evicted)
    )
