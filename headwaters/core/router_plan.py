"""Result of a routing dry run (HDW-118): what ``Router.handle`` would do, without doing it."""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.policy.filters import Candidate, Rejection
from headwaters.core.policy.types import RouteDecision
from headwaters.core.scope import ScopeDecision

__all__ = ["RoutePlan"]


@dataclass(frozen=True)
class RoutePlan:
    """The first decision of a request plus the context needed to explain it.

    ``candidates`` carry the live wall/cooldown state the decision was made with, so a caller can
    show each fallback's tier. ``rejected`` lists the models the hard filter dropped and why
    (stable codes such as ``ctx_too_small``). Nothing here holds request text.
    """

    requirements: Requirements
    scope: ScopeDecision
    decision: RouteDecision
    candidates: tuple[Candidate, ...]
    rejected: tuple[Rejection, ...]
    session_key: str

    def candidate(self, model_id: str) -> Candidate | None:
        return next((c for c in self.candidates if c.id == model_id), None)
