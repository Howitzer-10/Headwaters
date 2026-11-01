"""Shared pieces for the HDW-206 tests: slow and priced models, progress capture, polling.

The rig is the HDW-204 ``Rig`` (a real engine, a real SQLite state, a scripted backend). Models are
made slow or costly through the data the estimate reads (size, warm flag, price columns), never by
patching the code under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp import Client, types

from headwaters.core.features import FeatureSpec
from headwaters.core.rules import Rules
from headwaters.core.state_misc import get_job
from headwaters.mcp.tools.context import ToolSettings
from tests.cli_support import add_model
from tests.frontdoor_support import CODE_TASK, Rig, structured, text_of

__all__ = [
    "CODE_TASK",
    "PAID",
    "Rig",
    "agree",
    "call_with_progress",
    "cold_rig",
    "deny",
    "paid_rig",
    "start_and_finish",
    "structured",
    "text_of",
    "wait_for_job",
]

PAID = "vendor/premium"


async def agree(context: Any, params: Any) -> types.ElicitResult:
    return types.ElicitResult(action="accept", content={"agree": True})


async def deny(context: Any, params: Any) -> types.ElicitResult:
    return types.ElicitResult(action="accept", content={"agree": False})


def cold_rig(tmp_path: Path, *, threshold_s: float = 20, **settings: Any) -> Rig:
    """A rig whose only local model is big and cold, so a call is expected to be slow."""
    rules = Rules.model_validate({"mcp": {"async_threshold_s": threshold_s}})
    rig = Rig(tmp_path, rules=rules, settings=ToolSettings(**settings) if settings else None)
    with rig.db.write() as c:  # cold, so it has to be loaded first (12 GB still fits the 16 GB GPU)
        c.execute("UPDATE models SET warm = 0, size_gb = 12.0 WHERE id = 'qwen:14b'")
    return rig


def paid_rig(tmp_path: Path, *, price_out: float = 5000.0, limit: float = 0.25) -> Rig:
    """A rig with a paid model that is clearly the best pick and costs real money per token.

    Prices are per million tokens: 5000 per million for a ~400-token answer is about $2 a call.
    """
    rules = Rules.model_validate(
        {
            "strategy": "primary_first",  # the user's own paid model is tried first
            "budget": {"confirm_above_usd": limit},
            "tiers": {"paid": {"models": [PAID]}},
        }
    )
    rig = Rig(tmp_path, rules=rules)
    # Core alone never routes to a paid tier: ``paid_scopes`` is a ★ feature. Switch it on here.
    rig.engine.registry.register(FeatureSpec("paid_scopes", True, "Paid scopes"))
    add_model(rig.db, PAID, "paid", 99.0, agentic=99.0, ctx=200_000)
    with rig.db.write() as c:
        c.execute(
            "UPDATE models SET price_in = 10.0, price_out = ? WHERE id = ?", (price_out, PAID)
        )
    return rig


async def call_with_progress(
    rig: Rig, name: str, args: Mapping[str, Any], **client_kw: Any
) -> tuple[types.CallToolResult, list[tuple[float, float | None, str | None]]]:
    """One call on a fresh client that asked for progress; returns the result and every update."""
    seen: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append((progress, total, message))

    async with Client(rig.server, **client_kw) as client:
        result = await client.call_tool(name, dict(args), progress_callback=on_progress)
    return result, seen


async def wait_for_job(rig: Rig, job_id: str, *, timeout_s: float = 10.0) -> None:
    """Wait for the job's task to end (not a sleep: the registry knows when it is done)."""
    await asyncio.wait_for(rig.context.jobs.wait(job_id), timeout_s)
    row = get_job(rig.db, job_id)
    assert row is not None


async def start_and_finish(rig: Rig, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """Call a tool that starts a background job, wait for the job, return the start result."""
    started = structured(await rig.call(name, args))
    assert started["job_id"], started
    await wait_for_job(rig, started["job_id"])
    return started
