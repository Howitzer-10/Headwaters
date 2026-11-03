"""User model overrides on the routing path (HDW-512).

``models.overrides`` in ``rules.yaml`` is the escape hatch for a wrong catalog fact: a provider
that advertises tools while answering them as text, a context size reported too high, a score
the seed got wrong. Until now the override was folded in once, at discovery time
(``catalog/merge.py``), so a rules edit only reached a decision after a ``models refresh`` — and
a score override was only visible through the persisted column.

This module applies the override on every request, last: after the catalog facts, after the
scores and after the probe overlay. Removing an override therefore still needs a refresh to
undo the value that discovery already persisted; see the report.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import ModelOverride, Rules

__all__ = ["apply_overrides", "override_index"]


def _key(name: str) -> str:
    """Match an override key however the user wrote it: case, and a trailing ``:latest``."""
    return name.strip().lower().removesuffix(":latest")


def override_index(rules: Rules) -> dict[str, ModelOverride]:
    """Overrides keyed for lookup by model id (case-insensitive, ``:latest`` tolerated)."""
    return {_key(name): override for name, override in rules.models.overrides.items()}


def _overridden(candidate: Candidate, override: ModelOverride) -> Candidate:
    row = candidate.row
    fit = candidate.fit
    ctx_assumed = candidate.ctx_assumed
    is_session = candidate.is_session
    if override.tier is not None and override.tier != row.tier:
        row = replace(row, tier=override.tier)
        if override.tier != "local":
            # A model the user declared cloud has no local fit; keeping the old one would let a
            # stale fit decide a model that no longer runs here.
            fit = None
            is_session = False
    if override.tools_ok is not None:
        row = replace(row, tools_ok=override.tools_ok)
    if override.json_ok is not None:
        row = replace(row, json_ok=override.json_ok)
    if override.ctx is not None:
        row = replace(row, ctx=override.ctx)
        ctx_assumed = False
    scores = dict(candidate.scores)
    scores.update({task: float(score) for task, score in override.scores.items()})
    return replace(
        candidate, row=row, scores=scores, fit=fit, ctx_assumed=ctx_assumed, is_session=is_session
    )


def apply_overrides(candidates: Sequence[Candidate], rules: Rules) -> tuple[Candidate, ...]:
    """Fold ``rules.models.overrides`` into ``candidates``. Pure; one pass, no I/O."""
    if not rules.models.overrides:
        return tuple(candidates)
    index = override_index(rules)
    out: list[Candidate] = []
    for candidate in candidates:
        override = index.get(_key(candidate.id))
        out.append(candidate if override is None else _overridden(candidate, override))
    return tuple(out)
