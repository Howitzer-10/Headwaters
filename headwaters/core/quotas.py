"""Quota tracker and cooldowns (HDW-111, v3 section 4.4).

Turns ``WallEvent``s (HDW-110) into persisted per-scope state (``ok | cooldown | exhausted``) in the
shared SQLite ``walls`` table, keeps rpm/rpd counters per free model in ``quotas``, and answers
``availability(model) -> ok | cooldown(eta) | exhausted | soft``.

* ``rate_limit``: cooldown until the hinted reset; exponential backoff with full jitter on repeats;
  cascade at once when the wait exceeds ``walls.cascade_if_wait_over_s``.
* ``exhausted``: no retry until a balance check passes (``poll_key``) or the reset time.
* ``overloaded``: ``walls.overloaded_max_retries`` short retries, then cascade and cool down.
* rpm/rpd limits come from catalog ``free_limits``; soft-exhausted at ``walls.soft_exhaust_pct``.

Subjects are namespaced (``model:``, ``provider:``, ``key:``) so a model id can never collide with
a provider or key alias. Only counts and timestamps are stored: never prompts, outputs or keys.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from headwaters.core import state_limits
from headwaters.core.catalog.http import HttpClient, SourceError
from headwaters.core.catalog.schema import FreeLimit, WallScope
from headwaters.core.rules import WallsRules
from headwaters.core.state import Database
from headwaters.core.state_misc import kv_get, kv_set
from headwaters.core.walls import WallEvent

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
ScopeState = Literal["ok", "cooldown", "exhausted"]
AvailabilityStatus = Literal["ok", "cooldown", "exhausted", "soft"]
Action = Literal["retry", "cascade", "none"]

_TRANSIENT_KINDS = ("rate_limit", "overloaded")
_STALE_AFTER_S = 3600.0  # a wall that ended this long ago no longer counts as "repeated"
_RPM = "rpm:"
_RPD = "rpd:"

__all__ = [
    "OPENROUTER_KEY_URL",
    "Availability",
    "KeyStatus",
    "QuotaTracker",
    "WallDecision",
    "key_subject",
    "model_subject",
    "parse_key_status",
    "provider_subject",
]


def model_subject(model: str) -> str:
    return f"model:{model}"


def provider_subject(provider: str) -> str:
    return f"provider:{provider.lower()}"


def key_subject(alias: str) -> str:
    return f"key:{alias}"


@dataclass(frozen=True, slots=True)
class Availability:
    """Answer to ``availability(model)``. ``eta`` is seconds until retry is sensible, if known."""

    status: AvailabilityStatus
    eta: float | None = None
    subject: str | None = None  # the wall subject that decided it, if any

    @property
    def usable(self) -> bool:
        """``soft`` models are still usable, just ranked down."""
        return self.status in ("ok", "soft")


@dataclass(frozen=True, slots=True)
class WallDecision:
    """What to do after recording a wall: retry the same target after ``wait_s``, or cascade."""

    action: Action
    state: ScopeState
    subject: str | None = None
    wait_s: float = 0.0
    until: float | None = None
    consecutive: int = 0


@dataclass(frozen=True, slots=True)
class KeyStatus:
    """Parsed ``GET /api/v1/key`` answer (only what the tracker needs)."""

    exhausted: bool
    limit_remaining: float | None = None
    usage: float | None = None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def parse_key_status(payload: Any) -> KeyStatus:
    """OpenRouter key info: exhausted when the key's credit is spent or its blocking budget is."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise SourceError("unexpected key-status response")
    remaining = _number(data.get("limit_remaining"))
    exhausted = remaining is not None and remaining <= 0
    budget = data.get("effective_budget")
    if isinstance(budget, dict):
        left = _number(budget.get("remaining_usd"))
        if left is not None and left <= 0:
            exhausted = True
    return KeyStatus(exhausted, remaining, _number(data.get("usage")))


class QuotaTracker:
    """Persisted wall state and request counters. Cheap to create; all state is in ``db``."""

    def __init__(
        self,
        db: Database,
        walls: WallsRules | None = None,
        free_limits: Sequence[FreeLimit] = (),
        *,
        clock: Callable[[], float] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._db = db
        self._walls = walls or WallsRules()
        self._limits = list(free_limits)
        # Looked up per call (not bound) so a patched ``time.time`` (freezegun) is honoured.
        self._clock: Callable[[], float] = clock or (lambda: time.time())
        self._rng = rng or random.Random()  # jitter only, not security

    # ------------------------------------------------------------------ walls

    @staticmethod
    def _subject(scope: WallScope, model: str, provider: str, key: str | None) -> str:
        if scope == "provider":
            return provider_subject(provider)
        if scope == "key":
            return key_subject(key) if key else provider_subject(provider)
        return model_subject(model)

    def _jitter(self, base: float, attempt: int, cap: float) -> float:
        """Full jitter: uniform(0, min(cap, base * 2^(attempt-1)))."""
        ceiling = min(cap, base * (2 ** max(0, min(attempt, 32) - 1)))
        return self._rng.uniform(0.0, ceiling)

    def _forget_stale(self, subject: str, kind: str, now: float) -> None:
        """Drop a long-finished wall so an old streak does not inflate the backoff."""
        prior = state_limits.get_wall(self._db, subject, kind)
        if prior and prior.until is not None and now - prior.until > _STALE_AFTER_S:
            state_limits.clear_walls(self._db, [subject], [kind])

    def record_wall(
        self, event: WallEvent, *, model: str, provider: str, key: str | None = None
    ) -> WallDecision:
        """Persist the wall and say what to do. ``context`` and ``other`` are not walls."""
        if not event.is_wall:
            return WallDecision("none", "ok")
        now = self._clock()
        cfg = self._walls
        subject = self._subject(event.scope, model, provider, key)
        if event.kind == "exhausted":
            until = event.reset_at if event.reset_at and event.reset_at > now else None
            row = state_limits.record_wall(self._db, subject, "exhausted", until=until)
            return WallDecision("cascade", "exhausted", subject, 0.0, until, row.consecutive_count)
        self._forget_stale(subject, event.kind, now)
        prior = state_limits.get_wall(self._db, subject, event.kind)
        attempt = (prior.consecutive_count if prior else 0) + 1
        hint = event.retry_after or 0.0
        if event.kind == "rate_limit":
            wait = max(hint, self._jitter(cfg.backoff_base_s, attempt, cfg.backoff_max_s))
            action: Action = "cascade" if wait > cfg.cascade_if_wait_over_s else "retry"
        elif attempt <= cfg.overloaded_max_retries:
            wait = max(hint, self._jitter(cfg.overloaded_backoff_s, attempt, cfg.backoff_max_s))
            action = "cascade" if wait > cfg.cascade_if_wait_over_s else "retry"
        else:
            wait = max(hint, cfg.overloaded_cooldown_s)
            action = "cascade"
        until = now + wait
        row = state_limits.record_wall(self._db, subject, event.kind, until=until)
        return WallDecision(action, "cooldown", subject, wait, until, row.consecutive_count)

    def record_success(self, *, model: str, provider: str, key: str | None = None) -> None:
        """A good call ends transient walls (and their streak) and counts a request.

        An ``exhausted`` wall is only lifted by a balance check or its reset time.
        """
        subjects = [model_subject(model), provider_subject(provider)]
        if key:
            subjects.append(key_subject(key))
        state_limits.clear_walls(self._db, subjects, _TRANSIENT_KINDS)
        self.count_request(model, provider)

    # --------------------------------------------------------------- counters

    def _limit_for(self, model: str, provider: str) -> FreeLimit | None:
        """Catalog limit for a model: model-specific wins, else the provider-wide entry.

        A provider-wide limit (catalog ``model: null``) applies to each model separately,
        since free-tier limits are per model.
        """
        wanted = provider.lower()
        fallback: FreeLimit | None = None
        for entry in self._limits:
            if entry.provider.lower() != wanted:
                continue
            if entry.model == model:
                return entry
            if entry.model is None and fallback is None:
                fallback = entry
        return fallback

    @staticmethod
    def _windows(now: float) -> tuple[str, str]:
        moment = datetime.fromtimestamp(now, UTC)
        return _RPM + moment.strftime("%Y-%m-%dT%H:%M"), _RPD + moment.strftime("%Y-%m-%d")

    def count_request(self, model: str, provider: str) -> None:
        """Count one request against the model's rpm/rpd (no-op without a catalog limit)."""
        limit = self._limit_for(model, provider)
        if limit is None:
            return
        subject = model_subject(model)
        rpm_window, rpd_window = self._windows(self._clock())
        pct = self._walls.soft_exhaust_pct / 100
        for prefix, window, cap in ((_RPM, rpm_window, limit.rpm), (_RPD, rpd_window, limit.rpd)):
            row = state_limits.add_quota_usage(self._db, subject, window)
            soft = cap is not None and row.count >= cap * pct
            state_limits.set_quota_limit(self._db, subject, window, limit=cap, soft_exhausted=soft)
            state_limits.prune_quota_windows(self._db, subject, prefix, window)

    def _is_soft(self, model: str, provider: str, now: float) -> bool:
        limit = self._limit_for(model, provider)
        if limit is None:
            return False
        subject = model_subject(model)
        rpm_window, rpd_window = self._windows(now)
        pct = self._walls.soft_exhaust_pct / 100
        for window, cap in ((rpm_window, limit.rpm), (rpd_window, limit.rpd)):
            row = state_limits.get_quota(self._db, subject, window)
            if cap is not None and row is not None and row.count >= cap * pct:
                return True
        return False

    # ------------------------------------------------------------ availability

    def availability(self, model: str, provider: str, key: str | None = None) -> Availability:
        """``ok | cooldown(eta) | exhausted | soft`` for one model (walls beat counters)."""
        now = self._clock()
        subjects = {model_subject(model), provider_subject(provider)}
        if key:
            subjects.add(key_subject(key))
        rows = [r for r in state_limits.active_walls(self._db, now) if r.subject in subjects]
        hard = [r for r in rows if r.kind == "exhausted"]
        if hard:
            ends = [r.until for r in hard]
            eta = None if None in ends else max(u for u in ends if u is not None) - now
            return Availability("exhausted", eta, hard[0].subject)
        cooling = [r for r in rows if r.kind in _TRANSIENT_KINDS and r.until is not None]
        if cooling:
            last = max(cooling, key=lambda r: r.until or 0.0)
            return Availability("cooldown", max(0.0, (last.until or 0.0) - now), last.subject)
        return Availability("soft") if self._is_soft(model, provider, now) else Availability("ok")

    # ------------------------------------------------------------ key polling

    def mark_balance_ok(self, *, provider: str, key: str | None = None) -> int:
        """A balance check passed: lift exhausted walls on the provider and key scopes."""
        subjects = [provider_subject(provider)]
        if key:
            subjects.append(key_subject(key))
        return state_limits.clear_walls(self._db, subjects, ["exhausted"])

    def _poll_due(self, alias: str, now: float) -> bool:
        last = kv_get(self._db, f"quota.key_poll.{alias}")
        interval = self._walls.key_poll_minutes * 60
        return not isinstance(last, int | float) or now - float(last) >= interval

    def poll_key(
        self,
        http: HttpClient,
        api_key: str,
        *,
        alias: str,
        provider: str = "openrouter",
        force: bool = False,
        airplane: bool = False,
        local_lock: bool = False,
        url: str = OPENROUTER_KEY_URL,
    ) -> KeyStatus | None:
        """Check the OpenRouter key every ``key_poll_minutes`` (``force`` = after a paid call).

        Returns None when skipped (airplane mode, local lock, not yet due) or when the check
        failed; a failed check changes nothing. The key is only sent as a bearer header.
        """
        if airplane or local_lock:
            return None
        now = self._clock()
        if not force and not self._poll_due(alias, now):
            return None
        try:
            status = parse_key_status(
                http.request_json("GET", url, headers={"Authorization": f"Bearer {api_key}"})
            )
        except SourceError:
            return None
        kv_set(self._db, f"quota.key_poll.{alias}", now)
        if status.exhausted:
            state_limits.record_wall(self._db, key_subject(alias), "exhausted", until=None)
        else:
            self.mark_balance_ok(provider=provider, key=alias)
        return status
