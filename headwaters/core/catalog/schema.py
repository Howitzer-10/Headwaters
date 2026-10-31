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
    subscription_rates: list[SubscriptionRate] = Field(default_factory=list)
    analyzer_hints: AnalyzerHints = Field(default_factory=AnalyzerHints)
    prompt_bank_ref: str | None = None

    @model_validator(mode="after")
    def _cross_references(self) -> Self:
        problems: list[str] = []
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
