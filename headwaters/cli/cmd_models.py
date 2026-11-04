"""``headwaters models list|refresh``: what Headwaters knows about, and how each local model fits.

``list`` makes no network call. For local models it shows the fit on this GPU (fits, offload or
no_fit) and the ``num_ctx`` the router would use, computed from the catalog/Ollama architecture
data and the current VRAM budget (HDW-109), for a request of ``--tokens`` tokens (default: a
small one, so the column shows the largest context step that fits).
"""

import asyncio
from typing import Annotated, Any

import typer

from headwaters.cli import runtime
from headwaters.cli.output import emit_json, make_console, table, yes_no
from headwaters.core.candidates import CandidateBuilder, fit_for_row
from headwaters.core.catalog import discovery, updater
from headwaters.core.catalog.schema import Catalog
from headwaters.core.hardware.fit import FitResult
from headwaters.core.hardware.state import HardwareState
from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_models import ModelRow, list_models
from headwaters.core.state_probes import CAPABILITY_TOOLS, ProbeRow, probes
from headwaters.core.tool_probe import ProbeOutcome, probe_requirements, verify_models

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


def _probe_cells(outcome: ProbeOutcome) -> tuple[str, str]:
    verdict = "-" if outcome.ok is None else yes_no(outcome.ok)
    detail = outcome.detail
    if outcome.ok is None and outcome.previous is not None:
        detail = f"{detail} (last: {yes_no(outcome.previous)})"
    return verdict, detail


def _normalised(name: str) -> str:
    return name.strip().lower().removesuffix(":latest")


def _probe_rows(rows: list[ModelRow], names: list[str] | None, tier: str) -> list[ModelRow]:
    """Rows to verify: the named models when given, else every model in ``tier``."""
    if names:
        wanted = {_normalised(n) for n in names}
        return [r for r in rows if _normalised(r.id) in wanted]
    return [r for r in rows if r.tier == tier]


def _probe_candidates(rules: Rules, db: Database, catalog: Catalog) -> list[Candidate]:
    """The local fits a probe request would see, so a local call gets a real ``num_ctx``."""
    hw = runtime.hardware_state(rules)
    return CandidateBuilder(db, rules, catalog, lambda: hw)(probe_requirements())


def _run_verify(
    rules: Rules,
    db: Database,
    catalog: Catalog,
    rows: list[ModelRow],
    *,
    force: bool,
    as_json: bool,
) -> None:
    """Probe ``rows`` and print what each model actually did. Never fails the shell."""
    if not rows:
        typer.echo("No models to verify. Run: headwaters models refresh")
        return
    executor = runtime.build_executor(rules, catalog)
    candidates = _probe_candidates(rules, db, catalog)
    outcomes = asyncio.run(
        verify_models(executor, rules, rows, db=db, force=force, candidates=candidates)
    )
    if as_json:
        emit_json(
            {
                "ok": True,
                "probes": [
                    {
                        "model": o.model,
                        "tools_ok": o.ok,
                        "detail": o.detail,
                        "previous": o.previous,
                    }
                    for o in outcomes
                ],
            }
        )
        return
    grid = table(None, "Model", "Tools", "Detail", no_wrap=["Model"])
    for outcome in outcomes:
        grid.add_row(outcome.model, *_probe_cells(outcome))
    console = make_console()
    console.print(grid)
    yes = sum(1 for o in outcomes if o.ok is True)
    no = sum(1 for o in outcomes if o.ok is False)
    rest = len(outcomes) - yes - no
    console.print(f"{yes} model(s) called the tool, {no} did not, {rest} not checked.")


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


def _tools_cell(row: ModelRow, probe: ProbeRow | None) -> str:
    """What the model was seen to do, when a probe has run; else what it advertises (HDW-512)."""
    return yes_no(row.tools_ok) if probe is None else f"{yes_no(probe.ok)} ({probe.detail})"


def _row_payload(
    row: ModelRow, fit: FitResult | None, probe: ProbeRow | None = None
) -> dict[str, Any]:
    return {
        "id": row.id,
        "tier": row.tier,
        "provider": row.provider,
        "ctx": row.ctx,
        "tools": row.tools_ok,
        "tools_probe": None
        if probe is None
        else {"ok": probe.ok, "detail": probe.detail, "checked_at": probe.checked_at},
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
    @models_app.command("verify-tools")
    def models_verify_tools(
        model: Annotated[
            list[str] | None,
            typer.Option("--model", help="Only this model; repeatable. Default: every local one."),
        ] = None,
        tier: Annotated[
            str, typer.Option("--tier", help="Tier to verify when --model is not given.")
        ] = "local",
        force: Annotated[
            bool, typer.Option("--force", help="Re-check even when a recent observation exists.")
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Ask each model to call a tool and record what it really did (HDW-512).

        ``tools_ok`` used to come only from what a provider advertises; ``qwen2.5-coder:14b``
        advertises tools but writes the call as JSON text, which a host shows as a raw block.
        This is the only thing that measures it, and it loads the model, so it is a command and
        never part of a request.
        """
        if tier not in TIERS:
            typer.echo(f"error: --tier must be one of {', '.join(TIERS)}", err=True)
            raise typer.Exit(2)
        rules = runtime.load_rules_or_exit()
        catalog, _ = updater.load_active()
        db = open_database()
        rows = _probe_rows(list_models(db), model, tier)
        _run_verify(rules, db, catalog, rows, force=force, as_json=as_json)

    @models_app.command("refresh")
    def models_refresh(
        verify_tools: Annotated[
            bool,
            typer.Option("--verify-tools", help="Also probe each local model's tool calling."),
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Re-discover models (Ollama, OpenRouter, OpenCode, your endpoints) now."""
        rules = runtime.load_rules_or_exit()
        catalog, source = updater.load_active()
        db = open_database()

        def progress(message: str) -> None:
            # Human progress goes to stderr, so ``--json`` on stdout stays parseable.
            typer.echo(message, err=True)

        result = discovery.refresh_models(db, rules, catalog, progress=progress)
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
        else:
            typer.echo(
                f"Refreshed {result.models} models ({result.unscored} unscored, "
                f"{result.removed} removed) using the {source} catalog."
            )
            typer.echo(f"Sources: {', '.join(result.sources_ok) or 'none reachable'}")
            for warning in result.warnings:
                typer.echo(f"warning: {warning}", err=True)
        if verify_tools or rules.models.tool_probe.on_refresh:
            rows = [r for r in list_models(db) if r.tier == "local"]
            _run_verify(rules, db, catalog, rows, force=False, as_json=as_json)

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
        db = open_database()
        rows = list_models(db, tier)
        observed = probes(db, CAPABILITY_TOOLS)
        hw = runtime.hardware_state(rules) if any(r.tier == "local" for r in rows) else None
        fits = {r.id: _fit(r, hw, rules, tokens) for r in rows}
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "gpu_budget_gb": None if hw is None else round(hw.budget_gb, 2),
                    "models": [_row_payload(r, fits[r.id], observed.get(r.id)) for r in rows],
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
                _tools_cell(row, observed.get(row.id)),
                ", ".join(_flags(row)),
            )
        console = make_console()
        console.print(grid)
        if hw is not None and hw.has_gpu:
            console.print(f"GPU budget for local models: {hw.budget_gb:.1f} GB (after the reserve)")
        elif any(r.tier == "local" for r in rows):
            console.print("No NVIDIA GPU detected: local fit is unknown.")
