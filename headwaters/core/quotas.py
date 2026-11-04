"""Quota tracker and cooldowns (HDW-111, v3 section 4.4).

Turns ``WallEvent``s (HDW-110) into persisted per-scope state (``ok | cooldown | exhausted``) in the
shared SQLite ``walls`` table, keeps rpm/rpd counters per free model in ``quotas``, and answers
``availability(model) -> ok | cooldown(eta) | exhausted | soft``.

* ``rate_limit``: cooldown until the hinted reset; exponential backoff with full jitter on repeats;
  cascade at once when the wait exceeds ``walls.cascade_if_wait_over_s``.
* ``exhausted``: no retry until the reset time, a passing balance check (``poll_key``), or, when
  the provider gave no reset time, ``walls.exhausted_recheck_s`` (HDW-122). Credit walls on a
  provider or key are split by credit class: ``provider:openrouter:paid`` stops only paid models,
  ``provider:openrouter:free`` (free-tier daily cap) only free ones.
* ``overloaded``: ``walls.overloaded_max_retries`` short retries, then cascade and cool down.
* ``free_limits`` clauses (HDW-519) give the metric (``requests``, ``tokens``, …), the window
  (``minute``/``day``/``month``) and the scope that names the subject to count. Only ``requests``
  is counted today; the other metrics are documentation. Soft-exhausted at
  ``walls.soft_exhaust_pct``.

Subjects are namespaced (``model:``, ``provider:``, ``key:``) so a model id can never collide with
a provider or key alias. Only counts and timestamps are stored: never prompts, outputs or keys.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from headwaters.core import state_limits
from headwaters.core.catalog.http import HttpClient, SourceError
from headwaters.core.catalog.schema import Endpoints, FreeLimit, WallScope
from headwaters.core.rules import RulesProvider, WallsRules
from headwaters.core.scope import PAID_TIERS
from headwaters.core.state import Database
from headwaters.core.state_misc import kv_get, kv_set
from headwaters.core.walls import WallEvent

ScopeState = Literal["ok", "cooldown", "exhausted"]
AvailabilityStatus = Literal["ok", "cooldown", "exhausted", "soft"]
Action = Literal["retry", "cascade", "none"]
CreditClass = Literal["paid", "free"]

_CREDIT_CLASSES: tuple[CreditClass, ...] = ("paid", "free")
_TRANSIENT_KINDS = ("rate_limit", "overloaded")
_RPM = "rpm:"
_RPD = "rpd:"
_LIMIT_WINDOW_FORMAT: dict[str, str] = {
    "minute": "%Y-%m-%dT%H:%M",
    "hour": "%Y-%m-%dT%H",
    "day": "%Y-%m-%d",
    "month": "%Y-%m",
}
# ``requests``/minute and ``requests``/day keep the window names an older build wrote, so the
# counters already in the database (and the MCP quota view) keep reading the same rows.
_LEGACY_WINDOW_PREFIX: dict[tuple[str, str], str] = {
    ("requests", "minute"): _RPM,
    ("requests", "day"): _RPD,
}


def quota_window(metric: str, per: str, now: float) -> tuple[str, str]:
    """``(prefix, window)`` for one clause's time bucket (HDW-519)."""
    prefix = _LEGACY_WINDOW_PREFIX.get((metric, per), f"{metric}:{per}:")
    return prefix, prefix + datetime.fromtimestamp(now, UTC).strftime(_LIMIT_WINDOW_FORMAT[per])


__all__ = [
    "Availability",
    "CreditClass",
    "KeyStatus",
    "QuotaTracker",
    "WallDecision",
    "WallSubject",
    "credit_class",
    "credit_subject",
    "key_subject",
    "model_subject",
    "parse_key_status",
    "parse_wall_subject",
    "provider_name",
    "provider_subject",
    "wall_label",
]


def model_subject(model: str) -> str:
    return f"model:{model}"


def provider_subject(provider: str) -> str:
    return f"provider:{provider.lower()}"


def key_subject(alias: str) -> str:
    return f"key:{alias}"


def credit_class(tier: str) -> CreditClass:
    """Which credit a model of this tier spends: paid tiers use the key's balance, the rest the
    free allowance (local models never hit a credit wall, so they count as free)."""
    return "paid" if tier in PAID_TIERS else "free"


def credit_subject(subject: str, tier: str) -> str:
    """``provider:openrouter`` + a paid tier -> ``provider:openrouter:paid``."""
    return f"{subject}:{credit_class(tier)}"


_PROVIDER_NAMES = {
    "anthropic": "Anthropic",
    "google": "Google",
    "groq": "Groq",
    "mistral": "Mistral",
    "ollama": "Ollama",
    "openai": "OpenAI",
    "opencode": "OpenCode",
    "openrouter": "OpenRouter",
}
_SUBJECT_KINDS = ("model", "provider", "key")


@dataclass(frozen=True, slots=True)
class WallSubject:
    """A wall subject split into its parts; ``raw`` keeps the stored string unchanged."""

    kind: Literal["model", "provider", "key", "other"]
    name: str
    credit: CreditClass | None = None
    raw: str = ""


def provider_name(provider: str) -> str:
    """The display name of a provider id: ``openrouter`` -> ``OpenRouter``."""
    known = _PROVIDER_NAMES.get(provider.lower())
    if known is not None:
        return known
    return provider.replace("_", " ").replace("-", " ").strip().title() or provider


def parse_wall_subject(subject: str) -> WallSubject:
    """Split ``model:<id>``, ``provider:<name>[:paid|free]``, ``key:<alias>[:paid|free]``.

    A model id may itself contain colons (``qwen2.5-coder:14b``), so only provider and key
    subjects drop a trailing credit class.
    """
    kind, _, rest = subject.partition(":")
    if kind not in _SUBJECT_KINDS or not rest:
        return WallSubject("other", subject, raw=subject)
    name, credit = rest, None
    if kind in ("provider", "key"):
        head, sep, tail = rest.rpartition(":")
        if sep and tail in _CREDIT_CLASSES:
            name, credit = head, tail
    return WallSubject(cast(Any, kind), name, credit, subject)


def wall_label(subject: str) -> str:
    """A readable label (FU-122-4): ``provider:openrouter:paid`` -> ``OpenRouter: paid credits``."""
    parts = parse_wall_subject(subject)
    credits = "" if parts.credit is None else f": {parts.credit} credits"
    if parts.kind == "provider":
        return provider_name(parts.name) + credits
    if parts.kind == "key":
        return f'Key "{parts.name}"' + credits
    if parts.kind == "model":
        return f"Model {parts.name}"
    return subject


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
        walls: WallsRules | RulesProvider | None = None,
        free_limits: Sequence[FreeLimit] = (),
        *,
        clock: Callable[[], float] | None = None,
        rng: random.Random | None = None,
        facts: Mapping[str, float] | None = None,
    ) -> None:
        self._db = db
        # A provider is read on every use, so an edit to `walls:` applies without a restart.
        self._walls_source: Callable[[], WallsRules] = (
            (lambda: walls().walls) if callable(walls) else (lambda: walls or WallsRules())
        )
        self._limits = list(free_limits)
        # Looked up per call (not bound) so a patched ``time.time`` (freezegun) is honoured.
        self._clock: Callable[[], float] = clock or (lambda: time.time())
        self._rng = rng or random.Random()  # jitter only, not security
        # Account facts a conditional clause may read (HDW-519, D5). Unknown -> the clause is
        # dropped, so a condition can only ever add an allowance.
        self._facts = facts

    @property
    def _walls(self) -> WallsRules:
        return self._walls_source()

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
        if prior and prior.until is not None and now - prior.until > self._walls.streak_reset_s:
            state_limits.clear_walls(self._db, [subject], [kind])

    def record_wall(
        self,
        event: WallEvent,
        *,
        model: str,
        provider: str,
        key: str | None = None,
        tier: str | None = None,
    ) -> WallDecision:
        """Persist the wall and say what to do. ``context`` and ``other`` are not walls.

        ``tier`` is the tier of the model that hit the wall. A provider- or key-wide
        ``exhausted`` wall is recorded for that tier's credit class only (paid or free), so one
        spent balance cannot stop the free models. Without a tier it stays class-less and stops
        every model of the provider or key. A wall with no reset time ends after
        ``walls.exhausted_recheck_s``.
        """
        if not event.is_wall:
            return WallDecision("none", "ok")
        now = self._clock()
        cfg = self._walls
        subject = self._subject(event.scope, model, provider, key)
        if event.kind == "exhausted":
            if tier is not None and event.scope != "model":
                subject = credit_subject(subject, tier)
            reset = event.reset_at
            until = reset if reset is not None and reset > now else now + cfg.exhausted_recheck_s
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

        An ``exhausted`` wall is only lifted by a balance check, its reset time or its recheck time.
        """
        subjects = [model_subject(model), provider_subject(provider)]
        if key:
            subjects.append(key_subject(key))
        state_limits.clear_walls(self._db, subjects, _TRANSIENT_KINDS)
        self.count_request(model, provider, key)

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
    def _quota_subject(scope: str, model: str, provider: str, key: str | None) -> str:
        """The counting subject a clause's ``scope`` names (D5 rule 3).

        ``model`` is today's subject. ``organisation``/``account``/``ip`` use the provider name:
        no organisation id or client ip exists in discovery, so the provider is the closest
        observable subject (a documented approximation). ``key``/``user`` fall back to the
        provider when the caller knows no alias (the router passes none today).
        """
        if scope == "model":
            return model_subject(model)
        if scope in ("key", "user"):
            return key_subject(key) if key else provider_subject(provider)
        return provider_subject(provider)

    def _allowances(
        self, model: str, provider: str, key: str | None, now: float
    ) -> dict[tuple[str, str, str], float]:
        """``{(metric, window, subject): cap}`` for the clauses that apply now.

        Within one group the most permissive applicable clause governs (D5 rule 2), which is how
        a 50/day clause and a conditional 1000/day clause coexist after a top-up.
        """
        limit = self._limit_for(model, provider)
        if limit is None:
            return {}
        best: dict[tuple[str, str, str], float] = {}
        for clause in limit.clauses(self._facts):
            subject = self._quota_subject(clause.scope, model, provider, key)
            group = (clause.metric, clause.per, subject)
            best[group] = max(clause.max, best.get(group, 0.0))
        return best

    def count_request(self, model: str, provider: str, key: str | None = None) -> None:
        """Count one request against the allowances that apply (advisory: it never refuses).

        The router counts every request that reached the provider: successes and 429s. Only
        ``metric: requests`` is counted; ``tokens``, ``neurons`` and ``usd`` clauses are stored
        and inert, because nothing here accounts them.
        """
        now = self._clock()
        for (metric, per, subject), cap in self._allowances(model, provider, key, now).items():
            if metric != "requests":
                continue
            prefix, window = quota_window(metric, per, now)
            row = state_limits.add_quota_usage(self._db, subject, window)
            soft = row.count >= cap * self._walls.soft_exhaust_pct / 100
            state_limits.set_quota_limit(
                self._db, subject, window, limit=int(cap), soft_exhausted=soft
            )
            state_limits.prune_quota_windows(self._db, subject, prefix, window)

    def _is_soft(self, model: str, provider: str, now: float, key: str | None = None) -> bool:
        pct = self._walls.soft_exhaust_pct / 100
        for (metric, per, subject), cap in self._allowances(model, provider, key, now).items():
            if metric != "requests":
                continue
            _, window = quota_window(metric, per, now)
            row = state_limits.get_quota(self._db, subject, window)
            if row is not None and row.count >= cap * pct:
                return True
        return False

    # ------------------------------------------------------------ availability

    def availability(
        self, model: str, provider: str, key: str | None = None, tier: str | None = None
    ) -> Availability:
        """``ok | cooldown(eta) | exhausted | soft`` for one model (walls beat counters).

        With ``tier``, only the credit walls of that tier's class apply; without it every class
        does (the cautious reading for a caller that does not know the model's tier).
        """
        now = self._clock()
        shared = [provider_subject(provider)]
        if key:
            shared.append(key_subject(key))
        classes = _CREDIT_CLASSES if tier is None else (credit_class(tier),)
        subjects = {model_subject(model), *shared}
        subjects.update(f"{base}:{cls}" for base in shared for cls in classes)
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
        return (
            Availability("soft") if self._is_soft(model, provider, now, key) else Availability("ok")
        )

    # ------------------------------------------------------------ key polling

    def mark_balance_ok(self, *, provider: str, key: str | None = None) -> int:
        """A balance check passed: lift exhausted walls on the provider and key scopes.

        That covers the class-less walls and the paid-credit walls; the free-tier allowance is a
        separate wall that a balance check says nothing about.
        """
        subjects = [provider_subject(provider)]
        if key:
            subjects.append(key_subject(key))
        subjects += [credit_subject(s, "paid") for s in subjects]
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
        url: str | None = None,
    ) -> KeyStatus | None:
        """Check the OpenRouter key every ``key_poll_minutes`` (``force`` = after a paid call).

        Returns None when skipped (airplane mode, local lock, not yet due) or when the check
        failed; a failed check changes nothing. The key is only sent as a bearer header.
        """
        if airplane or local_lock:
            return None
        url = url or f"{Endpoints().openrouter_base_url}/key"
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
            # A spent balance stops the key's paid models only; free models need no credit.
            # A class-less key wall with no end would block them and never lift (HDW-119 F3).
            # Each failing poll renews the wall; if polling stops it ends after the recheck time.
            subject = credit_subject(key_subject(alias), "paid")
            until = now + self._walls.exhausted_recheck_s
            state_limits.record_wall(self._db, subject, "exhausted", until=until)
        else:
            self.mark_balance_ok(provider=provider, key=alias)
        return status
