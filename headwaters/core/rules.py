"""User rules model (``rules.yaml``).

A pydantic model describes every setting; unknown keys are errors so typos are reported.
Loading, defaults-on-first-run and schema export live in ``rules_io``.

Model slugs, limits and prices are NOT defaults here: they live in the catalog.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from headwaters.core.duration import normalize_keep_alive, parse_duration_s

Scope = Literal["auto", "free", "local", "cloud"]
Strategy = Literal["cost_saver", "quality_first", "primary_first"]
TierOrder = Literal["interleave", "descend"]
TierDirection = Literal["cloud_first", "local_first"]

# One registry per closed set of names (D18): the schema, the CLI and the MCP descriptions read
# these instead of retyping the names, so a new value cannot drift between them.
STRATEGIES: tuple[Strategy, ...] = get_args(Strategy)
TIER_ORDERS: tuple[TierOrder, ...] = get_args(TierOrder)
TIER_DIRECTIONS: tuple[TierDirection, ...] = get_args(TierDirection)


def tier_order_label(order: TierOrder, direction: TierDirection) -> str:
    """How the surfaces print the order: ``interleave`` or ``descend (cloud_first)``."""
    return order if order == "interleave" else f"{order} ({direction})"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


FooterField = Literal["model", "tier", "tokens", "cost", "latency"]
FOOTER_FIELDS: tuple[FooterField, ...] = ("model", "tier", "tokens", "cost", "latency")


class FooterRules(_Model):
    """HDW-512: the optional answer footer. Off by default, so no host sees extra body text.

    ``fields`` names the facts to print, in order; the gateway renders only these, so adding a
    fact is one formatter and one name here. The footer is never part of ``usage``.
    """

    enabled: bool = False
    fields: list[FooterField] = Field(default_factory=lambda: list(FOOTER_FIELDS))


class SecretsRules(_Model):
    action: Literal["local", "refuse", "off"] = "local"
    # HDW-142: joined and encoded forms. All bounds are rules, not code constants.
    scan_join: bool = True
    scan_decode: bool = True
    scan_join_window_chars: int = Field(default=2048, ge=64)
    scan_join_span: int = Field(default=2, ge=1, le=8)
    scan_base64_min_chars: int = Field(default=24, ge=8)
    scan_base64_max_chars: int = Field(default=16384, ge=64)
    scan_percent_max_chars: int = Field(default=262144, ge=64)
    scan_max_candidates: int = Field(default=256, ge=1)
    scan_max_decoded_bytes: int = Field(default=1048576, ge=1024)
    scan_budget_ms: int = Field(default=2000, ge=50)


class PickerRules(_Model):
    show_extras: bool = False


class ToolsRules(_Model):
    """Tool-schema handling (HDW-511).

    ``prune_to_fit`` drops non-headwaters tool schemas to fit a local model when the
    request failed only because the schemas did not fit its context. Off by default:
    dropping tools changes what the model on the other end can do.
    """

    prune_to_fit: bool = False


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


class HardwareRules(_Model):
    """Local GPU tunables (HDW-151): VRAM reserve, busy check, residency and sysmem thresholds."""

    reserve_display_gb: float = Field(default=2.0, ge=0)  # monitor on the RTX
    reserve_unknown_gb: float = Field(default=2.5, ge=0)  # display status unknown
    reserve_no_display_gb: float = Field(default=1.0, ge=0)  # iGPU drives the display
    busy_threshold_pct: float = Field(default=50.0, ge=0, le=100)
    warm_bonus: float = Field(default=0.15, ge=0)
    eviction_penalty_per_model: float = Field(default=0.25, ge=0)
    sysmem_slow_ratio: float = Field(default=0.5, gt=0, le=1)  # measured/expected below this
    sysmem_spill_tolerance: float = Field(default=0.02, ge=0, lt=1)


class LocalTier(_Model):
    host: str = "http://localhost:11434"
    profile: Literal["auto", "laptop_12gb", "desktop_16gb", "custom"] = "auto"
    reserve_gb: float | None = Field(default=None, ge=0)  # None = auto-detect
    kv_cache_type: str = "q8_0"
    keep_alive: str = Field(
        default="30m",
        description=(
            "How long Ollama keeps the model resident: a Go duration ('30m', '120s'). A bare "
            "number is stored as seconds ('120' -> '120s') because Ollama rejects a quoted "
            "number."
        ),
    )
    keep_warm_model: str = "off"
    # Keep-warm is opt-in (D21): ``off`` disables the tick, so a model unloads after
    # ``keep_alive`` once the last request is done. ``auto`` re-arms whatever ``/api/ps`` already
    # lists (it never loads one) and keeps doing so forever once a model has been used, which is
    # what holds VRAM; any other value names the model to keep warm.
    # HDW-518 adds an opt-in heartbeat mode on top: a configurable interval, idle backoff, and a
    # ``/api/ps`` ``expires_at`` on the result. ``0`` means derive the interval
    # (``keep_alive``/2) / never back off.
    keep_warm_heartbeat: bool = False
    heartbeat_interval_s: float = Field(default=0.0, ge=0)
    heartbeat_idle_backoff_s: float = Field(default=0.0, ge=0)
    classifier: ClassifierRules = Field(default_factory=ClassifierRules)
    on_battery: Literal["prefer_cloud", "normal", "local_only"] = "prefer_cloud"
    # Fit math (HDW-138): num_ctx steps, VRAM overhead, and how far weights may spill.
    ctx_steps: list[int] = Field(
        default_factory=lambda: [8192, 16384, 24576, 32768, 49152, 65536], min_length=1
    )
    overhead_gb: float = Field(default=1.0, ge=0)
    offload_weight_factor: float = Field(default=2.0, ge=1)
    hardware: HardwareRules = Field(default_factory=HardwareRules)

    @field_validator("keep_alive", mode="before")
    @classmethod
    def _keep_alive_is_text(cls, value: object) -> object:
        """HDW-522: a bare YAML number is not a form we accept; name the form that works."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        raise ValueError(
            "tiers.local.keep_alive must be a duration string with a unit: write '120s' "
            "(or '30m'), not a bare number of seconds"
        )

    @field_validator("keep_warm_model", mode="before")
    @classmethod
    def _keep_warm_model_accepts_yaml_booleans(cls, value: object) -> object:
        """D21: a bare ``off``/``on`` is YAML ``false``/``true``, not text; say what was meant.

        ``off`` has exactly one meaning, so ``false`` (``off``, ``no``) disables the tick.
        ``on`` means "keep warm", and ``auto`` is the only spelling that says so without
        inventing a model name, so ``true`` (``on``, ``yes``) selects it.
        """
        if not isinstance(value, bool):
            return value
        return "off" if value is False else "auto"

    @field_validator("keep_alive", mode="after")
    @classmethod
    def _keep_alive_carries_a_unit(cls, value: str) -> str:
        """HDW-522: Ollama 400s a quoted bare number, so ``120`` is stored as ``120s``."""
        return normalize_keep_alive(value)

    @model_validator(mode="after")
    def _heartbeat_fits_the_window(self) -> LocalTier:
        """D4/HDW-518: a heartbeat must ping faster than the model expires, and never clamp.

        ``keep_alive`` that is not a positive duration (``-1`` = stay resident, ``0`` = unload at
        once, stored as ``-1s`` / ``0s``) is not an error: the tick already skips with
        ``keep_alive_not_timed``.
        """
        if not self.keep_warm_heartbeat:
            return self
        window = parse_duration_s(self.keep_alive)
        if window is None or window <= 0:
            return self
        if window <= 60:
            raise ValueError(
                "tiers.local.keep_warm_heartbeat needs keep_alive > 60s "
                f"(keep_alive is {self.keep_alive!r}): raise keep_alive or turn the heartbeat off"
            )
        if self.heartbeat_interval_s and self.heartbeat_interval_s >= window:
            raise ValueError(
                "tiers.local.heartbeat_interval_s must be shorter than tiers.local.keep_alive "
                f"({self.heartbeat_interval_s:g}s is not under {window:g}s): lower the interval "
                "or raise keep_alive"
            )
        return self


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
    # Tasks whose answers can be checked mechanically (a JSON request always counts too).
    verifiable_tasks: list[str] = Field(default_factory=lambda: ["code"])


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
    # Points per complexity level above 1, scaled by (100 - quality) / 100 (HDW-133).
    complexity_penalty_per_level: float = Field(default=10.0, ge=0)
    offload_penalty: float = Field(default=30.0, ge=0)  # local model spills to system RAM (slow)
    assumed_output_tokens: int = Field(default=1000, ge=0)  # for the cost estimate
    residency_scale: float = Field(default=100.0, ge=0)  # 0..1 residency inputs -> points
    latency_ref_tps: float = Field(default=30.0, gt=0)  # at or above this speed: no penalty
    latency_weight: float = Field(default=5.0, ge=0)
    latency_max_penalty: float = Field(default=20.0, ge=0)
    on_battery_local_penalty: float = Field(default=20.0, ge=0)  # local.on_battery=prefer_cloud
    gpu_busy_local_penalty: float = Field(default=15.0, ge=0)
    soft_quota_penalty: float = Field(default=10.0, ge=0)  # model near its free-tier limit
    session_bonus: float = Field(default=5.0, ge=0)  # the session's last good model, any tier (D11)
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
    # HDW-122: an exhausted wall with no reset time ends (the model is tried again) after this.
    exhausted_recheck_s: float = Field(default=900, gt=0)
    # HDW-151: a wall that ended this long ago no longer counts as "repeated" for the backoff.
    streak_reset_s: float = Field(default=3600, gt=0)


class ExecutorRules(_Model):
    """Timeouts in seconds. The first-token limit is longer for a cold local model, because the
    model must be loaded (and the prompt processed) before anything streams."""

    cloud_first_token_s: float = Field(default=30, gt=0)
    local_warm_first_token_s: float = Field(default=60, gt=0)
    local_cold_first_token_s: float = Field(default=240, gt=0)
    stream_idle_s: float = Field(default=60, gt=0)  # max silence between tokens once streaming
    connect_s: float = Field(default=5, gt=0)
    default_effort: str = Field(default="medium", min_length=1)  # reasoning switched on (True)


class RouterRules(_Model):
    # Bounds every model call of one request (retries, cascades and escalations together).
    max_attempts: int = Field(default=4, ge=1)
    # HDW-516: how the fallbacks are ordered. ``interleave`` round-robins the tiers (HDW-513,
    # the default and unchanged); ``descend`` exhausts one tier before the next, walking the
    # tiers in ``tier_direction`` order.
    tier_order: TierOrder = "interleave"
    tier_direction: TierDirection = "cloud_first"
    # HDW-517: when a draft fails a real check, retry once on a strictly lower tier. Off by
    # default, so an enabled deployment has to opt in and accept the wider gate below.
    verify_cascade: bool = False
    # Empty = choose the target from the descending order. Set = that model or no cascade at
    # all, so a named fallback never turns into an unnamed one.
    verify_cascade_model: str = ""
    # Which task classes may cascade. Kept to the analyzer's task names rather than inventing a
    # second vocabulary.
    verify_cascade_tasks: list[str] = Field(default_factory=lambda: ["code"])


class GatewayRules(_Model):
    enabled: bool = True
    port: int = Field(default=4000, ge=1, le=65535)
    footer: FooterRules = Field(default_factory=lambda: FooterRules())


class DashboardRules(_Model):
    enabled: bool = True
    port: int = Field(default=4001, ge=1, le=65535)


class McpRules(_Model):
    transport: Literal["stdio"] = "stdio"
    async_threshold_s: float = Field(default=20, ge=0)
    roots: Literal["auto", "off"] = "auto"
    # HDW-146: a finished background job's row (done, failed or cancelled) is deleted this many
    # days after it last changed. The answer itself is never in SQLite.
    job_retention_days: float = Field(default=7, gt=0)


class WorkspaceRules(_Model):
    # Absolute folders that bound file access (D9). Empty = the shim's folder is the boundary.
    roots: list[str] = Field(default_factory=list)


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
    tools_ok: StrictBool | None = None  # HDW-512: a typo must not silently claim a capability
    json_ok: StrictBool | None = None
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


class ToolProbeRules(_Model):
    """HDW-512: behavioural tool-capability verification (``headwaters models verify-tools``).

    Off the request path by design: a probe loads the model, so it is an explicit command and
    never a per-request check. ``on_refresh`` is opt-in and off by default, even for
    ``models refresh``, which is why the observation is stored and overlaid at routing time.
    """

    enabled: bool = True
    on_refresh: bool = False
    retry_after_s: float = Field(default=86400, ge=0)
    max_models: int = Field(default=25, ge=1)
    max_tokens: int = Field(default=256, ge=1)


class ModelsRules(_Model):
    refresh_ttl_s: float = Field(default=3600, ge=60)
    opencode_config: str | None = None  # explicit opencode.json path; None = standard places
    overrides: dict[str, ModelOverride] = Field(default_factory=dict)
    tool_probe: ToolProbeRules = Field(default_factory=ToolProbeRules)
    openai_compatible: list[OpenAICompatEndpoint] = Field(default_factory=list)
    # HDW-304: the alias table. An IDE sends the model names its own picker lists; a key here
    # maps one to what the user means, either a picker model (``headwaters/free+verify``) or a
    # catalog model id. Keys match case-insensitively.
    aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("aliases")
    @classmethod
    def _aliases_usable(cls, value: dict[str, str]) -> dict[str, str]:
        for name, target in value.items():
            if not name.strip() or not target.strip():
                raise ValueError("an alias needs a non-empty model name and target")
            if any(ch.isspace() for ch in target.strip()):
                raise ValueError(f"alias {name!r} has whitespace in its target {target!r}")
        return value


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
    tools: ToolsRules = Field(default_factory=ToolsRules)
    budget: BudgetRules = Field(default_factory=BudgetRules)
    tiers: TiersRules = Field(default_factory=TiersRules)
    floors: FloorsRules = Field(default_factory=FloorsRules)
    scoring: ScoringRules = Field(default_factory=ScoringRules)
    walls: WallsRules = Field(default_factory=WallsRules)
    executor: ExecutorRules = Field(default_factory=ExecutorRules)
    router: RouterRules = Field(default_factory=RouterRules)
    gateway: GatewayRules = Field(default_factory=GatewayRules)
    dashboard: DashboardRules = Field(default_factory=DashboardRules)
    mcp: McpRules = Field(default_factory=McpRules)
    workspace: WorkspaceRules = Field(default_factory=WorkspaceRules)
    attribution: AttributionRules = Field(default_factory=AttributionRules)
    telemetry: Literal[False] = False
    catalog: CatalogRules = Field(default_factory=CatalogRules)
    models: ModelsRules = Field(default_factory=ModelsRules)
    features: dict[str, bool] = Field(default_factory=dict)
    hosts: HostsRules = Field(default_factory=HostsRules)
    advice: Literal["off", "inline", "header_only"] = "off"

    @model_validator(mode="after")
    def _cascade_is_usable(self) -> Rules:
        """HDW-517: reject a cascade that could never fire, or that contradicts the ordering."""
        router = self.router
        for task in router.verify_cascade_tasks:
            if task not in SCORE_TASKS:
                raise ValueError(
                    f"router.verify_cascade_tasks has unknown task {task!r}; "
                    f"use one of {', '.join(SCORE_TASKS)}"
                )
        if not router.verify_cascade:
            return self
        if router.tier_order != "descend":
            raise ValueError(
                "router.verify_cascade needs router.tier_order: descend, because interleave "
                "can repeat a tier before descending; set tier_order: descend or turn "
                "verify_cascade off"
            )
        if not router.verify_cascade_tasks:
            raise ValueError(
                "router.verify_cascade_tasks is empty, so the cascade could never fire; list "
                "at least one task or set router.verify_cascade: false"
            )
        # D7: a cascade may only move toward the cheap end. Under ``local_first`` the walk starts
        # at the cheap end already, so "lower" means a paid tier and the row would record money
        # spent in ``cascade_to``, a field that is not the escalation one precisely because it
        # means "spent less" (HDW-517).
        if router.tier_direction != "cloud_first":
            raise ValueError(
                "router.verify_cascade needs router.tier_direction: cloud_first, because a "
                "cascade may only move toward the cheap end; under local_first the walk already "
                "starts there and the next tier charges money, so set tier_direction: "
                "cloud_first or turn verify_cascade off"
            )
        if router.verify_cascade_model:
            # The tier of an arbitrary catalog model is not known to the rules document; an
            # override is. The runtime strictly-lower check covers the rest (HDW-517 report).
            override = self.models.overrides.get(router.verify_cascade_model)
            top = "paid"  # the top of the only walk a cascade may use (cloud_first, above)
            if override is not None and override.tier == top:
                raise ValueError(
                    f"router.verify_cascade_model {router.verify_cascade_model!r} is in the "
                    f"{top!r} tier, the top of the configured walk, so nothing is lower; name "
                    "a model in a lower tier or leave the key empty"
                )
        return self


RulesProvider = Callable[[], Rules]
"""Returns the live rules. Long-running engines read it once per request (HDW-131)."""


def as_provider(source: Rules | RulesProvider) -> RulesProvider:
    """A provider for a fixed ``Rules`` (tests, the CLI) or an existing provider.

    The daemon passes ``RulesManager.current`` so an edit to ``rules.yaml`` (``headwaters private
    on``) reaches a running router without a restart.
    """
    if isinstance(source, Rules):
        return lambda: source
    return source
