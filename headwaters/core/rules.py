"""User rules model (``rules.yaml``).

A pydantic model describes every setting; unknown keys are errors so typos are reported.
Loading, defaults-on-first-run and schema export live in ``rules_io``.

Model slugs, limits and prices are NOT defaults here: they live in the catalog.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Scope = Literal["auto", "free", "local", "cloud"]
Strategy = Literal["cost_saver", "quality_first", "primary_first"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecretsRules(_Model):
    action: Literal["local", "refuse", "off"] = "local"


class PickerRules(_Model):
    show_extras: bool = False


class BudgetRules(_Model):
    paid_daily_usd: float = Field(default=5.00, ge=0)
    premium_max_usd_per_call: float = Field(default=1.00, ge=0)
    confirm_above_usd: float = Field(default=0.25, ge=0)


class PaidTier(_Model):
    models: list[str] = Field(default_factory=list)
    use_floor_variant: bool = False


class EnabledFlag(_Model):
    enabled: bool = True


class FusionRules(_Model):
    enabled: bool = True
    preset: str = "quality"


class PremiumTier(_Model):
    fusion: FusionRules = Field(default_factory=FusionRules)


class FreeCloudTier(_Model):
    ranking: Literal["catalog"] = "catalog"
    opencode_free: list[str] = Field(default_factory=list)  # empty = auto-discovered


class FreeRouterRules(_Model):
    enabled: bool = False
    max_complexity: int = Field(default=2, ge=0)


class ClassifierRules(_Model):
    model: str = "auto_small"
    num_gpu: int = Field(default=0, ge=0)
    timeout_s: float = Field(default=2, gt=0)


class LocalTier(_Model):
    host: str = "http://localhost:11434"
    profile: Literal["auto", "laptop_12gb", "desktop_16gb", "custom"] = "auto"
    reserve_gb: float | None = Field(default=None, ge=0)  # None = auto-detect
    kv_cache_type: str = "q8_0"
    keep_alive: str = "30m"
    keep_warm_model: str = "auto"
    classifier: ClassifierRules = Field(default_factory=ClassifierRules)
    on_battery: Literal["prefer_cloud", "normal", "local_only"] = "prefer_cloud"


class TiersRules(_Model):
    paid: PaidTier = Field(default_factory=PaidTier)
    openrouter_auto: EnabledFlag = Field(default_factory=EnabledFlag)
    premium: PremiumTier = Field(default_factory=PremiumTier)
    free_cloud: FreeCloudTier = Field(default_factory=FreeCloudTier)
    openrouter_free_router: FreeRouterRules = Field(default_factory=FreeRouterRules)
    local: LocalTier = Field(default_factory=LocalTier)


class FloorRule(_Model):
    base: int = Field(ge=0)
    per_complexity: int = Field(ge=0)


class FloorsRules(_Model):
    code: FloorRule = FloorRule(base=70, per_complexity=5)
    reasoning: FloorRule = FloorRule(base=70, per_complexity=6)
    chat: FloorRule = FloorRule(base=50, per_complexity=3)
    summarize: FloorRule = FloorRule(base=50, per_complexity=3)
    extract: FloorRule = FloorRule(base=55, per_complexity=3)
    vision: FloorRule = FloorRule(base=60, per_complexity=4)
    creative: FloorRule = FloorRule(base=50, per_complexity=3)
    agentic_bonus: int = Field(default=15, ge=0)
    high_risk_bonus: int = Field(default=10, ge=0)
    optimistic_margin: int = Field(default=8, ge=0)


class StrategyWeights(_Model):
    """How one strategy adjusts the score terms (HDW-113). All values are first-draft."""

    cost_weight: float = Field(ge=0)  # score points per USD of estimated request cost
    latency_mult: float = Field(default=1.0, ge=0)
    warm_mult: float = Field(default=1.0, ge=0)
    evict_mult: float = Field(default=1.0, ge=0)
    primary_bonus: float = Field(default=0.0, ge=0)  # added to the user's paid primary models


class StrategiesRules(_Model):
    cost_saver: StrategyWeights = StrategyWeights(cost_weight=400)
    quality_first: StrategyWeights = StrategyWeights(
        cost_weight=40, latency_mult=0.5, warm_mult=0.5, evict_mult=0.5
    )
    primary_first: StrategyWeights = StrategyWeights(cost_weight=0, primary_bonus=30)


class ScoringRules(_Model):
    """Policy engine weights (HDW-113). Scores are on the same 0..100 scale as the floors."""

    ctx_headroom: float = Field(default=1.2, ge=1)  # context must be >= headroom x tokens
    complexity_penalty_per_level: float = Field(default=3.0, ge=0)
    assumed_output_tokens: int = Field(default=1000, ge=0)  # for the cost estimate
    residency_scale: float = Field(default=100.0, ge=0)  # 0..1 residency inputs -> points
    latency_ref_tps: float = Field(default=30.0, gt=0)  # at or above this speed: no penalty
    latency_weight: float = Field(default=5.0, ge=0)
    latency_max_penalty: float = Field(default=20.0, ge=0)
    on_battery_local_penalty: float = Field(default=20.0, ge=0)  # local.on_battery=prefer_cloud
    gpu_busy_local_penalty: float = Field(default=15.0, ge=0)
    soft_quota_penalty: float = Field(default=10.0, ge=0)  # model near its free-tier limit
    strategies: StrategiesRules = Field(default_factory=StrategiesRules)


class WallsRules(_Model):
    cascade_if_wait_over_s: float = Field(default=8, ge=0)
    soft_exhaust_pct: float = Field(default=90, ge=0, le=100)
    # Quota tracker (HDW-111): backoff for repeated 429s, short retries for overload, key polling.
    backoff_base_s: float = Field(default=2, gt=0)
    backoff_max_s: float = Field(default=300, gt=0)
    overloaded_max_retries: int = Field(default=2, ge=0)
    overloaded_backoff_s: float = Field(default=1, gt=0)
    overloaded_cooldown_s: float = Field(default=30, ge=0)
    key_poll_minutes: float = Field(default=10, gt=0)


class GatewayRules(_Model):
    enabled: bool = True
    port: int = Field(default=4000, ge=1, le=65535)


class DashboardRules(_Model):
    enabled: bool = True
    port: int = Field(default=4001, ge=1, le=65535)


class McpRules(_Model):
    transport: Literal["stdio"] = "stdio"
    async_threshold_s: float = Field(default=20, ge=0)
    roots: Literal["auto", "off"] = "auto"


class AttributionRules(_Model):
    referer: str | None = None
    title: str = "Headwaters"


class CatalogRules(_Model):
    auto_update: bool = True
    url: str | None = None
    # When true, fetches are also off while the Private lock or local default scope is set.
    disable_when_local: bool = False


SCORE_TASKS = (
    "code",
    "reasoning",
    "chat",
    "summarize",
    "extract",
    "creative",
    "vision",
    "agentic_tools",
)


class ModelOverride(_Model):
    """Per-model user override (applied last). Scores are 0..100, like the floors."""

    tier: Literal["local", "free_cloud", "paid"] | None = None
    scores: dict[str, int] = Field(default_factory=dict)
    tools_ok: bool | None = None
    json_ok: bool | None = None
    ctx: int | None = Field(default=None, gt=0)

    @field_validator("scores")
    @classmethod
    def _scores_valid(cls, value: dict[str, int]) -> dict[str, int]:
        for task, score in value.items():
            if task not in SCORE_TASKS:
                raise ValueError(f"unknown task {task!r}; use one of {', '.join(SCORE_TASKS)}")
            if not 0 <= score <= 100:
                raise ValueError(f"score for {task!r} must be between 0 and 100")
        return value


class OpenAICompatEndpoint(_Model):
    """A free OpenAI-compatible endpoint. The key is read from an env var, never stored here."""

    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    base_url: str = Field(min_length=1)
    api_key_env: str | None = None
    models: list[str] = Field(default_factory=list)  # empty = ask GET {base_url}/models


class ModelsRules(_Model):
    refresh_ttl_s: float = Field(default=3600, ge=60)
    opencode_config: str | None = None  # explicit opencode.json path; None = standard places
    overrides: dict[str, ModelOverride] = Field(default_factory=dict)
    openai_compatible: list[OpenAICompatEndpoint] = Field(default_factory=list)


class HostsRules(_Model):
    # Host id -> True when that host runs on a subscription (advice only; Headwaters never
    # proxies or reuses subscription credentials).
    subscription: dict[str, bool] = Field(default_factory=dict)


class Rules(_Model):
    """The whole ``rules.yaml`` document."""

    version: Literal[1] = 1
    default_scope: Scope = "auto"
    strategy: Strategy = "cost_saver"
    private_lock: bool = False
    airplane: bool = False
    secrets: SecretsRules = Field(default_factory=SecretsRules)
    verify: Literal["always", "agentic", "off"] = "agentic"
    picker: PickerRules = Field(default_factory=PickerRules)
    budget: BudgetRules = Field(default_factory=BudgetRules)
    tiers: TiersRules = Field(default_factory=TiersRules)
    floors: FloorsRules = Field(default_factory=FloorsRules)
    scoring: ScoringRules = Field(default_factory=ScoringRules)
    walls: WallsRules = Field(default_factory=WallsRules)
    gateway: GatewayRules = Field(default_factory=GatewayRules)
    dashboard: DashboardRules = Field(default_factory=DashboardRules)
    mcp: McpRules = Field(default_factory=McpRules)
    attribution: AttributionRules = Field(default_factory=AttributionRules)
    telemetry: Literal[False] = False
    catalog: CatalogRules = Field(default_factory=CatalogRules)
    models: ModelsRules = Field(default_factory=ModelsRules)
    features: dict[str, bool] = Field(default_factory=dict)
    hosts: HostsRules = Field(default_factory=HostsRules)
    advice: Literal["off", "inline", "header_only"] = "off"
