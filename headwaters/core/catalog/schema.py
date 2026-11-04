"""Typed model of the curated catalog (``catalog/seed_catalog.json`` and later signed updates).

Model slugs, limits, prices, error regexes and placeholder patterns live in the catalog data,
never in code. Unknown keys are errors so typos are reported. Scores use a 0..1 scale.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Tier = Literal["paid", "free", "local"]
Role = Literal["agentic_coder", "general", "vision", "classifier"]
GpuClass = str
_DATE = r"^\d{4}-\d{2}-\d{2}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_regex(value: str) -> str:
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"invalid regular expression {value!r}: {exc}") from exc
    return value


class Scores(_Model):
    code: float | None = Field(default=None, ge=0, le=1)
    reasoning: float | None = Field(default=None, ge=0, le=1)
    chat: float | None = Field(default=None, ge=0, le=1)
    summarize: float | None = Field(default=None, ge=0, le=1)
    extract: float | None = Field(default=None, ge=0, le=1)
    creative: float | None = Field(default=None, ge=0, le=1)
    vision: float | None = Field(default=None, ge=0, le=1)
    agentic_tools: float | None = Field(default=None, ge=0, le=1)


class Arch(_Model):
    n_layers: int = Field(gt=0)
    n_kv_heads: int = Field(gt=0)
    head_dim: int = Field(gt=0)


class ModelEntry(_Model):
    id: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    tier_hints: list[Tier] = Field(default_factory=list)
    provider: str = Field(min_length=1)
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    tools_ok: bool | None = None
    json_ok: bool | None = None
    reasoning_ok: bool | None = None  # lets a catalog-only or capability-less source serve think
    ctx: int | None = Field(default=None, gt=0)
    price_in: float | None = Field(default=None, ge=0, description="USD per million tokens")
    price_out: float | None = Field(default=None, ge=0, description="USD per million tokens")
    price_date: str | None = Field(default=None, pattern=_DATE)
    size_gb: float | None = Field(default=None, gt=0)
    arch: Arch | None = None
    scores: Scores = Field(default_factory=Scores)
    expected_tps: dict[GpuClass, float] = Field(default_factory=dict)
    deprecated_by: str | None = None
    retains_data: bool = False
    unscored: bool = False
    verify: bool = False

    @field_validator("expected_tps")
    @classmethod
    def _tps_positive(cls, value: dict[GpuClass, float]) -> dict[GpuClass, float]:
        for gpu, tps in value.items():
            if tps <= 0:
                raise ValueError(f"expected_tps[{gpu}] must be positive")
        return value

    @model_validator(mode="after")
    def _price_needs_date(self) -> Self:
        if (self.price_in is not None or self.price_out is not None) and not self.price_date:
            raise ValueError("prices require price_date")
        return self


LimitMetric = Literal["requests", "tokens", "neurons", "usd"]
LimitWindow = Literal["minute", "hour", "day", "month"]
LimitScope = Literal["model", "key", "user", "organisation", "account", "ip"]
LimitLicence = Literal["unstated", "commercial_ok", "non_commercial_only"]
# A closed set: a fact belongs here only when something can observe it. Add a member with a test.
LimitFact = Literal["account_topup_usd_total"]
LimitTokenCount = Literal["input+output", "output"]


class LimitCondition(_Model):
    """A condition on an *extra* allowance: when it holds the clause is added, never removed."""

    fact: LimitFact
    op: Literal[">=", ">", "=="]
    value: float

    def holds(self, facts: Mapping[str, float] | None) -> bool:
        """Whether the fact was observed and clears the condition (an unknown fact does not)."""
        if facts is None or self.fact not in facts:
            return False
        seen = facts[self.fact]
        if self.op == ">=":
            return seen >= self.value
        if self.op == ">":
            return seen > self.value
        return seen == self.value


class LimitClause(_Model):
    """One allowance: a metric, a cap, a window and the subject it is counted against (D5).

    Only ``metric: "requests"`` is counted (``quotas``); ``tokens``, ``neurons`` and ``usd`` are
    stored, validated and inert, because nothing in this codebase accounts them. Rate limits are
    advisory, not enforced: a counted request ranks a model down, it never refuses a call.
    """

    metric: LimitMetric
    max: float = Field(gt=0)
    per: LimitWindow
    scope: LimitScope
    tokens: LimitTokenCount | None = None
    condition: LimitCondition | None = None
    verify: bool = False
    note: str | None = None

    def applies(self, facts: Mapping[str, float] | None = None) -> bool:
        return self.condition is None or self.condition.holds(facts)


class FreeLimit(_Model):
    """A provider's free allowance. ``rpm``/``rpd`` are the legacy spelling (D5 rule 1).

    Rate limits are advisory, not enforced. ``limits`` empty means "legacy row: read rpm/rpd";
    ``licence`` is documentation only, because no request carries a commercial-use intent.
    """

    provider: str = Field(min_length=1)
    model: str | None = None
    rpm: int | None = Field(default=None, gt=0)
    rpd: int | None = Field(default=None, gt=0)
    verify: bool = False
    limits: list[LimitClause] = Field(default_factory=list)
    licence: LimitLicence = "unstated"
    source_url: str | None = Field(default=None, pattern=r"^https://\S+$")
    note: str | None = None

    def clauses(self, facts: Mapping[str, float] | None = None) -> list[LimitClause]:
        """The allowances that apply now: the explicit clauses, else ``rpm``/``rpd``.

        A legacy row resolves to ``scope: model`` because today's counter subject *is* the
        model: the conversion preserves behaviour, it does not bless it (D5 rule 1). A
        conditional clause is dropped while its fact is unknown, so it can only add allowance.
        """
        return [c for c in (self.limits or self._legacy_clauses()) if c.applies(facts)]

    def _legacy_clauses(self) -> list[LimitClause]:
        out: list[LimitClause] = []
        if self.rpm is not None:
            out.append(LimitClause(metric="requests", max=self.rpm, per="minute", scope="model"))
        if self.rpd is not None:
            out.append(LimitClause(metric="requests", max=self.rpd, per="day", scope="model"))
        return out


WallKind = Literal["rate_limit", "exhausted", "overloaded", "context", "other"]
WallScope = Literal["model", "provider", "key"]


class WallPattern(_Model):
    """One wall signal. ``provider`` is a provider id or ``*`` (any provider)."""

    provider: str = Field(min_length=1)
    status_codes: list[int] = Field(default_factory=list)
    body_regex: str | None = None
    kind: WallKind
    scope: WallScope
    verify: bool = False
    source: str | None = None  # where the status code / body text was taken from
    checked: str | None = Field(default=None, pattern=_DATE)  # date the source was read

    @field_validator("body_regex")
    @classmethod
    def _regex_ok(cls, value: str | None) -> str | None:
        return None if value is None else _check_regex(value)

    @model_validator(mode="after")
    def _has_matcher(self) -> Self:
        if not self.status_codes and self.body_regex is None:
            raise ValueError("a wall pattern needs status_codes or body_regex")
        return self


class NamedPattern(_Model):
    name: str = Field(min_length=1)
    regex: str
    verify: bool = False

    @field_validator("regex")
    @classmethod
    def _regex_ok(cls, value: str) -> str:
        return _check_regex(value)


class GpuProfile(_Model):
    vram_gb: float = Field(gt=0)
    recommended: dict[Role, list[str]] = Field(default_factory=dict)
    # HDW-151: total VRAM (GB, inclusive) that selects this profile, and Ollama's resident models.
    vram_range_gb: tuple[float, float] | None = None
    max_loaded: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _range_ordered(self) -> Self:
        if self.vram_range_gb is not None and self.vram_range_gb[0] > self.vram_range_gb[1]:
            raise ValueError("vram_range_gb must be [low, high] with low <= high")
        return self


class Endpoints(_Model):
    """Provider URLs that can move without a release (HDW-151)."""

    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", min_length=1)


ProviderTier = Literal["free_cloud", "paid"]
# HDW-519/D1: ``paid`` answered two questions at once. These are the two answers, and they are
# labels rather than tier names: no tier name is added, removed or renamed by this split.
FreeAccess = Literal["none", "marked", "all", "plan"]


class ProviderLabels(_Model):
    """The split pair: whether money may change hands, and how a free path exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    charges_money: bool
    free_access: FreeAccess


def labels_for_tier(tier: ProviderTier, honor_free_markers: bool = False) -> ProviderLabels:
    """Today's reading of one ``tier`` (plus markers), kept as the derivation for old rows.

    An unmigrated catalog is legal, not broken (D2): both fields absent derives exactly today's
    behaviour, so discovery, ``ProviderInfo`` and quota counting do not move.
    """
    if tier == "free_cloud":
        return ProviderLabels(charges_money=False, free_access="all")
    markers = "marked" if honor_free_markers else "none"
    return ProviderLabels(charges_money=True, free_access=markers)


FREE_TO_USE = "free to use"
SELLS_USAGE = "sells usage"
SELLS_MARKED = "sells usage \u00b7 free models marked"
SELLS_PLAN = "sells usage \u00b7 free on the free plan"


def format_access(charges_money: bool, free_access: FreeAccess) -> str:
    """The one label formatter (D1.5, D18): every surface prints this string, not its own."""
    if not charges_money:
        return FREE_TO_USE
    if free_access == "marked":
        return SELLS_MARKED
    if free_access == "plan":
        return SELLS_PLAN
    return SELLS_USAGE


# Names the router already uses for providers that are wired in code (or never routed).
# ``opencode`` left this set in HDW-515: it is a catalog row now (Console, key required).
RESERVED_PROVIDER_NAMES = frozenset({"anthropic", "openai", "openrouter", "ollama"})


class ProviderEntry(_Model):
    """One OpenAI-compatible cloud provider, registered as data (HDW-514).

    Adding a provider is a catalog entry plus a test: the key store, ``keys test``, discovery and
    the executor all read this row. ``tier`` is what an unmarked model of the provider costs:
    ``paid`` keeps every model out of the free scope unless the provider's own listing marks it
    free and ``honor_free_markers`` is on. HDW-519 splits that one word into ``charges_money``
    and ``free_access``, which ``labels()`` reads and ``format_access`` renders; ``tier`` stays
    as the derivation fallback and the routing vocabulary. Every row names its docs page and the
    day it was read.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    env_var: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    base_url: str = Field(pattern=r"^https://\S+$")
    auth_style: Literal["bearer"] = "bearer"
    metadata_url: str | None = Field(default=None, pattern=r"^https://\S+$")
    models_path: str = Field(default="/models", pattern=r"^/\S*$")
    # HDW-515: some providers list models outside the chat root (OpenCode: chat on
    # ``/inference/openai/v1``, listing on ``/inference/v1/models``). A full URL here wins over
    # ``base_url + models_path``; it is never guessed, only read from the provider's docs.
    models_endpoint: str | None = Field(default=None, pattern=r"^https://\S+$")
    tier: ProviderTier
    honor_free_markers: bool = False
    # HDW-519/D1: the fact ``tier`` alone could not state, so a reviewer read "paid" as "no free
    # path". Absent means "derive from tier" (D2); the seed states both on every row.
    charges_money: bool | None = None
    free_access: FreeAccess | None = None
    key_required: bool = True
    skip_id_pattern: str | None = None
    # HDW-515 amendment: a provider that routes a call by conversation requires this header on
    # every chat request (OpenCode Go answers 400 MissingSessionID without it, so a plain
    # bearer-style call can never succeed). The header name is data; the value is the request's
    # stable session key, set by the executor. ``None`` means the provider needs no such header.
    session_header: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")
    retains_data: bool | None = None
    free_notes: str | None = None
    source_url: str = Field(pattern=r"^https://\S+$")
    checked: str = Field(pattern=_DATE)
    verify: bool = False
    # D4: a row that could not be verified says which probe could not be run, so a reader knows
    # the difference between "checked and wrong" and "not checked".
    note: str | None = None

    @field_validator("skip_id_pattern")
    @classmethod
    def _skip_is_regex(cls, value: str | None) -> str | None:
        return value if value is None else _check_regex(value)

    def labels(self) -> ProviderLabels:
        """The split pair: stated by the row, else derived from ``tier`` exactly as before (D2)."""
        charges = self.tier == "paid" if self.charges_money is None else self.charges_money
        access = self.free_access
        if access is None:
            access = labels_for_tier(self.tier, self.honor_free_markers).free_access
        return ProviderLabels(charges_money=charges, free_access=access)

    @property
    def models_url(self) -> str:
        if self.models_endpoint is not None:
            return self.models_endpoint
        return self.base_url.rstrip("/") + self.models_path

    def refuses(self, model_id: str) -> bool:
        """Whether ``skip_id_pattern`` covers this upstream id.

        Discovery uses it to drop ids the provider has for its own client only; the executor uses
        it so such a model is refused with a clear error instead of being called for a 403
        (HDW-515, defence in depth — no model slug is ever hard-coded).
        """
        return (
            self.skip_id_pattern is not None
            and re.search(self.skip_id_pattern, model_id) is not None
        )


class SubscriptionRate(_Model):
    plan: str = Field(min_length=1)
    usd_per_month: float = Field(ge=0)
    included_credits: float | None = Field(default=None, ge=0)
    price_date: str = Field(pattern=_DATE)
    verify: bool = False


class UnknownModelDefaults(_Model):
    """Conservative values given to a model the catalog does not know."""

    scores: Scores
    tools_ok: bool = False
    json_ok: bool = False
    ctx: int = Field(gt=0)


ANALYZER_TASKS = ("code", "reasoning", "chat", "summarize", "extract", "creative")


class AnalyzerHints(_Model):
    """Data for the deterministic analyzer (HDW-106): keywords, agent user-agents, thresholds.

    Every list is data so it can be updated without a release. Regexes are matched
    case-insensitively against the last user message. All defaults are empty or neutral.
    """

    agentic_system_prompt_tokens: int = Field(default=1500, gt=0)
    confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    chars_per_token_code: float = Field(default=3.5, gt=0)
    chars_per_token_prose: float = Field(default=4.0, gt=0)
    image_tokens: int = Field(default=800, ge=0)
    # HDW-151: scan window, short-prompt cut-off, task-score bonuses, classifier limits.
    keyword_scan_head_chars: int = Field(default=4000, gt=0)
    keyword_scan_tail_chars: int = Field(default=4000, gt=0)
    short_prompt_tokens: int = Field(default=100, ge=0)
    image_bonus: int = Field(default=3, ge=0)
    json_bonus: int = Field(default=2, ge=0)
    fence_bonus: int = Field(default=2, ge=0)
    classifier_max_prompt_chars: int = Field(default=2000, ge=2)
    classifier_min_confidence: float = Field(default=0.75, ge=0, le=1)
    complexity_token_steps: list[int] = Field(default_factory=lambda: [2000, 8000])
    agent_user_agents: list[str] = Field(default_factory=list)  # case-insensitive substrings
    reasoning_keywords: list[str] = Field(default_factory=list)
    duck_keywords: list[str] = Field(default_factory=list)
    risk_keywords: list[str] = Field(default_factory=list)
    task_keywords: dict[str, list[str]] = Field(default_factory=dict)
    fence_aliases: dict[str, str] = Field(default_factory=dict)
    extension_languages: dict[str, str] = Field(default_factory=dict)  # ".py" -> "python"

    @field_validator("reasoning_keywords", "duck_keywords", "risk_keywords")
    @classmethod
    def _regexes_ok(cls, value: list[str]) -> list[str]:
        for item in value:
            _check_regex(item)
        return value

    @field_validator("task_keywords")
    @classmethod
    def _task_keywords_ok(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for task, patterns in value.items():
            if task not in ANALYZER_TASKS:
                raise ValueError(f"unknown task {task!r}; use one of {', '.join(ANALYZER_TASKS)}")
            for item in patterns:
                _check_regex(item)
        return value

    @field_validator("extension_languages")
    @classmethod
    def _extensions_ok(cls, value: dict[str, str]) -> dict[str, str]:
        for ext in value:
            if not ext.startswith(".") or len(ext) < 2:
                raise ValueError(f"extension {ext!r} must start with '.'")
        return value


class Catalog(_Model):
    schema_version: int = Field(ge=1)
    catalog_version: str = Field(min_length=1)
    models: list[ModelEntry] = Field(default_factory=list)
    unknown_model_defaults: UnknownModelDefaults
    free_limits: list[FreeLimit] = Field(default_factory=list)
    wall_patterns: list[WallPattern] = Field(default_factory=list)
    placeholder_patterns: list[NamedPattern] = Field(default_factory=list)
    ollama_remote_patterns: list[NamedPattern] = Field(default_factory=list)
    gpu_profiles: dict[str, GpuProfile] = Field(default_factory=dict)
    secret_patterns: list[NamedPattern] = Field(default_factory=list)
    secret_placeholders: list[NamedPattern] = Field(default_factory=list)
    # HDW-203: gitignore-syntax lines (secret/env files) always excluded from files-by-path.
    default_ignore_patterns: list[str] = Field(default_factory=list)
    subscription_rates: list[SubscriptionRate] = Field(default_factory=list)
    analyzer_hints: AnalyzerHints = Field(default_factory=AnalyzerHints)
    endpoints: Endpoints = Field(default_factory=Endpoints)
    providers: list[ProviderEntry] = Field(default_factory=list)
    prompt_bank_ref: str | None = None

    @model_validator(mode="after")
    def _cross_references(self) -> Self:
        problems: list[str] = []
        seen_names: set[str] = set()
        # A repeated ``env_var`` is allowed (HDW-515: ``opencode`` and ``opencode-go`` both read
        # one key): the key store keeps a record per provider name, so the share is safe.
        for provider in self.providers:
            if provider.name in seen_names:
                problems.append(f"providers: name {provider.name!r} is listed twice")
            seen_names.add(provider.name)
            if provider.name in RESERVED_PROVIDER_NAMES:
                problems.append(f"providers: {provider.name!r} is a reserved provider name")
        names: dict[str, int] = {}
        for index, entry in enumerate(self.models):
            for name in {entry.id.lower(), *(a.lower() for a in entry.aliases)}:
                if name in names:
                    other = self.models[names[name]].id
                    problems.append(f"name {name!r} is used by {other!r} and {entry.id!r}")
                names.setdefault(name, index)
        ids = {m.id for m in self.models}
        for entry in self.models:
            if entry.deprecated_by is not None and entry.deprecated_by not in ids:
                problems.append(f"{entry.id}: deprecated_by {entry.deprecated_by!r} is unknown")
        for profile_name, profile in self.gpu_profiles.items():
            for role, model_ids in profile.recommended.items():
                for model_id in model_ids:
                    if model_id not in ids:
                        problems.append(f"gpu_profiles.{profile_name}.{role}: unknown {model_id!r}")
        if problems:
            raise ValueError("; ".join(problems))
        return self
