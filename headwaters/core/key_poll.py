"""Early lift of a spent paid wall by a balance check (HDW-131, FU-122-2).

``QuotaTracker.poll_key`` is the only way a paid ``exhausted`` wall ends before its recheck time.
This module finds the keys that have such a wall and polls each one. It acts on the paid class
only: a ``:free`` wall is never looked at, because a balance check says nothing about it.

It runs under the same blocking rule as model discovery (``network_block``): airplane mode, the
Private lock and a local default scope send nothing at all.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from headwaters.core import state_limits
from headwaters.core.catalog.discovery import network_block
from headwaters.core.catalog.http import HttpClient
from headwaters.core.quotas import (
    QuotaTracker,
    key_subject,
    provider_subject,
)
from headwaters.core.rules import Rules
from headwaters.core.state import Database

__all__ = ["KeyPollResult", "PaidWall", "paid_walls", "poll_paid_walls"]

# The balance endpoint (``{openrouter_base_url}/key``, from the catalog) belongs to OpenRouter,
# the only provider whose
# balance this checks. A key wall without a provider is read as OpenRouter's.
POLLED_PROVIDER = "openrouter"
_PAID = ":paid"
_KEY = key_subject("")
_PROVIDER = provider_subject("")


@dataclass(frozen=True, slots=True)
class PaidWall:
    """A spent paid wall: the key alias (or provider name) to check and the provider it is on."""

    alias: str
    provider: str


@dataclass(frozen=True, slots=True)
class KeyPollResult:
    blocked: str | None = None  # why nothing was polled, when the network rule said so
    polled: tuple[str, ...] = ()
    lifted: tuple[str, ...] = ()


def paid_walls(db: Database, now: float | None = None) -> list[PaidWall]:
    """Active ``exhausted`` walls on ``key:<alias>:paid`` or ``provider:<p>:paid``."""
    found: dict[str, PaidWall] = {}
    for row in state_limits.active_walls(db, now):
        if row.kind != "exhausted" or not row.subject.endswith(_PAID):
            continue
        base = row.subject[: -len(_PAID)]
        if base.startswith(_KEY) and len(base) > len(_KEY):
            alias = base[len(_KEY) :]
            found.setdefault(alias, PaidWall(alias, POLLED_PROVIDER))
        elif base.startswith(_PROVIDER) and len(base) > len(_PROVIDER):
            provider = base[len(_PROVIDER) :]
            found.setdefault(provider, PaidWall(provider, provider))
    return [found[name] for name in sorted(found)]


def poll_paid_walls(
    db: Database,
    quotas: QuotaTracker,
    http: HttpClient,
    rules: Rules,
    key_for: Callable[[str], str | None],
    *,
    now: float | None = None,
    key_url: str | None = None,
) -> KeyPollResult:
    """Poll each key that has a paid wall. ``rules`` is the live rules, read by the caller.

    ``key_for(provider)`` returns the secret to send (never logged, never stored), or None to skip
    that key. Providers other than OpenRouter are skipped: no balance endpoint is known for them.
    """
    blocked = network_block(rules)
    if blocked is not None:
        return KeyPollResult(blocked=blocked)
    at = time.time() if now is None else now
    polled: list[str] = []
    lifted: list[str] = []
    for wall in paid_walls(db, at):
        if wall.provider != POLLED_PROVIDER:
            continue
        secret = key_for(wall.provider)
        if not secret:
            continue
        status = quotas.poll_key(
            http,
            secret,
            alias=wall.alias,
            provider=wall.provider,
            airplane=rules.airplane,
            local_lock=rules.private_lock,
            url=key_url,
        )
        if status is None:
            continue
        polled.append(wall.alias)
        if not status.exhausted:
            lifted.append(wall.alias)
    return KeyPollResult(None, tuple(polled), tuple(lifted))
