"""Structured routing errors (HDW-114). Never carries prompt text."""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.core.policy.filters import Rejection


@dataclass(frozen=True)
class Suggestion:
    """One next step for the user or the host. ``code`` is stable; ``text`` is for people."""

    code: str  # wait | split_task | pull_model | use_cloud | enable_free_router | ...
    text: str


class NoRouteError(Exception):
    """No model can serve the request inside the effective scope.

    ``etas`` maps a model id to the seconds until it is usable again (only known resets).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        etas: dict[str, float] | None = None,
        suggestions: tuple[Suggestion, ...] = (),
        rejected: tuple[Rejection, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.etas = dict(etas or {})
        self.suggestions = suggestions
        self.rejected = rejected

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "etas": self.etas,
            "suggestions": [{"code": s.code, "text": s.text} for s in self.suggestions],
            "rejected": [{"model": r.model_id, "reason": r.reason} for r in self.rejected],
        }
