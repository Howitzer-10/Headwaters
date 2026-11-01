"""``headwaters_status``: walls, scope, locks, GPU, warm model, pin and savings (HDW-205).

Read-only and local: no model call, no network. Same facts as ``headwaters status`` (the CLI), read
from the live rules, the live engine and the shared SQLite state. Savings appear only when a
dashboard plugin of the ledger feature is registered and on.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from headwaters.core.engine import Engine
from headwaters.core.hardware.state import HardwareState
from headwaters.core.project_config import ProjectConfig
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeError, paid_scopes_available, resolve_scope
from headwaters.core.state_limits import active_walls
from headwaters.core.state_misc import kv_get
from headwaters.core.state_models import list_models
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.schemas import ErrorInfo
from headwaters.mcp.tools.schemas_more import (
    GpuInfoOut,
    ScopeInfo,
    StatusResult,
    WallInfo,
    WarmModel,
)

__all__ = ["PIN_KEY", "build_status", "gpu_info", "scope_info", "walls_now"]

PIN_KEY = "mcp.pinned_model"
LEDGER_FEATURE = "ledger"


def scope_info(engine: Engine | None, rules: Rules, project: ProjectConfig | None) -> ScopeInfo:
    """What a plain request gets right now (rules default, Private lock, project rule)."""
    info = ScopeInfo(
        default_scope=rules.default_scope,
        strategy=rules.strategy,
        private_lock=rules.private_lock,
        airplane=rules.airplane,
        project_rule=(project.privacy if project is not None else None),
    )
    paid = paid_scopes_available(engine.registry) if engine is not None else False
    try:
        decision = resolve_scope(rules, project, paid_scopes=paid, local_available=True)
    except ScopeError as exc:
        info.error = ErrorInfo(code=exc.code, message=exc.message, fix=exc.fix_hint)
        info.locks = _lock_names(rules, ())
        return info
    info.effective = decision.scope
    info.tiers = sorted(decision.tiers)
    info.locks = _lock_names(rules, decision.locks)
    return info


def _lock_names(rules: Rules, found: tuple[str, ...]) -> list[str]:
    names = list(found)
    if rules.private_lock and "private_lock" not in names:
        names.append("private_lock")
    if rules.airplane:
        names.append("airplane")
    return names


def gpu_info(hw: HardwareState | None) -> GpuInfoOut | None:
    if hw is None or hw.gpu is None:
        return None
    return GpuInfoOut(
        name=hw.gpu.name,
        total_gb=round(hw.gpu.total_mb / 1024, 1),
        budget_gb=round(hw.budget_gb, 1),
        profile=hw.profile,
        on_battery=hw.battery.on_battery,
        busy=hw.gpu_busy.busy,
        local_suggestion=hw.local_suggestion,
    )


def walls_now(tc: ToolContext) -> list[WallInfo]:
    now = time.time()
    return [
        WallInfo(
            subject=w.subject,
            kind=w.kind,
            eta_s=None if w.until is None else round(max(0.0, w.until - now), 1),
            consecutive=w.consecutive_count,
        )
        for w in active_walls(tc.state(), now)
    ]


def _savings(engine: Engine | None) -> dict[str, Any] | None:
    if engine is None or not engine.registry.enabled(LEDGER_FEATURE):
        return None
    for plugin in engine.registry.plugins_for("DashboardPlugin"):
        summary: Mapping[str, Any] = plugin.render_summary()  # type: ignore[attr-defined]
        return dict(summary)
    return None


def build_status(tc: ToolContext, project: ProjectConfig | None) -> StatusResult:
    engine = tc.engine()
    rules = tc.rules()
    db = tc.state()
    warm = [WarmModel(model=m.id, vram_gb=m.size_vram_gb) for m in list_models(db) if m.warm]
    pinned = kv_get(db, PIN_KEY)
    notes: list[str] = []
    if engine is None:
        notes.append("Headwaters is still loading its model list.")
    hw = engine.hardware() if engine is not None else None
    return StatusResult(
        scope=scope_info(engine, rules, project),
        walls=walls_now(tc),
        gpu=gpu_info(hw),
        warm_models=warm,
        pinned_model=pinned if isinstance(pinned, str) else None,
        savings=_savings(engine),
        catalog=None if engine is None else engine.catalog_source,
        notes=notes,
    )
