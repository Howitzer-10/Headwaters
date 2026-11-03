"""``headwaters models list|refresh``: what Headwaters knows about, and how each local model fits.

``list`` makes no network call. For local models it shows the fit on this GPU (fits, offload or
no_fit) and the ``num_ctx`` the router would use, computed from the catalog/Ollama architecture
data and the current VRAM budget (HDW-109), for a request of ``--tokens`` tokens (default: a
small one, so the column shows the largest context step that fits).
"""

from typing import Annotated, Any

import typer

from headwaters.cli import runtime
from headwaters.cli.output import emit_json, make_console, table, yes_no
from headwaters.core.candidates import fit_for_row
from headwaters.core.catalog import discovery, updater
from headwaters.core.hardware.fit import FitResult
from headwaters.core.hardware.state import HardwareState
from headwaters.core.rules import Rules
from headwaters.core.state import open_database
from headwaters.core.state_models import ModelRow, list_models

TIERS = ("local", "free_cloud", "paid")


def _flags(row: ModelRow) -> list[str]:
    flags = []
    if row.unscored:
        flags.append("unscored")
    if row.warm:
        flags.append("warm")
    if row.retains_data:
        flags.append("retains-data")
    if row.deprecated_by:
        flags.append(f"deprecated, use {row.deprecated_by}")
    return flags


def _fit(row: ModelRow, hw: HardwareState | None, rules: Rules, tokens: int) -> FitResult | None:
    if row.tier != "local":
        return None
    budget = hw.budget_gb if hw is not None else 0.0
    return fit_for_row(row, budget, tokens, rules.tiers.local.kv_cache_type)


def _fit_cell(fit: FitResult | None, hw: HardwareState | None) -> str:
    if fit is None:
        return "-"
    if hw is None or not hw.has_gpu:
        return "unknown (no GPU detected)"
    return fit.status if fit.usable else f"no_fit ({fit.reason})"


def _row_payload(row: ModelRow, fit: FitResult | None) -> dict[str, Any]:
    return {
        "id": row.id,
        "tier": row.tier,
        "provider": row.provider,
        "ctx": row.ctx,
        "tools": row.tools_ok,
        "json": row.json_ok,
        "vision": "image" in row.modalities,
        "reasoning_ok": row.reasoning_ok,
        "size_gb": row.size_gb,
        "warm": row.warm,
        "unscored": row.unscored,
        "retains_data": row.retains_data,
        "deprecated_by": row.deprecated_by,
        "fit": None
        if fit is None
        else {"status": fit.status, "num_ctx": fit.num_ctx, "reason": fit.reason},
    }


def register(models_app: typer.Typer) -> None:
    @models_app.command("refresh")
    def models_refresh(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Re-discover models (Ollama, OpenRouter, OpenCode, your endpoints) now."""
        rules = runtime.load_rules_or_exit()
        catalog, source = updater.load_active()

        def progress(message: str) -> None:
            # Human progress goes to stderr, so ``--json`` on stdout stays parseable.
            typer.echo(message, err=True)

        result = discovery.refresh_models(open_database(), rules, catalog, progress=progress)
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "models": result.models,
                    "unscored": result.unscored,
                    "removed": result.removed,
                    "sources_ok": result.sources_ok,
                    "warnings": result.warnings,
                    "catalog": source,
                }
            )
            return
        typer.echo(
            f"Refreshed {result.models} models ({result.unscored} unscored, {result.removed} "
            f"removed) using the {source} catalog."
        )
        typer.echo(f"Sources: {', '.join(result.sources_ok) or 'none reachable'}")
        for warning in result.warnings:
            typer.echo(f"warning: {warning}", err=True)

    @models_app.command("list")
    def models_list(
        tier: Annotated[
            str | None, typer.Option("--tier", help="Only this tier: local, free_cloud or paid.")
        ] = None,
        tokens: Annotated[
            int,
            typer.Option("--tokens", min=1, help="Request size to compute the local fit for."),
        ] = 1000,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show the models Headwaters knows about, with fit and context. No network call."""
        if tier is not None and tier not in TIERS:
            typer.echo(f"error: --tier must be one of {', '.join(TIERS)}", err=True)
            raise typer.Exit(2)
        rules = runtime.load_rules_or_exit()
        rows = list_models(open_database(), tier)
        hw = runtime.hardware_state(rules) if any(r.tier == "local" for r in rows) else None
        fits = {r.id: _fit(r, hw, rules, tokens) for r in rows}
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "gpu_budget_gb": None if hw is None else round(hw.budget_gb, 2),
                    "models": [_row_payload(r, fits[r.id]) for r in rows],
                }
            )
            return
        if not rows:
            typer.echo("No models yet. Run: headwaters models refresh")
            return
        grid = table(
            None, "Model", "Tier", "Fit", "num_ctx", "Context", "Tools", "Flags", no_wrap=["Model"]
        )
        for row in rows:
            fit = fits[row.id]
            ctx = "-" if row.ctx is None else str(row.ctx)
            num_ctx = "-" if fit is None or fit.num_ctx is None else str(fit.num_ctx)
            grid.add_row(
                row.id,
                row.tier,
                _fit_cell(fit, hw),
                num_ctx,
                ctx,
                yes_no(row.tools_ok),
                ", ".join(_flags(row)),
            )
        console = make_console()
        console.print(grid)
        if hw is not None and hw.has_gpu:
            console.print(f"GPU budget for local models: {hw.budget_gb:.1f} GB (after the reserve)")
        elif any(r.tier == "local" for r in rows):
            console.print("No NVIDIA GPU detected: local fit is unknown.")
