"""Executor errors (HDW-115). They never carry prompt or output text."""

from __future__ import annotations

from collections.abc import Mapping

from headwaters.core.walls import WallEvent


class ExecutorError(Exception):
    """A call failed. ``wall`` is the wall classifier's reading of a provider response.

    ``before_first_token`` tells callers whether failing over to another model is still safe
    (nothing was streamed yet). Messages are short fixed text, never provider or user text.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        wall: WallEvent | None = None,
        status: int | None = None,
        provider: str = "",
        model: str = "",
        before_first_token: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.wall = wall
        self.status = status
        self.provider = provider
        self.model = model
        self.before_first_token = before_first_token

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "wall": None if self.wall is None else self.wall.kind,
            "before_first_token": self.before_first_token,
        }


class ScopeViolation(ExecutorError):
    """The target is not allowed by the effective scope (or by airplane mode)."""

    def __init__(self, message: str, *, model: str = "", code: str = "scope_violation") -> None:
        super().__init__(code, message, model=model)


class LocalScopeViolation(ScopeViolation):
    """A local-scoped call would leave the machine (non-loopback Ollama host or a cloud tier)."""

    def __init__(self, message: str, *, model: str = "") -> None:
        super().__init__(message, model=model, code="local_scope_violation")


class ProviderError(Exception):
    """Raw failure from a backend; the executor turns it into an ``ExecutorError``.

    ``body`` is only matched against wall patterns, never stored or logged.
    """

    def __init__(
        self,
        status: int,
        headers: Mapping[str, str] | None = None,
        body: str = "",
        *,
        kind: str = "http",  # http | timeout | connection | stream_error
    ) -> None:
        super().__init__(f"{kind} {status}")
        self.status = status
        self.headers: Mapping[str, str] = dict(headers or {})
        self.body = body
        self.kind = kind
