"""Structured results of the front-door tools (HDW-204, v3.4 B1).

These pydantic models are the tools' ``outputSchema``. Every field has a default, so an error
result (``ok`` false, ``error`` set) validates against the same schema as a success. Nothing here
holds file contents or prompt text: ``answer`` and ``diffs`` are the model's output, handed back
to the caller on purpose.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "AskResult",
    "CheckItem",
    "CheckResult",
    "ConfirmInfo",
    "DoResult",
    "ErrorInfo",
    "FileNote",
    "Relay",
    "ScopeName",
    "SuggestionOut",
    "VerifyStatus",
    "Want",
]

Relay = Literal["verbatim", "apply_diff", "summarize_ok"]
ScopeName = Literal["auto", "free", "local", "cloud"]
Want = Literal["answer", "diff", "plan"]
VerifyStatus = Literal["pass", "fail", "unchecked"]


class SuggestionOut(BaseModel):
    code: str = Field(description="Stable code such as pull_model, split_task, use_cloud.")
    text: str


class ErrorInfo(BaseModel):
    code: str = Field(description="Stable error code.")
    message: str
    fix: str = Field(default="", description="What to do next.")


class FileNote(BaseModel):
    """What happened to one requested file. Never carries the file's content."""

    path: str
    status: str = Field(
        description="included, truncated, budget, ignored, outside, invalid, missing, binary, ..."
    )
    tokens: int = 0


class CheckItem(BaseModel):
    name: str
    status: str
    detail: str = ""


class ConfirmInfo(BaseModel):
    """What needs the user's yes before a call goes ahead (HDW-206). Never holds request text."""

    reasons: list[str] = Field(description="spend and/or scope_loosening.")
    model: str | None = Field(default=None, description="The model the call would use.")
    tier: str | None = None
    estimated_cost_usd: float | None = Field(
        default=None, description="Expected spend of that call, first model only."
    )
    confirm_above_usd: float | None = Field(
        default=None, description="The user's limit (rules.yaml budget.confirm_above_usd)."
    )
    requested_scope: str | None = None
    default_scope: str | None = None
    retry_with: dict[str, bool] = Field(
        default_factory=dict,
        description="Extra arguments to repeat the same call with, but ONLY after you asked the "
        "USER and they agreed. The Private lock and project rules still apply.",
    )


class _ModelResult(BaseModel):
    """Fields shared by every tool that calls a model."""

    ok: bool = Field(description="False when the request could not be served; see error.")
    answer: str | None = Field(default=None, description="The text answer, when there is one.")
    answer_truncated: bool = Field(
        default=False, description="True when answer was cut; the full text is at result_uri."
    )
    model: str | None = Field(default=None, description="Model that answered.")
    tier: str | None = Field(default=None, description="paid, openrouter_auto, free_cloud, local.")
    scope: str | None = Field(default=None, description="Effective scope the model was picked in.")
    verified: bool = Field(
        default=False, description="True only when at least one check ran and none failed."
    )
    verify_status: VerifyStatus = Field(
        default="unchecked", description="pass, fail, or unchecked (nothing could be checked)."
    )
    verify_reasons: list[str] = Field(default_factory=list)
    below_floor: bool = Field(
        default=False, description="True when no model in scope reached the quality floor."
    )
    reasons: list[str] = Field(default_factory=list, description="Why this model was picked.")
    suggestions: list[SuggestionOut] = Field(default_factory=list)
    relay: Relay = Field(
        default="verbatim",
        description="apply_diff: apply diffs as given. verbatim: show unchanged. "
        "summarize_ok: you may summarize.",
    )
    result_uri: str | None = Field(
        default=None, description="headwaters://result/<id>: full detail, read on demand."
    )
    files: list[FileNote] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    decision_id: int | None = Field(
        default=None, description="For headwaters_feedback (thumbs up or down on this answer)."
    )
    job_id: str | None = Field(
        default=None,
        description="Set when the work runs in the background: there is no answer yet. Poll "
        "headwaters_job_status, then read headwaters_job_result.",
    )
    eta_s: float | None = Field(
        default=None, description="Expected seconds until a background job ends."
    )
    confirmation_required: bool = Field(
        default=False, description="True when the user must agree first; see confirm."
    )
    confirm: ConfirmInfo | None = None
    error: ErrorInfo | None = None


class DoResult(_ModelResult):
    diffs: list[str] = Field(default_factory=list, description="Unified diffs, ready to apply.")
    commands: list[str] = Field(default_factory=list, description="Shell commands to run.")


class AskResult(_ModelResult):
    pass


class CheckResult(BaseModel):
    ok: bool = Field(description="False when the request itself was invalid; see error.")
    verified: bool = False
    verify_status: VerifyStatus = "unchecked"
    verify_reasons: list[str] = Field(default_factory=list)
    checks: list[CheckItem] = Field(default_factory=list)
    relay: Relay = "verbatim"
    notes: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
