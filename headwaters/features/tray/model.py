"""What the tray shows, parsed from the daemon's own payloads (HDW-504).

Nothing here invents a name: a provider is whatever ``GET /v1/keys`` calls it, and a scope is
whatever the daemon says is in force. That is what keeps the tray, the CLI and the dashboard from
disagreeing, and it is why this module holds no provider or scope table of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["AMBER", "GREEN", "NOT_SET", "RED", "ProviderRow", "TrayState", "providers_from"]

GREEN = "green"
AMBER = "amber"
RED = "red"
# ``KeyStore.entries()`` uses this exact string for a provider with no key (core/keys.py NOT_SET).
NOT_SET = "not set"


@dataclass(frozen=True)
class ProviderRow:
    """One provider, as ``GET /v1/keys`` reports it: name, alias, where the key came from."""

    provider: str
    alias: str
    source: str

    @property
    def is_set(self) -> bool:
        return self.source != NOT_SET


def _rows(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        payload = payload.get("providers")
    return list(payload) if isinstance(payload, list) else []


def providers_from(payload: Any) -> tuple[ProviderRow, ...]:
    """Parse ``GET /v1/keys``. Only ``provider``, ``alias`` and ``source`` are read.

    ``env_var`` is not accepted at all and ``masked`` is not read: neither has any business in a
    surface, and a payload that carried them would still produce no label from them.
    """
    rows: list[ProviderRow] = []
    for row in _rows(payload):
        if not isinstance(row, Mapping):
            continue
        provider = row.get("provider")
        if not isinstance(provider, str) or not provider:
            continue
        alias = row.get("alias")
        source = row.get("source")
        rows.append(
            ProviderRow(
                provider=provider,
                alias=alias if isinstance(alias, str) and alias else provider,
                source=source if isinstance(source, str) and source else NOT_SET,
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class TrayState:
    """One reading of the daemon, and the icon colour and one-line summary that follow from it."""

    daemon_up: bool = False
    scope: str = "auto"
    effective_scope: str | None = None
    tier: str | None = None
    warm_model: str | None = None
    walls: tuple[str, ...] = ()
    last_status: str | None = None
    below_floor: bool = False
    keys_set: int = 0
    anonymous_provider: bool = False
    private_lock: bool = False
    airplane: bool = False
    providers: tuple[ProviderRow, ...] = field(default=())
    dashboard_url: str | None = None

    @classmethod
    def daemon_down(cls, *, dashboard_url: str | None = None) -> TrayState:
        return cls(daemon_up=False, dashboard_url=dashboard_url)

    @classmethod
    def from_payloads(
        cls,
        status: Mapping[str, Any],
        keys: Any = None,
        *,
        dashboard_url: str | None = None,
    ) -> TrayState:
        block = status.get("tray")
        tray: Mapping[str, Any] = block if isinstance(block, Mapping) else {}
        scope = tray.get("scope") or status.get("scope") or "auto"
        walls = tray.get("walls")
        last = tray.get("last_status")
        return cls(
            daemon_up=True,
            scope=scope if isinstance(scope, str) else "auto",
            effective_scope=_text(tray.get("effective_scope")),
            tier=_text(tray.get("tier")),
            warm_model=_text(tray.get("warm_model")),
            walls=tuple(str(wall) for wall in walls) if isinstance(walls, list) else (),
            last_status=last if isinstance(last, str) else None,
            below_floor=bool(tray.get("below_floor")),
            keys_set=_count(tray.get("keys_set")),
            anonymous_provider=bool(tray.get("anonymous_provider")),
            private_lock=bool(status.get("private_lock", tray.get("private_lock"))),
            airplane=bool(status.get("airplane", tray.get("airplane"))),
            providers=providers_from(keys),
            dashboard_url=dashboard_url,
        )

    @property
    def local(self) -> bool:
        return (self.effective_scope or self.scope) == "local"

    @property
    def color(self) -> str:
        """Green: nothing to say. Amber: something needs a look. Red: the daemon is not there,
        or the last routed request failed."""
        if not self.daemon_up:
            return RED
        if self.last_status not in (None, "ok"):
            return RED  # a failed verification and an error code are both a failed request
        if self.walls or self.below_floor:
            return AMBER
        if not self.keys_set and not self.anonymous_provider:
            return AMBER  # no key anywhere and no provider that works without one
        return GREEN

    @property
    def status_line(self) -> str:
        """The one line at the top of the menu: scope, tier, warm model."""
        if not self.daemon_up:
            return "daemon not running"
        parts = [(self.effective_scope or self.scope).capitalize()]
        if self.private_lock:
            parts.append("Private Mode")
        if self.tier:
            parts.append(self.tier)
        if self.warm_model:
            parts.append(f"{self.warm_model} warm")
        return " · ".join(parts)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
