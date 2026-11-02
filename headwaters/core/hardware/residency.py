"""Warm bonus and eviction penalty inputs (HDW-109, v3 section 7.3).

Pure functions: the router (HDW-1xx) turns these into score terms. The numeric weights are
tunable inputs, not catalog facts; defaults are a first draft.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from headwaters.core.catalog.schema import GpuProfile
from headwaters.core.rules import HardwareRules

_NO_PROFILE_MAX_LOADED = GpuProfile.model_fields["max_loaded"].default


@dataclass(frozen=True)
class LoadedModel:
    """One entry of Ollama ``/api/ps``. ``size_gb`` is what it holds on the GPU."""

    name: str
    size_gb: float


@dataclass(frozen=True)
class ResidencyConfig:
    """Defaults come from the rules model (``tiers.local.hardware``) and the catalog."""

    max_loaded_models: int = _NO_PROFILE_MAX_LOADED
    warm_bonus: float = field(default_factory=lambda: HardwareRules().warm_bonus)
    eviction_penalty_per_model: float = field(
        default_factory=lambda: HardwareRules().eviction_penalty_per_model
    )

    @classmethod
    def from_rules(cls, rules: HardwareRules, max_loaded_models: int) -> ResidencyConfig:
        return cls(max_loaded_models, rules.warm_bonus, rules.eviction_penalty_per_model)


@dataclass(frozen=True)
class ResidencyInputs:
    warm: bool
    warm_bonus: float
    evicts: tuple[str, ...]
    eviction_penalty: float


def default_max_loaded(profile: str, profiles: Mapping[str, GpuProfile] | None = None) -> int:
    """How many models Ollama keeps loaded for a GPU profile (catalog ``max_loaded``)."""
    found = (profiles or {}).get(profile)
    return found.max_loaded if found is not None else _NO_PROFILE_MAX_LOADED


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
