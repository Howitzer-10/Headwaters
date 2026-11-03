"""Behavioural tool-capability verification (HDW-512).

``models.tools_ok`` came only from what a provider advertises. Ollama advertises ``tools`` for
models that answer a tool call as plain JSON *text*; a host then shows that text to the user as a
raw fenced block instead of running the tool. This module asks one model to call a trivial
synthetic tool and records what actually came back.

Timing, deliberately: verification never runs on a request path and never inside the background
TTL refresh. A probe has to load a model (up to the executor's cold-start bound), so it runs from
``headwaters models verify-tools`` or ``models refresh --verify-tools``. The observation is
stored and overlaid on candidates at routing time, so a later refresh cannot resurrect a
capability that was measured as broken.

A skipped or failed probe records nothing: an unverified model keeps whatever it had, and never
becomes ``tools_ok=1`` by accident.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from headwaters.core.analyzer.requirements import HardRequirements, Requirements, SoftRequirements
from headwaters.core.engine import remember_candidates
from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.types import ExecOptions, ExecResult, Message, ToolSpec
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.scoring import ScoreBreakdown
from headwaters.core.policy.types import RouteDecision
from headwaters.core.rules import Rules, Scope
from headwaters.core.scope import LOCAL_TIERS, ScopeDecision, TierName
from headwaters.core.state import Database
from headwaters.core.state_models import ModelRow, set_model_tools_ok
from headwaters.core.state_probes import CAPABILITY_TOOLS, ProbeRow, get_probe, record_probe

__all__ = [
    "DETAIL_CALL_AS_TEXT",
    "DETAIL_EMPTY",
    "DETAIL_STRUCTURED",
    "DETAIL_STRUCTURED_WITH_TEXT",
    "DETAIL_TEXT_ANSWER",
    "DETAIL_WRONG_TOOL",
    "PROBE_TOOL_NAME",
    "ExecutorLike",
    "ProbeOutcome",
    "classify",
    "probe_scope",
    "probe_tool_spec",
    "verify_model",
    "verify_models",
]

PROBE_TOOL_NAME = "headwaters_probe"

PROBE_PROMPT = (
    "Call the headwaters_probe tool right now with reason set to 'capability check'. "
    "Use the tool; do not answer in text."
)

DETAIL_STRUCTURED = "structured"  # matching structured call, empty content: what we want
DETAIL_STRUCTURED_WITH_TEXT = "structured_with_text"  # matching call, plus prose
DETAIL_WRONG_TOOL = "wrong_tool"  # a structured call, but not the offered tool
DETAIL_CALL_AS_TEXT = "call_as_text"  # the defect: the call arrives as JSON text
DETAIL_TEXT_ANSWER = "text_answer"  # prose, no call at all
DETAIL_EMPTY = "empty"  # nothing came back

# A probe needs room for the prompt and one small tool schema, nothing more. It feeds the
# candidate builder so a local call gets a real ``num_ctx`` instead of a refusal.
PROBE_EST_TOKENS = 2_048


def probe_requirements(est_tokens: int = PROBE_EST_TOKENS) -> Requirements:
    """The tiny tool request a probe makes, in the shape the candidate builder wants."""
    return Requirements(
        hard=HardRequirements(vision=False, tools=True, json=False, min_ctx=0),
        soft=SoftRequirements(
            task="chat",
            complexity=1,
            risk="low",
            agentic=False,
            languages=(),
            reasoning_hint=False,
            duck_hint=False,
        ),
        confidence=1.0,
        source="tool probe",
        est_tokens=est_tokens,
        ambiguous=False,
    )


# Detail codes for outcomes that must not touch what is stored (``ok`` stays None).
SKIPPED_PREFIX = "skipped:"
ERROR_PREFIX = "error:"

DETAIL_TRUNCATED = f"{ERROR_PREFIX}truncated"  # cut off at the token cap: not an observation

_FENCE = re.compile(r"^```[A-Za-z0-9_-]*\s*|\s*```$")
_NAME_KEYS = ("name", "tool", "tool_name", "function")


# The probe only ever awaits ``run(..., stream=False)``. ``Executor`` satisfies it, and so does
# the small fake the tests pass, so the alias names the contract without importing the executor
# here (the probe module stays free of the transport layer).
ExecutorLike = Any


@dataclass(frozen=True)
class ProbeOutcome:
    """What one probe concluded. ``ok`` is None when nothing definitive was observed."""

    model: str
    detail: str
    ok: bool | None = None
    previous: bool | None = None  # last stored observation, for a skipped re-check

    @property
    def recorded(self) -> bool:
        return self.ok is not None


def probe_tool_spec() -> ToolSpec:
    """The synthetic tool the model is asked to call. Deliberately trivial and side-effect free."""
    return {
        "type": "function",
        "function": {
            "name": PROBE_TOOL_NAME,
            "description": "Report that the model can call tools. Does nothing else.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why the tool was called."}
                },
                "required": ["reason"],
            },
        },
    }


def _json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in ``text``, with a surrounding markdown fence removed."""
    body = _FENCE.sub("", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _text_call_name(text: str) -> str | None:
    """The tool name when ``text`` is a tool call written as text, else None.

    Covers the shapes seen in the wild: ``{"name": ..., "arguments": {...}}``,
    ``{"tool": ...}``, and a ``{"tool_calls": [{...}]}`` wrapper. A JSON answer that names no
    tool is not a call.
    """
    parsed = _json_object(text)
    if parsed is None:
        return None
    calls = parsed.get("tool_calls")
    if isinstance(calls, list) and calls and isinstance(calls[0], dict):
        parsed = calls[0]
        inner = parsed.get("function")
        if isinstance(inner, dict):
            parsed = inner
    for key in _NAME_KEYS:
        name = parsed.get(key)
        if isinstance(name, dict):  # {"function": {"name": ...}}
            name = name.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def classify(result: ExecResult) -> tuple[bool | None, str]:
    """``(ok, detail)`` for one probe answer.

    ``ok`` is None only when the answer was cut off at the token cap: a truncated reply is
    not evidence that the model cannot call a tool, so it must not be recorded as a refusal.
    """
    if result.tool_calls:
        if any(call.name == PROBE_TOOL_NAME for call in result.tool_calls):
            detail = DETAIL_STRUCTURED_WITH_TEXT if result.content.strip() else DETAIL_STRUCTURED
            return True, detail
        return False, DETAIL_WRONG_TOOL
    if _text_call_name(result.content) is not None:
        return False, DETAIL_CALL_AS_TEXT
    if result.finish_reason == "length":
        return None, DETAIL_TRUNCATED
    if result.content.strip() or result.reasoning.strip():
        return False, DETAIL_TEXT_ANSWER
    return False, DETAIL_EMPTY


def probe_scope(rules: Rules, tier: TierName) -> ScopeDecision:
    """The scope a probe runs under: just the probed tier (plus local), never a paid upgrade.

    No lock is invented here: the executor's ``enforce_scope`` reads the live rules, so a cloud
    probe under the private lock or airplane mode is refused there and recorded as an error
    rather than a capability observation. That keeps one place in charge of the locks.
    """
    if tier == "local":
        return ScopeDecision(
            scope="local", tiers=LOCAL_TIERS, strategy=rules.strategy, verify="off"
        )
    scope: Scope = "auto"
    return ScopeDecision(
        scope=scope, tiers=frozenset({tier, "local"}), strategy=rules.strategy, verify="off"
    )


def _decision(rules: Rules, row: ModelRow) -> RouteDecision:
    tier: TierName = row.tier  # type: ignore[assignment]  # checked by the caller
    breakdown = ScoreBreakdown(
        model_id=row.id,
        quality=0.0,
        complexity_penalty=0.0,
        warm_bonus=0.0,
        latency_penalty=0.0,
        evict_penalty=0.0,
        cost_usd=0.0,
        cost_penalty=0.0,
        soft_penalty=0.0,
        primary_bonus=0.0,
        total=0.0,
        floor=0.0,
        meets_floor=True,
        below_floor_by=0.0,
    )
    return RouteDecision(
        model=row.id,
        tier=tier,
        score=0.0,
        floor=0.0,
        below_floor=False,
        scope=probe_scope(rules, tier),
        reasons=("capability probe",),
        fallbacks=(),
        suggestions=(),
        rung="pin",
        verify_required=False,
        breakdown=breakdown,
    )


def _error_detail(exc: BaseException) -> str:
    """``error:<code>``; a ProviderError carries an HTTP status where others carry a code."""
    code = getattr(exc, "code", None) or getattr(exc, "status", None)
    return f"{ERROR_PREFIX}{code or type(exc).__name__}"


async def verify_model(executor: ExecutorLike, rules: Rules, row: ModelRow) -> ProbeOutcome:
    """Ask one model to call the synthetic tool. No database write; see ``verify_models``."""
    config = rules.models.tool_probe
    if not config.enabled:
        return ProbeOutcome(row.id, f"{SKIPPED_PREFIX}disabled")
    if row.tier not in ("local", "free_cloud", "paid"):
        return ProbeOutcome(row.id, f"{SKIPPED_PREFIX}tier")
    messages: tuple[Message, ...] = ({"role": "user", "content": PROBE_PROMPT},)
    try:
        result = await executor.run(
            _decision(rules, row),
            messages,
            [probe_tool_spec()],
            stream=False,
            options=ExecOptions(max_tokens=config.max_tokens, temperature=0),
        )
    except ExecutorError as exc:
        return ProbeOutcome(row.id, _error_detail(exc))
    except Exception as exc:  # a provider blowing up is not a capability observation
        return ProbeOutcome(row.id, _error_detail(exc))
    ok, detail = classify(result)
    return ProbeOutcome(row.id, detail, ok)


async def verify_models(
    executor: ExecutorLike,
    rules: Rules,
    rows: Sequence[ModelRow],
    *,
    db: Database | None = None,
    force: bool = False,
    max_models: int | None = None,
    now: float | None = None,
    candidates: Sequence[Candidate] = (),
) -> list[ProbeOutcome]:
    """Probe ``rows`` in order, recording each definitive outcome.

    Bounded by ``rules.models.tool_probe.max_models`` (``max_models`` overrides) and skips a
    model verified within ``retry_after_s`` unless ``force``. Idempotent: re-probing replaces
    the one row for that model and capability. ``candidates`` carries the computed local fits
    (``num_ctx``), without which a local call is refused; pass what the router would see.
    """
    config = rules.models.tool_probe
    if candidates:
        remember_candidates(candidates)
    stamp = time.time() if now is None else now
    limit = config.max_models if max_models is None else max_models
    out: list[ProbeOutcome] = []
    for index, row in enumerate(rows):
        if not config.enabled:
            out.append(ProbeOutcome(row.id, f"{SKIPPED_PREFIX}disabled"))
            continue
        if index >= limit:
            out.append(ProbeOutcome(row.id, f"{SKIPPED_PREFIX}limit"))
            continue
        stored: ProbeRow | None = None
        if db is not None:
            stored = get_probe(db, row.id, CAPABILITY_TOOLS)
        if (
            not force
            and stored is not None
            and 0 <= stamp - stored.checked_at < config.retry_after_s
        ):
            out.append(ProbeOutcome(row.id, f"{SKIPPED_PREFIX}recent", None, stored.ok))
            continue
        outcome = await verify_model(executor, rules, row)
        if outcome.ok is not None and db is not None:
            record_probe(db, row.id, CAPABILITY_TOOLS, outcome.ok, outcome.detail, now=stamp)
            # The column is what ``models list`` shows; routing reads the probe row, so a later
            # refresh re-advertising the capability cannot undo this.
            set_model_tools_ok(db, row.id, outcome.ok)
        out.append(outcome)
    return out
