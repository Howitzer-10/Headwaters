"""Candidate source: turns discovered models into policy candidates (HDW-118).

Reads the shared SQLite state (models and scores written by discovery), the catalog (speeds) and
the current hardware snapshot, and answers the router's ``CandidateSource`` question: which
models could serve a request with these requirements, and how well do they fit?

* Scores: user override, else learned average, else catalog seed (``state_models``).
* Local models: KV/fit math for this request's size, plus warm/eviction inputs from the models
  Ollama held at the last refresh.
* Walls and cooldowns are NOT applied here: the router overlays live quota state on every request.
* The user's paid primary models (``tiers.paid.models``) are flagged ``is_primary``.

Not covered yet (engines add them): OpenRouter auto/free router routes and the premium route,
API-key presence for paid models, and live ``/api/ps`` calibration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.catalog.merge import CatalogLookup
from headwaters.core.catalog.schema import Arch, Catalog, ModelEntry
from headwaters.core.hardware.fit import GB_TO_GIB, FitConfig, FitResult, evaluate_fit
from headwaters.core.hardware.residency import (
    LoadedModel,
    ResidencyConfig,
    ResidencyInputs,
    default_max_loaded,
    residency_inputs,
)
from headwaters.core.hardware.state import HardwareState
from headwaters.core.hardware.sysmem import expected_tps_for
from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import Rules
from headwaters.core.state import Database
from headwaters.core.state_models import ModelRow, effective_scores, list_models

__all__ = ["CandidateBuilder", "fit_for_row"]

_ARCH_KEYS = ("n_layers", "n_kv_heads", "head_dim")


def _entry_for(row: ModelRow) -> ModelEntry:
    """A catalog-shaped entry for the fit math, built from what discovery stored.

    ``ModelRow.size_gb`` is GiB (discovery divides Ollama's bytes by 1024^3) while
    ``evaluate_fit`` reads ``size_gb`` as decimal GB, so convert back to keep the weights exact.
    """
    arch: Arch | None = None
    if row.arch and all(isinstance(row.arch.get(k), int) and row.arch[k] > 0 for k in _ARCH_KEYS):
        arch = Arch(**{k: int(row.arch[k]) for k in _ARCH_KEYS})
    size = row.size_gb / GB_TO_GIB if row.size_gb and row.size_gb > 0 else None
    return ModelEntry(
        id=row.id,
        provider=row.provider or "ollama",
        ctx=row.ctx if row.ctx and row.ctx > 0 else None,
        size_gb=size,
        arch=arch,
    )


def fit_for_row(
    row: ModelRow, budget_gb: float, est_tokens: int, cfg: FitConfig | str
) -> FitResult:
    """Fit of one local model on ``budget_gb`` of VRAM for a request of ``est_tokens``.

    ``cfg`` is a ``FitConfig`` (from rules), or a bare KV type for the default tunables.
    """
    if isinstance(cfg, str):
        cfg = FitConfig(kv_type=cfg)
    return evaluate_fit(_entry_for(row), budget_gb, est_tokens, cfg)


class CandidateBuilder:
    """``CandidateSource`` for the router. Cheap to create; reads the database per request."""

    def __init__(
        self,
        db: Database,
        rules: Rules,
        catalog: Catalog,
        hardware: Callable[[], HardwareState | None],
    ) -> None:
        self._db = db
        self._rules = rules
        self._catalog = catalog
        self._hardware = hardware

    def __call__(self, reqs: Requirements) -> list[Candidate]:
        rows = list_models(self._db)
        scores = effective_scores(self._db)
        hw = self._hardware()
        lookup = CatalogLookup(self._catalog)
        primary = set(self._rules.tiers.paid.models)
        loaded = [
            LoadedModel(r.id, r.size_vram_gb or r.size_gb or 0.0)
            for r in rows
            if r.tier == "local" and r.warm
        ]
        out: list[Candidate] = []
        for row in rows:
            fit: FitResult | None = None
            residency: ResidencyInputs | None = None
            tps: float | None = None
            if row.tier == "local":
                fit, residency = self._local_inputs(row, reqs.est_tokens, hw, loaded)
                entry = lookup.find([row.id])
                if entry is not None and hw is not None:
                    tps = expected_tps_for(entry, hw.profile)
            # F18: a model that reports no context gets the catalog default, flagged as assumed.
            assumed = row.ctx is None or row.ctx <= 0
            shown = replace(row, ctx=self._catalog.unknown_model_defaults.ctx) if assumed else row
            out.append(
                Candidate(
                    shown,
                    scores.get(row.id, {}),
                    fit=fit,
                    residency=residency,
                    expected_tps=tps,
                    is_primary=row.id in primary,
                    ctx_assumed=assumed,
                )
            )
        return out

    def _local_inputs(
        self,
        row: ModelRow,
        est_tokens: int,
        hw: HardwareState | None,
        loaded: list[LoadedModel],
    ) -> tuple[FitResult, ResidencyInputs]:
        budget = hw.budget_gb if hw is not None else 0.0
        fit = fit_for_row(row, budget, est_tokens, FitConfig.from_rules(self._rules))
        needed = fit.weights_gb + fit.kv_gb if fit.usable else (row.size_gb or 0.0)
        config = ResidencyConfig.from_rules(
            self._rules.tiers.local.hardware,
            default_max_loaded(
                hw.profile if hw is not None else "custom", self._catalog.gpu_profiles
            ),
        )
        return fit, residency_inputs(row.id, needed, loaded, budget, config)
