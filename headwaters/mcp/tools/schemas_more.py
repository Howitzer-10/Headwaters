"""Structured results of the second wave of front-door tools (HDW-205, v3.4 B1).

Same rules as ``schemas.py``: every field has a default, so an error result (``ok`` false,
``error`` set) validates against the same schema. Nothing here holds prompt text, file contents
or reasoning text.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from headwaters.mcp.tools.schemas import AskResult, ErrorInfo, FileNote, SuggestionOut

__all__ = [
    "AdvancedToolInfo",
    "FeedbackResult",
    "GpuInfoOut",
    "HandoffPacket",
    "HandoffResult",
    "HostSwitch",
    "JobResult",
    "LocalModelInfo",
    "LocalModelsResult",
    "MoreResult",
    "PinResult",
    "QuotaLine",
    "QuotaResult",
    "RecommendResult",
    "ScopeInfo",
    "ScopeResult",
    "StatusResult",
    "SwitchHostResult",
    "ThinkResult",
    "WallInfo",
    "WarmModel",
]


class ThinkResult(AskResult):
    """``headwaters_think``: the final answer and routing metadata. Never the reasoning text."""


class WallInfo(BaseModel):
    subject: str
    kind: str
    eta_s: float | None = Field(default=None, description="Seconds until it ends, if known.")
    consecutive: int = 0
    # FU-122-4: the same wall, in words: "OpenRouter: paid credits" instead of
    # "provider:openrouter:paid".
    label: str = Field(default="", description="Readable label for the subject.")
    # FU-205-9: the provider and the credit the wall stops, told apart. A key subject used to
    # carry its alias and its credit in one opaque string.
    provider: str | None = Field(default=None, description="Provider the wall names, if known.")
    credit: str | None = Field(default=None, description="Key scope: paid or free credits.")


class ScopeInfo(BaseModel):
    default_scope: str | None = None
    strategy: str | None = None
    # HDW-516: the fallback order, "interleave" or "descend (cloud_first)". Hand-edited in
    # rules.yaml (router.tier_order / router.tier_direction); no tool changes it.
    tier_order: str | None = Field(default=None, description="Fallback order label.")
    tier_direction: str | None = None
    effective: str | None = Field(default=None, description="Scope a plain request gets now.")
    tiers: list[str] = Field(default_factory=list)
    locks: list[str] = Field(default_factory=list)
    private_lock: bool = False
    airplane: bool = False
    project_rule: str | None = None
    error: ErrorInfo | None = None


class GpuInfoOut(BaseModel):
    name: str | None = None
    total_gb: float | None = None
    budget_gb: float | None = None
    profile: str | None = None
    on_battery: bool = False
    busy: bool = False
    local_suggestion: str | None = None


class WarmModel(BaseModel):
    model: str
    vram_gb: float | None = None


class StatusResult(BaseModel):
    ok: bool = True
    scope: ScopeInfo = Field(default_factory=ScopeInfo)
    walls: list[WallInfo] = Field(default_factory=list)
    gpu: GpuInfoOut | None = None
    warm_models: list[WarmModel] = Field(default_factory=list)
    pinned_model: str | None = None
    savings: dict[str, Any] | None = Field(
        default=None, description="Present only when the ledger feature is on."
    )
    catalog: str | None = None
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class ScopeResult(BaseModel):
    ok: bool
    changed: list[str] = Field(default_factory=list, description="Settings that changed.")
    scope: ScopeInfo = Field(default_factory=ScopeInfo)
    confirmation_required: bool = Field(
        default=False, description="True when loosening needs the user; nothing was changed."
    )
    user_command: str | None = Field(
        default=None, description="The command the USER runs to confirm. Never run it yourself."
    )
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class HandoffPacket(BaseModel):
    goal: str = ""
    files: list[dict[str, str]] = Field(default_factory=list, description="path, sha256, role")
    last_error: str = ""
    recent_turns: list[str] = Field(default_factory=list)
    scope: str | None = None
    model: str | None = None


class HandoffResult(BaseModel):
    ok: bool
    packet_id: int | None = None
    packet: HandoffPacket | None = None
    files: list[FileNote] = Field(default_factory=list)
    redacted: int = Field(default=0, description="Secret-shaped strings removed from the packet.")
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class AdvancedToolInfo(BaseModel):
    name: str
    summary: str


class MoreResult(BaseModel):
    ok: bool = True
    tools: list[AdvancedToolInfo] = Field(default_factory=list)
    enabled: bool = Field(default=False, description="True when they now appear in tools/list.")
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class HostSwitch(BaseModel):
    host: str
    command: str = Field(description="What the user runs or types to switch the host's model.")
    model_id: str = Field(description="The model id as that host names it.")


class RecommendResult(BaseModel):
    ok: bool
    model: str | None = None
    tier: str | None = None
    scope: str | None = None
    below_floor: bool = False
    reasons: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    switch: list[HostSwitch] = Field(default_factory=list)
    suggestions: list[SuggestionOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class SwitchHostResult(BaseModel):
    ok: bool
    switch: bool = False
    severity: str = Field(default="none", description="none, info or warn.")
    reason: str = ""
    recommended: RecommendResult | None = None
    handoff_packet_id: int | None = None
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class LocalModelInfo(BaseModel):
    model: str
    fit: str = Field(description="fits, offload, no_fit or unknown.")
    num_ctx: int | None = None
    max_ctx: int | None = None
    size_gb: float | None = None
    warm: bool = False
    expected_tps: float | None = None
    thinking: bool | None = None


class LocalModelsResult(BaseModel):
    ok: bool = True
    gpu: GpuInfoOut | None = None
    models: list[LocalModelInfo] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class QuotaLine(BaseModel):
    model: str
    status: str
    eta_s: float | None = None
    rpm_used: int = 0
    rpm_limit: int | None = None
    rpd_used: int = 0
    rpd_limit: int | None = None
    # FU-205-9: local models are listed too, with the tier and whether the model is warm.
    tier: str | None = None
    warm: bool | None = None


class QuotaResult(BaseModel):
    ok: bool = True
    walls: list[WallInfo] = Field(default_factory=list)
    models: list[QuotaLine] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class JobResult(BaseModel):
    ok: bool
    job_id: str | None = None
    status: str | None = Field(
        default=None, description="queued, running, done, failed or cancelled."
    )
    progress: float | None = Field(default=None, description="0 to 1; never goes down.")
    message: str | None = Field(default=None, description="What the job is doing now.")
    tool: str | None = Field(default=None, description="Which tool started the job.")
    result: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class PinResult(BaseModel):
    ok: bool
    pinned_model: str | None = None
    previous: str | None = None
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class FeedbackResult(BaseModel):
    ok: bool
    recorded: bool = False
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
