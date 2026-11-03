"""Typed model of the curated catalog (``catalog/seed_catalog.json`` and later signed updates).

Model slugs, limits, prices, error regexes and placeholder patterns live in the catalog data,
never in code. Unknown keys are errors so typos are reported. Scores use a 0..1 scale.
"""

from __future__ import annotations

import re
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


class FreeLimit(_Model):
    provider: str = Field(min_length=1)
    model: str | None = None
    rpm: int | None = Field(default=None, gt=0)
    rpd: int | None = Field(default=None, gt=0)
    verify: bool = False


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
# Names the router already uses for providers that are wired in code (or never routed).
RESERVED_PROVIDER_NAMES = frozenset({"anthropic", "openai", "openrouter", "ollama", "opencode"})


class ProviderEntry(_Model):
    """One OpenAI-compatible cloud provider, registered as data (HDW-514).

    Adding a provider is a catalog entry plus a test: the key store, ``keys test``, discovery and
    the executor all read this row. ``tier`` is what an unmarked model of the provider costs:
    ``paid`` keeps every model out of the free scope unless the provider's own listing marks it
    free and ``honor_free_markers`` is on. Every row names its docs page and the day it was read.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    env_var: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    base_url: str = Field(pattern=r"^https://\S+$")
    auth_style: Literal["bearer"] = "bearer"
    metadata_url: str | None = Field(default=None, pattern=r"^https://\S+$")
    models_path: str = Field(default="/models", pattern=r"^/\S*$")
    tier: ProviderTier
    honor_free_markers: bool = False
    key_required: bool = True
    skip_id_pattern: str | None = None
    retains_data: bool | None = None
    free_notes: str | None = None
    source_url: str = Field(pattern=r"^https://\S+$")
    checked: str = Field(pattern=_DATE)
    verify: bool = False

    @field_validator("skip_id_pattern")
    @classmethod
    def _skip_is_regex(cls, value: str | None) -> str | None:
        return value if value is None else _check_regex(value)

    @property
    def models_url(self) -> str:
        return self.base_url.rstrip("/") + self.models_path


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
        seen_env: dict[str, str] = {}
        for provider in self.providers:
            if provider.name in seen_names:
                problems.append(f"providers: name {provider.name!r} is listed twice")
            seen_names.add(provider.name)
            if provider.name in RESERVED_PROVIDER_NAMES:
                problems.append(f"providers: {provider.name!r} is a reserved provider name")
            if provider.env_var in seen_env:
                problems.append(
                    f"providers: {provider.env_var} is used by {seen_env[provider.env_var]!r} "
                    f"and {provider.name!r}"
                )
            seen_env.setdefault(provider.env_var, provider.name)
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
