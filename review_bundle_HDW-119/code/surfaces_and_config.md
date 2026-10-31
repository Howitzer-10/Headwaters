# CLI, example feature, config and data

Not the main review target, but needed to judge layering, the import contract and the seed data.

19 files.

| File | Lines | Purpose |
| --- | ---: | --- |
| `headwaters/cli/__init__.py` | 1 |  |
| `headwaters/cli/cmd_catalog.py` | 63 | ``headwaters catalog update|status``: the signed model catalog. |
| `headwaters/cli/cmd_features.py` | 80 | ``headwaters features``: the core and ★ feature list with the legend. |
| `headwaters/cli/cmd_models.py` | 157 | ``headwaters models list|refresh``: what Headwaters knows about, and how each local model fits. |
| `headwaters/cli/cmd_pick.py` | 96 | ``headwaters pick``: show which model would answer a prompt, why, and the fallbacks. |
| `headwaters/cli/cmd_scope.py` | 130 | ``headwaters scope`` and ``headwaters private``: the user's switches for scope and the lock. |
| `headwaters/cli/cmd_status.py` | 152 | ``headwaters status``: scope, locks, walls with ETAs and the warm model. |
| `headwaters/cli/main.py` | 62 | Headwaters command line (HDW-118). |
| `headwaters/cli/output.py` | 91 | Shared output helpers for the CLI (HDW-118): Rich tables for people, JSON for scripts. |
| `headwaters/cli/pick_report.py` | 119 | Rendering of a ``RoutePlan`` for ``headwaters pick``: a JSON payload and Rich tables. |
| `headwaters/cli/runtime.py` | 144 | Wiring for CLI commands: rules, catalog, state, project rule, features, hardware, router. |
| `headwaters/features/__init__.py` | 2 | Star (★) feature modules. Core must never import from here. |
| `headwaters/features/_example/__init__.py` | 40 | Dummy ★ feature used to prove registration and the contract harness. Not a real feature. |
| `pyproject.toml` | 96 |  |
| `.importlinter` | 16 |  |
| `.github/workflows/ci.yml` | 35 |  |
| `.pre-commit-config.yaml` | 26 |  |
| `catalog/seed_catalog.json` | 179 |  |
| `scripts/sign_catalog.py` | 93 | Owner tool: generate the catalog signing key and sign catalog releases (ed25519). |

## headwaters/cli/__init__.py

````python

````

## headwaters/cli/cmd_catalog.py

````python
"""``headwaters catalog update|status``: the signed model catalog.

``status`` makes no network call. ``update`` fetches the signed catalog (no prompt data is sent)
and is refused in airplane mode.
"""

import time
from typing import Annotated

import typer

from headwaters.cli import runtime
from headwaters.cli.output import EXIT_FAILED, emit_json
from headwaters.core.catalog import updater


def _fetched(at: float | None) -> str | None:
    return None if at is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(at))


def register(catalog_app: typer.Typer) -> None:
    @catalog_app.command("update")
    def catalog_update(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Fetch the signed catalog now (no prompt data is sent; off in airplane mode)."""
        result = updater.update(runtime.load_rules_or_exit(), manual=True)
        failed = result.outcome == "error"
        if as_json:
            emit_json({"ok": not failed, "outcome": result.outcome, "message": result.message})
        else:
            typer.echo(result.message)
        if failed:
            raise typer.Exit(EXIT_FAILED)

    @catalog_app.command("status")
    def catalog_status(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show which catalog is active and whether updates are allowed. No network call."""
        info = updater.status(runtime.load_rules_or_exit())
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "source": info.source,
                    "version": info.catalog_version,
                    "fetched_at": info.fetched_at,
                    "etag": info.etag,
                    "url": info.url,
                    "updates": "allowed" if info.update_blocked is None else info.update_blocked,
                }
            )
            return
        # Plain lines, as before: scripts and docs already quote these.
        typer.echo(f"Active catalog: {info.source} (version {info.catalog_version})")
        when = _fetched(info.fetched_at)
        if when is not None:
            typer.echo(f"Last fetched: {when}")
        typer.echo(f"ETag: {info.etag or '-'}")
        typer.echo(f"Feed URL: {info.url or '(not configured)'}")
        typer.echo(f"Updates: {info.update_blocked or 'allowed'}")
````

## headwaters/cli/cmd_features.py

````python
"""``headwaters features``: the core and ★ feature list with the legend.

Core features are free forever (a public promise, v3.5 section B); ★ features are free during the
beta and may become part of Headwaters Pro at 1.0. Core capabilities are a fixed list here so the
split is always visible, even before any ★ package is installed.
"""

from typing import Annotated

import typer

from headwaters.cli import runtime
from headwaters.cli.output import emit_json, make_console, table
from headwaters.core.features import STAR_LEGEND, FeatureRegistry, FeatureSpec, discover_features

# v3.5 addendum B, "Core" rows. Core never moves behind a paywall.
CORE_FEATURES: tuple[tuple[str, str], ...] = (
    ("gateway", "Gateway: OpenAI and Anthropic endpoints, bring your own keys"),
    ("mcp", "MCP server with the 8 front-door tools"),
    ("failover", "Automatic failover across walls (rate limit, out of credit, overloaded)"),
    (
        "scopes_free_local",
        "Free and local scopes, Private mode, project privacy rule, secret detection",
    ),
    ("hardware_guard", "Hardware guard: VRAM/KV fit, computed num_ctx, warm model, battery"),
    ("verify_basic", "Basic verification: parse, placeholder scan, git apply --check"),
    (
        "installer",
        "Installer for VS Code/Copilot, Cline, OpenCode, Claude Desktop; doctor; CLI; status page",
    ),
    ("catalog_updates", "Catalog updates"),
)


def _star_specs(registry: FeatureRegistry) -> list[FeatureSpec]:
    # Internal example/test features (ids starting with an underscore) are never listed.
    return [s for s in registry.all() if not s.id.startswith("_")]


def register(app: typer.Typer) -> None:
    @app.command("features")
    def features_command(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """List core features and ★ features (free during beta) and whether each is on."""
        rules = runtime.load_rules_or_exit()
        registry = FeatureRegistry(rules.features)
        discover_features(registry)
        stars = _star_specs(registry)
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "legend": STAR_LEGEND,
                    "core": [{"id": i, "title": t, "enabled": True} for i, t in CORE_FEATURES],
                    "star": [
                        {"id": s.id, "title": s.title, "enabled": registry.enabled(s.id)}
                        for s in stars
                    ],
                }
            )
            return
        console = make_console()
        core = table("Core (free forever)", "Feature", "State", no_wrap=["State"])
        for _, title in CORE_FEATURES:
            core.add_row(title, "on")
        console.print(core)
        extra = table(
            "★ features (free during beta)", "Feature", "Id", "State", no_wrap=["Id", "State"]
        )
        for spec in stars:
            extra.add_row(spec.label, spec.id, "on" if registry.enabled(spec.id) else "off")
        console.print()
        if stars:
            console.print(extra)
        else:
            console.print("★ features: none installed.")
        console.print()
        console.print(STAR_LEGEND)
````

## headwaters/cli/cmd_models.py

````python
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
        result = discovery.refresh_models(open_database(), rules, catalog)
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
````

## headwaters/cli/cmd_pick.py

````python
"""``headwaters pick``: show which model would answer a prompt, why, and the fallbacks.

A dry run of the router (``Router.plan``): the prompt is analyzed on this machine, scope locks and
secret detection are applied, and the policy picks a model. No model is called, no cloud client
is created, nothing is written, and the prompt is never stored or printed back.
"""

import asyncio
from typing import Annotated

import typer

from headwaters.cli import runtime
from headwaters.cli.output import EXIT_NO_ROUTE, EXIT_SCOPE, emit_json, fail
from headwaters.cli.pick_report import plan_payload, print_plan
from headwaters.core.executor.types import Message, ToolSpec
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.router_types import RouteRequest
from headwaters.core.rules import Scope
from headwaters.core.scope import ScopeError
from headwaters.core.state_models import list_models

_IMAGE_PART = {"type": "image_url", "image_url": {"url": "data:,"}}
_PROBE_TOOL: ToolSpec = {
    "type": "function",
    "function": {
        "name": "headwaters_probe",
        "description": "Stands in for the tools your host would send.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _messages(prompt: str, image: bool) -> list[Message]:
    if not image:
        return [{"role": "user", "content": prompt}]
    return [{"role": "user", "content": [{"type": "text", "text": prompt}, _IMAGE_PART]}]


def register(app: typer.Typer) -> None:
    @app.command("pick")
    def pick(
        prompt: Annotated[str, typer.Argument(help="The prompt to route (analyzed locally).")],
        image: Annotated[
            bool, typer.Option("--image", help="Pretend an image is attached.")
        ] = False,
        tools: Annotated[
            bool, typer.Option("--tools", help="Pretend the host sends tools.")
        ] = False,
        tokens: Annotated[
            int | None,
            typer.Option("--tokens", min=1, help="Pretend the request has this many tokens."),
        ] = None,
        scope: Annotated[
            Scope | None, typer.Option("--scope", help="Scope to ask for (default: your rules).")
        ] = None,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show which model would answer a prompt, why, and what it falls back to.

        A dry run: no model is called and nothing is stored.
        """
        rt = runtime.load_runtime(as_json=as_json)
        hw = runtime.hardware_state(rt.rules)
        router = runtime.build_router(rt, hw, tokens)
        request = RouteRequest(
            messages=_messages(prompt, image),
            surface="cli",
            tools=[_PROBE_TOOL] if tools else None,
            scope=scope,
            project=rt.project,
        )
        try:
            plan = asyncio.run(router.plan(request))
        except ScopeError as exc:
            fail(exc.code, exc.message, fix=exc.fix_hint, exit_code=EXIT_SCOPE, as_json=as_json)
        except NoRouteError as exc:
            hints = [t.text for t in exc.suggestions]
            if exc.code == "no_candidates" and not list_models(rt.db):
                hints.insert(0, "No models are known yet. Run: headwaters models refresh")
            fail(
                exc.code,
                exc.message,
                fix=" ".join(hints) or None,
                exit_code=EXIT_NO_ROUTE,
                as_json=as_json,
                extra={
                    "etas_s": exc.etas,
                    "rejected": [{"model": x.model_id, "reason": x.reason} for x in exc.rejected],
                },
            )
        if as_json:
            emit_json(plan_payload(plan))
        else:
            print_plan(plan)
````

## headwaters/cli/cmd_scope.py

````python
"""``headwaters scope`` and ``headwaters private``: the user's switches for scope and the lock.

Both edit ``rules.yaml`` in place (comments are kept) and the running engines pick the change up
through the rules hot reload. Only the user loosens a lock, and the CLI is the user: it never
does so on behalf of a request. A project rule or the Private lock still wins over a looser
default scope, and the command says so.
"""

from typing import Annotated, Any, Literal

import typer

from headwaters.cli import runtime
from headwaters.cli.cmd_status import effective_scope, locks_in_force
from headwaters.cli.output import EXIT_CONFIG, emit_json, fail, make_console
from headwaters.core.rules import Scope
from headwaters.core.rules_edit import set_setting
from headwaters.core.rules_io import ConfigError

Switch = Literal["on", "off"]


def _effective_payload(rt: runtime.Runtime) -> tuple[dict[str, Any], str]:
    decision, error = effective_scope(rt)
    locks = locks_in_force(rt, decision)
    if decision is None:
        message = error.message if error else "no scope can be served"
        return {
            "scope": None,
            "tiers": [],
            "locks": locks,
            "error": error.as_dict() if error else None,
        }, (f"Effective scope: none. {message}" + (f"\nfix: {error.fix_hint}" if error else ""))
    tiers = sorted(decision.tiers)
    line = f"Effective scope: {decision.scope} (tiers: {', '.join(tiers)})"
    if locks:
        line += f"; locks in force: {', '.join(locks)}"
    return {"scope": decision.scope, "tiers": tiers, "locks": locks, "error": None}, line


def _apply(key: str, value: bool | str, as_json: bool) -> tuple[Any, Any]:
    try:
        return set_setting(key, value)
    except ConfigError as exc:
        fail(
            "invalid_rules",
            str(exc),
            fix="Fix the problems listed above in rules.yaml, then run the command again.",
            exit_code=EXIT_CONFIG,
            as_json=as_json,
        )


def register(app: typer.Typer) -> None:
    @app.command("scope")
    def scope_command(
        value: Annotated[
            Scope | None,
            typer.Argument(help="auto | free | local | cloud. Omit to show the current scope."),
        ] = None,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show or set the default scope: which tiers may serve requests."""
        previous = None
        if value is not None:
            previous, _ = _apply("default_scope", value, as_json)
        rt = runtime.load_runtime(as_json=as_json)
        effective, line = _effective_payload(rt)
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "default_scope": rt.rules.default_scope,
                    "previous": previous,
                    "changed": value is not None and previous != value,
                    "effective": effective,
                }
            )
            return
        console = make_console()
        if value is None:
            console.print(f"Default scope: {rt.rules.default_scope}")
        elif previous == value:
            console.print(f"Default scope is already {value}.")
        else:
            console.print(f"Default scope: {previous} -> {value}")
        console.print(line)
        if value is not None and effective["scope"] != value:
            console.print(
                "Note: a lock or project rule is narrowing this; the tighter setting always wins."
            )

    @app.command("private")
    def private_command(
        state: Annotated[
            Switch | None,
            typer.Argument(help="on = local models only (the Private lock). Omit to show it."),
        ] = None,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Turn the Private lock on or off. While it is on, nothing leaves this machine."""
        previous = None
        if state is not None:
            previous, _ = _apply("private_lock", state == "on", as_json)
        rt = runtime.load_runtime(as_json=as_json)
        effective, line = _effective_payload(rt)
        lock = rt.rules.private_lock
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "private_lock": lock,
                    "previous": previous,
                    "changed": state is not None and previous != lock,
                    "effective": effective,
                }
            )
            return
        console = make_console()
        word = "on" if lock else "off"
        if state is None:
            console.print(f"Private lock: {word}")
        elif previous == lock:
            console.print(f"Private lock is already {word}.")
        else:
            console.print(f"Private lock: {'on' if previous else 'off'} -> {word}")
        if lock:
            console.print("Only local models will be used; no cloud client is created.")
        console.print(line)
````

## headwaters/cli/cmd_status.py

````python
"""``headwaters status``: scope, locks, walls with ETAs and the warm model.

Reads only local state (rules, project rule, the shared SQLite database); no network call.
"""

import time
from typing import Annotated, Any

import typer

from headwaters.cli import runtime
from headwaters.cli.output import emit_json, fmt_eta, make_console, table
from headwaters.core.scope import ScopeDecision, ScopeError, paid_scopes_available, resolve_scope
from headwaters.core.state_limits import WallRow, active_walls
from headwaters.core.state_models import ModelRow, list_models

_KIND_TEXT = {
    "rate_limit": "rate limited",
    "exhausted": "out of credit",
    "overloaded": "overloaded",
}


def effective_scope(rt: runtime.Runtime) -> tuple[ScopeDecision | None, ScopeError | None]:
    """The scope a plain request gets right now (rules default, Private lock, project rule)."""
    try:
        decision = resolve_scope(
            rt.rules,
            rt.project,
            paid_scopes=paid_scopes_available(rt.registry),
            local_available=True,
        )
    except ScopeError as exc:
        return None, exc
    return decision, None


def locks_in_force(rt: runtime.Runtime, decision: ScopeDecision | None) -> list[str]:
    found = list(decision.locks) if decision is not None else []
    if rt.rules.private_lock and "private_lock" not in found:
        found.append("private_lock")
    if rt.rules.airplane:
        found.append("airplane")
    return found


def _wall_eta(row: WallRow, now: float) -> float | None:
    return None if row.until is None else max(0.0, row.until - now)


def _wall_payload(row: WallRow, now: float) -> dict[str, Any]:
    return {
        "subject": row.subject,
        "kind": row.kind,
        "since": row.since,
        "eta_s": _wall_eta(row, now),
        "consecutive": row.consecutive_count,
    }


def _warm_payload(row: ModelRow) -> dict[str, Any]:
    return {"model": row.id, "vram_gb": row.size_vram_gb}


def register(app: typer.Typer) -> None:
    @app.command("status")
    def status(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show the scope in force, locks, walls with their reset times, and the warm model."""
        rt = runtime.load_runtime(as_json=as_json)
        now = time.time()
        decision, scope_error = effective_scope(rt)
        locks = locks_in_force(rt, decision)
        walls = active_walls(rt.db, now)
        warm = [m for m in list_models(rt.db) if m.warm]

        if as_json:
            emit_json(
                {
                    "ok": True,
                    "default_scope": rt.rules.default_scope,
                    "strategy": rt.rules.strategy,
                    "effective": None
                    if decision is None
                    else {
                        "scope": decision.scope,
                        "tiers": sorted(decision.tiers),
                        "reasons": list(decision.reasons),
                    },
                    "scope_error": scope_error.as_dict() if scope_error else None,
                    "locks": locks,
                    "private_lock": rt.rules.private_lock,
                    "airplane": rt.rules.airplane,
                    "project_rule": rt.project.privacy,
                    "project_file": str(rt.project_path) if rt.project_path else None,
                    "walls": [_wall_payload(w, now) for w in walls],
                    "warm_models": [_warm_payload(m) for m in warm],
                    "catalog": {"source": rt.catalog_source, "version": rt.catalog.catalog_version},
                }
            )
            return

        console = make_console()
        top = table("Status", "Item", "Value", no_wrap=["Item"])
        top.add_row("Default scope", f"{rt.rules.default_scope}  (strategy: {rt.rules.strategy})")
        if decision is not None:
            tiers = ", ".join(sorted(decision.tiers))
            top.add_row("Effective scope", f"{decision.scope}  (tiers: {tiers})")
        else:
            top.add_row("Effective scope", "none: " + (scope_error.message if scope_error else ""))
        top.add_row("Private lock", "on" if rt.rules.private_lock else "off")
        top.add_row(
            "Project rule",
            f"privacy: {rt.project.privacy}"
            + (f"  ({rt.project_path})" if rt.project_path else "  (no .headwaters.yaml here)"),
        )
        top.add_row("Airplane mode", "on (local models only)" if rt.rules.airplane else "off")
        top.add_row("Locks in force", ", ".join(locks) or "none")
        top.add_row("Catalog", f"{rt.catalog_source}, version {rt.catalog.catalog_version}")
        console.print(top)
        if scope_error is not None:
            console.print(f"\nfix: {scope_error.fix_hint}")

        if walls:
            wall_table = table(
                "Walls", "Subject", "State", "Resets in", "Hits", no_wrap=["Subject"]
            )
            for row in walls:
                eta = _wall_eta(row, now)
                wall_table.add_row(
                    row.subject,
                    _KIND_TEXT.get(row.kind, row.kind),
                    fmt_eta(eta) if eta is not None else "after a balance check",
                    str(row.consecutive_count),
                )
            console.print()
            console.print(wall_table)
        else:
            console.print("\nWalls: none. Every model is available.")

        if warm:
            console.print(
                "\nWarm model: "
                + ", ".join(
                    m.id + (f" ({m.size_vram_gb:.1f} GB on the GPU)" if m.size_vram_gb else "")
                    for m in warm
                )
            )
        else:
            console.print("\nWarm model: none (as of the last `headwaters models refresh`).")
````

## headwaters/cli/main.py

````python
"""Headwaters command line (HDW-118).

Commands live in ``cmd_*`` modules and register themselves here. Every command that prints data
has ``--json`` for scripts; human output uses Rich tables. Exit codes: 0 ok, 1 the command
failed, 2 invalid config or usage, 3 the request cannot be served inside the scope, 4 no model
can serve it right now.
"""

import typer

import headwaters
from headwaters.cli import cmd_catalog, cmd_features, cmd_models, cmd_pick, cmd_scope, cmd_status
from headwaters.cli.output import emit_json
from headwaters.core.stdio import configure_utf8

app = typer.Typer(
    name="headwaters",
    help="Headwaters: keeps you coding when paid credits run out.",
    no_args_is_help=True,
    add_completion=False,
)

models_app = typer.Typer(help="Discovered models: refresh from live sources, or list.")
catalog_app = typer.Typer(help="Model catalog: update from the signed feed, or show its status.")
app.add_typer(models_app, name="models")
app.add_typer(catalog_app, name="catalog")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(headwaters.__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version."
    ),
) -> None:
    """Headwaters command line."""
    configure_utf8()


@app.command("version")
def version_command(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show the Headwaters version."""
    if as_json:
        emit_json({"ok": True, "version": headwaters.__version__})
    else:
        typer.echo(headwaters.__version__)


cmd_pick.register(app)
cmd_status.register(app)
cmd_scope.register(app)
cmd_features.register(app)
cmd_models.register(models_app)
cmd_catalog.register(catalog_app)
````

## headwaters/cli/output.py

````python
"""Shared output helpers for the CLI (HDW-118): Rich tables for people, JSON for scripts.

``--json`` output is written with plain ``typer.echo`` so it is never wrapped, coloured or cut.
Nothing printed here may contain prompt text, model output or keys: only decision metadata.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.table import Table

EXIT_OK = 0
EXIT_FAILED = 1  # the command ran and failed (network, nothing to do, ...)
EXIT_CONFIG = 2  # invalid rules.yaml / .headwaters.yaml, or bad usage
EXIT_SCOPE = 3  # the request cannot be served inside the effective scope
EXIT_NO_ROUTE = 4  # no model can serve the request right now

_PIPED_WIDTH = 120


def make_console() -> Console:
    """A console that prints text as-is (no markup, emoji or auto-highlighting).

    Created per command so it follows the current ``sys.stdout``. When output is piped and
    ``COLUMNS`` is not set, tables get a fixed generous width instead of Rich's 80 columns.
    """
    console = Console(markup=False, highlight=False, emoji=False)
    if not console.is_terminal and "COLUMNS" not in os.environ:
        console.width = _PIPED_WIDTH
    return console


def table(title: str | None, *columns: str, no_wrap: Sequence[str] = ()) -> Table:
    """A table with the given column headers; ``no_wrap`` columns fold instead of wrapping."""
    result = Table(title=title, title_justify="left", show_lines=False)
    for name in columns:
        result.add_column(name, no_wrap=name in no_wrap, overflow="fold")
    return result


def emit_json(payload: Mapping[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False))


def fail(
    code: str,
    message: str,
    *,
    fix: str | None = None,
    exit_code: int = EXIT_FAILED,
    as_json: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> NoReturn:
    """Report a structured error (JSON on stdout, or text on stderr) and exit."""
    error: dict[str, Any] = {"code": code, "message": message}
    if fix:
        error["fix"] = fix
    if extra:
        error.update(extra)
    if as_json:
        emit_json({"ok": False, "error": error})
    else:
        typer.echo(f"error: {message}", err=True)
        if fix:
            typer.echo(f"fix: {fix}", err=True)
    raise typer.Exit(exit_code)


def fmt_eta(seconds: float | None) -> str:
    """``45s``, ``3m 05s``, ``1h 02m``; ``-`` when unknown."""
    if seconds is None:
        return "-"
    total = max(0, round(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def yes_no(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "no")
````

## headwaters/cli/pick_report.py

````python
"""Rendering of a ``RoutePlan`` for ``headwaters pick``: a JSON payload and Rich tables.

Only decision metadata is shown (model ids, tiers, scores, reason codes). The prompt is never
echoed back.
"""

from typing import Any

from headwaters.cli.output import fmt_eta, make_console, table, yes_no
from headwaters.core.policy.filters import Candidate
from headwaters.core.router_plan import RoutePlan

_MAX_SKIPPED_SHOWN = 12


def availability(c: Candidate | None) -> dict[str, Any]:
    if c is None:
        return {"status": "unknown", "eta_s": None}
    return {"status": c.availability.status, "eta_s": c.availability.eta}


def plan_payload(plan: RoutePlan) -> dict[str, Any]:
    d, s, r = plan.decision, plan.scope, plan.requirements
    return {
        "ok": True,
        "model": d.model,
        "tier": d.tier,
        "rung": d.rung,
        "score": round(d.score, 2),
        "floor": round(d.floor, 2),
        "below_floor": d.below_floor,
        "verify_required": d.verify_required,
        "scope": {
            "scope": s.scope,
            "tiers": sorted(s.tiers),
            "strategy": s.strategy,
            "verify": s.verify,
            "locks": list(s.locks),
        },
        "requirements": {
            "task": r.soft.task,
            "complexity": r.soft.complexity,
            "risk": r.soft.risk,
            "agentic": r.soft.agentic,
            "est_tokens": r.est_tokens,
            "vision": r.hard.vision,
            "tools": r.hard.tools,
            "json": r.hard.json,
            "min_ctx": r.hard.min_ctx,
        },
        "reasons": list(d.reasons),
        "fallbacks": [
            {"model": m, "tier": c.tier if c else None, **availability(c)}
            for m in d.fallbacks
            for c in [plan.candidate(m)]
        ],
        "suggestions": [{"code": x.code, "text": x.text} for x in d.suggestions],
        "rejected": [{"model": x.model_id, "reason": x.reason} for x in plan.rejected],
    }


def _request_line(plan: RoutePlan) -> str:
    r = plan.requirements
    needs = [
        name
        for name, on in (("vision", r.hard.vision), ("tools", r.hard.tools), ("json", r.hard.json))
        if on
    ]
    line = (
        f"{r.soft.task}, complexity {r.soft.complexity}, risk {r.soft.risk}, ~{r.est_tokens} tokens"
    )
    return line + (f", needs {', '.join(needs)}" if needs else "")


def print_plan(plan: RoutePlan) -> None:
    console = make_console()
    d, s = plan.decision, plan.scope

    head = table("Pick", "Item", "Value", no_wrap=["Item"])
    head.add_row("Model", d.model)
    head.add_row("Tier", d.tier)
    head.add_row("Scope", f"{s.scope} (tiers: {', '.join(sorted(s.tiers))})")
    head.add_row("Strategy", s.strategy)
    head.add_row("Locks", ", ".join(s.locks) or "none")
    head.add_row("Rung", d.rung)
    head.add_row("Score / floor", f"{d.score:.1f} / {d.floor:.0f}")
    head.add_row("Below floor", yes_no(d.below_floor))
    head.add_row("Verification", "required" if d.verify_required else s.verify)
    head.add_row("Request", _request_line(plan))
    console.print(head)

    console.print("\nWhy:")
    for reason in d.reasons:
        console.print(f"  - {reason}")
    for tip in d.suggestions:
        console.print(f"  * {tip.text}")

    if d.fallbacks:
        fallbacks = table("Fallbacks, in order", "#", "Model", "Tier", "Availability")
        for index, model in enumerate(d.fallbacks, start=1):
            c = plan.candidate(model)
            state = availability(c)
            eta = f" (reset in {fmt_eta(state['eta_s'])})" if state["eta_s"] is not None else ""
            fallbacks.add_row(str(index), model, c.tier if c else "-", f"{state['status']}{eta}")
        console.print()
        console.print(fallbacks)
    else:
        console.print("\nFallbacks: none. If this model fails, the request fails.")

    if plan.rejected:
        skipped = table("Not eligible", "Model", "Reason", no_wrap=["Model"])
        for item in plan.rejected[:_MAX_SKIPPED_SHOWN]:
            skipped.add_row(item.model_id, item.reason)
        console.print()
        console.print(skipped)
        extra = len(plan.rejected) - _MAX_SKIPPED_SHOWN
        if extra > 0:
            console.print(f"... and {extra} more (use --json for the full list)")
````

## headwaters/cli/runtime.py

````python
"""Wiring for CLI commands: rules, catalog, state, project rule, features, hardware, router.

Everything is created per command and nothing here starts a server or calls a model.
``hardware_state`` is the one place that probes the GPU, so tests replace it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import typer

from headwaters.cli.output import EXIT_CONFIG, fail
from headwaters.core.analyzer.requirements import Analyzer, AnalyzerInput, Requirements
from headwaters.core.candidates import CandidateBuilder
from headwaters.core.catalog import updater as catalog_updater
from headwaters.core.catalog.schema import Catalog
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import ExecTarget
from headwaters.core.features import FeatureRegistry, discover_features
from headwaters.core.hardware.state import HardwareMonitor, HardwareState
from headwaters.core.policy.types import RouteDecision
from headwaters.core.project_config import ProjectConfig, load_project_config
from headwaters.core.quotas import QuotaTracker
from headwaters.core.router import Router
from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError, load_rules, rules_path
from headwaters.core.secrets_detect import SecretDetector
from headwaters.core.state import Database, open_database
from headwaters.core.walls import WallClassifier

__all__ = ["Runtime", "build_router", "hardware_state", "load_rules_or_exit", "load_runtime"]


def load_rules_or_exit() -> Rules:
    """``rules.yaml`` (defaults when absent). An invalid file is reported and exits with 2."""
    path = rules_path()
    if not path.exists():
        return Rules()
    try:
        return load_rules(path)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(EXIT_CONFIG) from exc


def hardware_state(rules: Rules) -> HardwareState | None:
    """Current GPU/battery snapshot, or ``None`` when probing fails."""
    try:
        return HardwareMonitor(rules.tiers.local).get_state()
    except Exception:  # a broken probe must never stop a CLI command
        return None


def _registry(rules: Rules) -> FeatureRegistry:
    registry = FeatureRegistry()
    discover_features(registry)
    registry.set_flags(rules.features)
    return registry


@dataclass(frozen=True)
class Runtime:
    rules: Rules
    catalog: Catalog
    catalog_source: str
    db: Database
    project: ProjectConfig
    project_path: Path | None
    registry: FeatureRegistry


def load_runtime(*, as_json: bool = False) -> Runtime:
    rules = load_rules_or_exit()
    catalog, source = catalog_updater.load_active()
    try:
        project, project_path = load_project_config(Path.cwd())
    except ConfigError as exc:
        fail(
            "invalid_project_config",
            str(exc),
            fix="Fix the problems listed above in .headwaters.yaml.",
            exit_code=EXIT_CONFIG,
            as_json=as_json,
        )
    return Runtime(
        rules=rules,
        catalog=catalog,
        catalog_source=source,
        db=open_database(),
        project=project,
        project_path=project_path,
        registry=_registry(rules),
    )


class SizedAnalyzer:
    """Deterministic analyzer that can pretend the request has a given size (``--tokens``).

    Layer 3 (the CPU classifier) is not used: a dry run must not call any model, not even a
    local one. The complexity formula mirrors layer 1 (one level per token step passed, plus
    one each for a reasoning hint and an agentic request), so a big ``--tokens`` raises it.
    """

    def __init__(self, base: Analyzer, tokens: int | None = None) -> None:
        self._base = base
        self._tokens = tokens

    def analyze(self, inp: AnalyzerInput) -> Requirements:
        found = self._base.analyze(inp)
        size = self._tokens
        if size is None:
            return found
        steps = self._base.hints.complexity_token_steps
        level = 1 + sum(1 for step in steps if size > step)
        level += int(found.soft.reasoning_hint) + int(found.soft.agentic)
        hard = dataclasses.replace(found.hard, min_ctx=size)
        soft = dataclasses.replace(found.soft, complexity=max(1, min(5, level)))
        return dataclasses.replace(found, hard=hard, soft=soft, est_tokens=size)


def _never_runs(decision: RouteDecision) -> ExecTarget:
    raise RuntimeError("the CLI dry run never executes a model")


def build_router(rt: Runtime, hw: HardwareState | None, tokens: int | None = None) -> Router:
    """A router for dry runs: real scope, policy, quotas and candidates; no execution."""
    catalog = rt.catalog
    executor = Executor(rt.rules, WallClassifier(catalog.wall_patterns), _never_runs)
    candidates = CandidateBuilder(rt.db, rt.rules, catalog, lambda: hw)
    return Router(
        rt.rules,
        rt.db,
        SizedAnalyzer(Analyzer.from_catalog(catalog), tokens),
        candidates,
        executor,
        QuotaTracker(rt.db, rt.rules.walls, catalog.free_limits),
        secrets=SecretDetector.from_catalog(catalog),
        registry=rt.registry,
        hardware=lambda: hw,
    )
````

## headwaters/features/__init__.py

````python
"""Star (★) feature modules. Core must never import from here."""
````

## headwaters/features/_example/__init__.py

````python
"""Dummy ★ feature used to prove registration and the contract harness. Not a real feature."""

from __future__ import annotations

from collections.abc import Mapping

from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.plugins import CURRENT_API_VERSION

FEATURE = FeatureSpec(
    id="_example",
    star=True,
    title="Example feature",
    interfaces=("VerifierPlugin", "LedgerPlugin"),
)


class ExampleVerifier:
    api_version = CURRENT_API_VERSION

    def verify(self, candidate: str) -> list[str]:
        return [] if candidate.strip() else ["empty answer"]


class ExampleLedger:
    api_version = CURRENT_API_VERSION

    def __init__(self) -> None:
        self.events: list[Mapping[str, object]] = []

    def record(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))


def register(registry: FeatureRegistry) -> None:
    registry.register(
        FEATURE,
        {"VerifierPlugin": ExampleVerifier(), "LedgerPlugin": ExampleLedger()},
    )
````

## pyproject.toml

````toml
[project]
name = "headwaters"
version = "0.0.0"
description = "Windows-first, local-first LLM router that keeps developers coding when paid credits run out."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "All rights reserved" }
dependencies = [
    "cryptography",
    "httpx",
    "jsonschema",
    "litellm==1.106.6",
    "nvidia-ml-py",
    "platformdirs>=4.14",
    "psutil",
    "pydantic>=2.13",
    "pyyaml>=6",
    "tree-sitter-language-pack",
    "typer>=0.27.3",
    "watchdog>=6",
]

[project.scripts]
headwaters = "headwaters.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["headwaters"]

[dependency-groups]
dev = [
    "freezegun",
    "import-linter>=2.15",
    "mypy>=2.4.1",
    "pre-commit>=4.7.0",
    "pytest>=9.2.0",
    "pytest-cov>=7.1.0",
    "ruff>=0.17.3",
    "types-jsonschema>=4.26.0.20261006",
    "types-psutil",
    "types-pyyaml>=6.0.12.20261030",
]

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["."]
extend-exclude = ["model_provisioner_previous.py", "setup_models_previous.py"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "C4", "PT", "RUF"]

[tool.mypy]
python_version = "3.11"
files = ["headwaters", "tests"]
warn_unused_configs = true
warn_redundant_casts = true

# Strict is required for core only; mypy cannot set `strict` per module, so the flags are listed.
[[tool.mypy.overrides]]
module = ["headwaters.core", "headwaters.core.*"]
disallow_any_generics = true
disallow_subclassing_any = true
disallow_untyped_calls = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
disallow_untyped_decorators = true
warn_unused_ignores = true
warn_return_any = true
no_implicit_reexport = true
strict_equality = true
extra_checks = true

[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config -m 'not live'"
markers = [
    "live: needs a real external service such as Ollama (deselected by default; run with -m live)",
    "windows: Windows-only test (skipped on other platforms)",
    "slow: slow test",
]

[tool.coverage.run]
source = ["headwaters/core"]
branch = true

[tool.coverage.report]
fail_under = 85
show_missing = true
skip_empty = true
````

## .importlinter

````
[importlinter]
root_package = headwaters
include_external_packages = False

[importlinter:contract:core-is-a-leaf]
name = Core must not import features, gateway, mcp, control or installer
type = forbidden
source_modules =
    headwaters.core
forbidden_modules =
    headwaters.features
    headwaters.gateway
    headwaters.mcp
    headwaters.control
    headwaters.installer
````

## .github/workflows/ci.yml

````yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    name: ${{ matrix.os }} / py${{ matrix.python }}
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [windows-latest, ubuntu-latest]
        python: ["3.11", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: ${{ matrix.python }}
          enable-cache: true
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run lint-imports
      - run: uv run python -m headwaters.core.rules_io --check catalog/rules.schema.json
      - run: uv run python -m headwaters.core.catalog.loader --check catalog/catalog.schema.json
      - run: uv run pytest --cov --cov-report=term-missing --cov-report=xml
      - uses: actions/upload-artifact@v4
        with:
          name: coverage-${{ matrix.os }}-py${{ matrix.python }}
          path: coverage.xml
````

## .pre-commit-config.yaml

````
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: uv run ruff check --fix
        language: system
        types: [python]
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types: [python]
      - id: mypy
        name: mypy
        entry: uv run mypy
        language: system
        types: [python]
        pass_filenames: false
      - id: import-linter
        name: import-linter
        entry: uv run lint-imports
        language: system
        types: [python]
        pass_filenames: false
````

## catalog/seed_catalog.json

````json
{
  "schema_version": 1,
  "catalog_version": "0.0.0-seed",
  "unknown_model_defaults": {
    "scores": {
      "code": 0.3, "reasoning": 0.3, "chat": 0.4, "summarize": 0.4,
      "extract": 0.3, "creative": 0.3, "vision": 0.0, "agentic_tools": 0.2
    },
    "tools_ok": false,
    "json_ok": false,
    "ctx": 8192
  },
  "models": [
    {
      "id": "example-paid/model-large",
      "aliases": ["example-large"],
      "tier_hints": ["paid"],
      "provider": "example-paid",
      "modalities": ["text"],
      "tools_ok": true,
      "json_ok": true,
      "ctx": 200000,
      "price_in": 3.0,
      "price_out": 15.0,
      "price_date": "2026-01-01",
      "scores": {"code": 0.9, "reasoning": 0.9, "chat": 0.9, "agentic_tools": 0.9},
      "deprecated_by": null,
      "retains_data": false,
      "verify": true
    },
    {
      "id": "example-openrouter/model-free:free",
      "aliases": [],
      "tier_hints": ["free"],
      "provider": "example-openrouter",
      "modalities": ["text"],
      "tools_ok": true,
      "json_ok": true,
      "ctx": 131072,
      "price_in": 0,
      "price_out": 0,
      "price_date": "2026-01-01",
      "scores": {"code": 0.6, "reasoning": 0.6, "chat": 0.7, "agentic_tools": 0.5},
      "retains_data": true,
      "verify": true
    },
    {
      "id": "example-local:14b",
      "aliases": ["example-local-14b"],
      "tier_hints": ["local"],
      "provider": "ollama",
      "modalities": ["text"],
      "tools_ok": true,
      "json_ok": true,
      "ctx": 32768,
      "size_gb": 9.0,
      "arch": {"n_layers": 48, "n_kv_heads": 8, "head_dim": 128},
      "scores": {"code": 0.7, "reasoning": 0.6, "chat": 0.7, "agentic_tools": 0.6},
      "expected_tps": {"laptop_12gb": 35.0, "desktop_16gb": 50.0},
      "verify": true
    },
    {
      "id": "example-local-vision:7b",
      "aliases": [],
      "tier_hints": ["local"],
      "provider": "ollama",
      "modalities": ["text", "image"],
      "tools_ok": false,
      "json_ok": true,
      "ctx": 32768,
      "size_gb": 6.0,
      "arch": {"n_layers": 28, "n_kv_heads": 4, "head_dim": 128},
      "scores": {"vision": 0.6, "chat": 0.5},
      "expected_tps": {"laptop_12gb": 45.0, "desktop_16gb": 60.0},
      "verify": true
    }
  ],
  "free_limits": [
    {"provider": "example-openrouter", "model": null, "rpm": 20, "rpd": 50, "verify": true}
  ],
  "wall_patterns": [
    {"provider": "*", "status_codes": [429], "body_regex": null, "kind": "rate_limit", "scope": "model", "verify": true},
    {"provider": "*", "status_codes": [402], "body_regex": null, "kind": "exhausted", "scope": "key", "verify": true},
    {"provider": "*", "status_codes": [503, 529], "body_regex": null, "kind": "overloaded", "scope": "model", "verify": true},
    {"provider": "*", "status_codes": [], "body_regex": "(?i)overloaded_error|\\boverloaded\\b", "kind": "overloaded", "scope": "model", "verify": true},
    {"provider": "*", "status_codes": [400, 413, 422], "body_regex": "(?i)context[_ ]length|context window|maximum context|prompt is too long|too many tokens|input is too long|input length exceeds|exceeds the (maximum|context)", "kind": "context", "scope": "model", "verify": true},
    {"provider": "openai", "status_codes": [429], "body_regex": "insufficient_quota", "kind": "exhausted", "scope": "key", "verify": true},
    {"provider": "anthropic", "status_codes": [400, 402], "body_regex": "(?i)credit balance is too low", "kind": "exhausted", "scope": "key", "verify": true},
    {"provider": "anthropic", "status_codes": [429], "body_regex": "rate_limit_error", "kind": "rate_limit", "scope": "model", "verify": true},
    {"provider": "anthropic", "status_codes": [529], "body_regex": "overloaded_error", "kind": "overloaded", "scope": "provider", "verify": true},
    {"provider": "gemini", "status_codes": [429], "body_regex": "(?is)RESOURCE_EXHAUSTED.*(per ?day|daily)", "kind": "exhausted", "scope": "model", "verify": true},
    {"provider": "gemini", "status_codes": [429], "body_regex": "RESOURCE_EXHAUSTED", "kind": "rate_limit", "scope": "model", "verify": true},
    {"provider": "openrouter", "status_codes": [402], "body_regex": "(?i)credits|afford", "kind": "exhausted", "scope": "provider", "verify": true},
    {"provider": "openrouter", "status_codes": [429], "body_regex": "(?i)free-models-per-day", "kind": "exhausted", "scope": "key", "verify": true},
    {"provider": "openrouter", "status_codes": [429], "body_regex": "(?i)rate.?limit", "kind": "rate_limit", "scope": "model", "verify": true},
    {"provider": "ollama", "status_codes": [503], "body_regex": "(?i)server busy|maximum pending requests", "kind": "overloaded", "scope": "provider", "verify": true},
    {"provider": "opencode", "status_codes": [401, 402, 429], "body_regex": "(?i)CreditsError|insufficient balance", "kind": "exhausted", "scope": "provider", "verify": true},
    {"provider": "opencode", "status_codes": [429], "body_regex": "(?i)FreeUsageLimitError|rate limit", "kind": "rate_limit", "scope": "provider", "verify": true}
  ],
  "placeholder_patterns": [
    {"name": "example-placeholder", "regex": "(?i)\\byour[_-]?api[_-]?key\\b", "verify": true}
  ],
  "gpu_profiles": {
    "laptop_12gb": {
      "vram_gb": 12,
      "recommended": {
        "agentic_coder": ["example-local:14b"],
        "general": ["example-local:14b"],
        "vision": ["example-local-vision:7b"],
        "classifier": ["example-local-vision:7b"]
      }
    },
    "desktop_16gb": {
      "vram_gb": 16,
      "recommended": {
        "agentic_coder": ["example-local:14b"],
        "general": ["example-local:14b"],
        "vision": ["example-local-vision:7b"]
      }
    }
  },
  "secret_patterns": [
    {"name": "sk-style-api-key", "regex": "\\bsk-[A-Za-z0-9_-]{20,}", "verify": true},
    {"name": "aws-access-key-id", "regex": "\\b(?:AKIA|ASIA)[A-Z0-9]{16}\\b", "verify": true},
    {"name": "github-token", "regex": "\\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})", "verify": true},
    {"name": "slack-token", "regex": "\\bxox[abprs]-[A-Za-z0-9-]{10,}", "verify": true},
    {"name": "google-api-key", "regex": "\\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])", "verify": true},
    {"name": "json-web-token", "regex": "\\beyJ[A-Za-z0-9_-]{8,}\\.eyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}", "verify": true},
    {"name": "bearer-token", "regex": "(?i)\\bBearer\\s+[A-Za-z0-9._~+/=-]{20,}", "verify": true},
    {"name": "private-key-block", "regex": "-----BEGIN [A-Z ]*PRIVATE KEY-----", "verify": true}
  ],
  "secret_placeholders": [
    {"name": "dummy-sk-key", "regex": "(?i)sk-(x{4,}|\\.{3}|your)", "verify": true},
    {"name": "filler-characters", "regex": "(?i)(x{8,}|\\*{4,}|\\.{3}|0{8,}|1234567890)", "verify": true},
    {"name": "placeholder-words", "regex": "(?i)(example|dummy|placeholder|changeme|redacted|your[_-]?(api[_-]?)?(key|token|secret))", "verify": true}
  ],
  "subscription_rates": [
    {"plan": "example-plan", "usd_per_month": 20.0, "included_credits": null,
     "price_date": "2026-01-01", "verify": true}
  ],
  "analyzer_hints": {
    "agentic_system_prompt_tokens": 1500,
    "confidence_threshold": 0.6,
    "chars_per_token_code": 3.5,
    "chars_per_token_prose": 4.0,
    "image_tokens": 800,
    "complexity_token_steps": [2000, 8000],
    "agent_user_agents": ["cline", "roo-code", "roocode", "opencode", "claude-cli", "claude-code", "continue", "aider", "kilo"],
    "reasoning_keywords": [
      "\\bprove\\b", "\\bproof\\b", "\\btheorem\\b", "\\bderive\\b", "\\barchitecture\\b",
      "\\bdesign (a|an|the)\\b", "security audit", "\\btrade-?offs?\\b", "\\bcomplexity analysis\\b"
    ],
    "duck_keywords": [
      "help me think", "\\bwhy does\\b", "\\bwhy is\\b", "explain my bug", "rubber duck",
      "\\bwalk me through\\b", "i'?m stuck"
    ],
    "risk_keywords": [
      "\\bproduction\\b", "\\bmigration\\b", "\\bdelete\\b", "\\bdrop table\\b", "\\bcredentials?\\b",
      "\\bsecurity\\b", "\\bpayments?\\b"
    ],
    "task_keywords": {
      "code": ["\\bfunction\\b", "\\bclass\\b", "\\brefactor\\b", "\\bimplement\\b", "\\bbug\\b", "\\bstack ?trace\\b", "\\bunit tests?\\b", "\\bcompile\\b"],
      "reasoning": ["\\bprove\\b", "\\bproof\\b", "\\bderive\\b", "\\btheorem\\b", "\\barchitecture\\b"],
      "summarize": ["\\bsummari[sz]e\\b", "\\btl;?dr\\b", "\\bsummary\\b"],
      "extract": ["\\bextract\\b", "\\bparse\\b", "\\bas json\\b", "\\bjson (object|array)\\b"],
      "creative": ["\\bwrite (a )?(poem|story|song)\\b", "\\bbrainstorm\\b"]
    },
    "fence_aliases": {"py": "python", "js": "javascript", "ts": "typescript", "sh": "shell", "bash": "shell", "ps1": "powershell", "rs": "rust", "yml": "yaml", "c++": "cpp", "cs": "csharp", "golang": "go"},
    "extension_languages": {
      ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
      ".rs": "rust", ".go": "go", ".java": "java", ".cs": "csharp", ".cpp": "cpp", ".cc": "cpp",
      ".c": "c", ".h": "c", ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".swift": "swift",
      ".sh": "shell", ".ps1": "powershell", ".sql": "sql", ".yaml": "yaml", ".yml": "yaml",
      ".json": "json", ".toml": "toml", ".html": "html", ".css": "css", ".md": "markdown"
    }
  },
  "prompt_bank_ref": null
}
````

## scripts/sign_catalog.py

````python
"""Owner tool: generate the catalog signing key and sign catalog releases (ed25519).

    python scripts/sign_catalog.py keygen KEYFILE        # writes private key, prints public key
    python scripts/sign_catalog.py sign KEYFILE CATALOG   # writes CATALOG.sig (base64)
    python scripts/sign_catalog.py verify PUBKEY_B64 CATALOG

Paste the printed public key into ``EMBEDDED_PUBLIC_KEY_B64`` in
``headwaters/core/catalog/trust.py``. Keep KEYFILE secret and out of the repo. Publish CATALOG
and CATALOG.sig side by side at the configured catalog URL.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_RAW = serialization.Encoding.Raw, serialization.PublicFormat.Raw


def _public_b64(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(key.public_key().public_bytes(*_RAW)).decode("ascii")


def keygen(keyfile: Path) -> int:
    if keyfile.exists():
        sys.stderr.write(f"{keyfile} already exists; refusing to overwrite.\n")
        return 1
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    keyfile.write_text(base64.b64encode(seed).decode("ascii") + "\n", encoding="ascii")
    sys.stdout.write(f"Public key (embed this): {_public_b64(key)}\n")
    return 0


def _load_private(keyfile: Path) -> Ed25519PrivateKey:
    seed = base64.b64decode(keyfile.read_text(encoding="ascii").strip(), validate=True)
    return Ed25519PrivateKey.from_private_bytes(seed)


def sign(keyfile: Path, catalog: Path) -> int:
    key = _load_private(keyfile)
    signature = base64.b64encode(key.sign(catalog.read_bytes())).decode("ascii")
    out = catalog.with_name(catalog.name + ".sig")
    out.write_text(signature + "\n", encoding="ascii")
    sys.stdout.write(f"Wrote {out}\n")
    return 0


def verify(public_b64: str, catalog: Path) -> int:
    from headwaters.core.catalog.trust import SignatureError
    from headwaters.core.catalog.trust import verify as verify_signature

    sig = catalog.with_name(catalog.name + ".sig").read_bytes()
    try:
        verify_signature(catalog.read_bytes(), sig, public_b64)
    except SignatureError as exc:
        sys.stderr.write(f"INVALID: {exc}\n")
        return 1
    sys.stdout.write("OK\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    p_keygen = sub.add_parser("keygen")
    p_keygen.add_argument("keyfile", type=Path)
    p_sign = sub.add_parser("sign")
    p_sign.add_argument("keyfile", type=Path)
    p_sign.add_argument("catalog", type=Path)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("public_key")
    p_verify.add_argument("catalog", type=Path)
    args = parser.parse_args(argv)
    if args.command == "keygen":
        return keygen(args.keyfile)
    if args.command == "sign":
        return sign(args.keyfile, args.catalog)
    return verify(args.public_key, args.catalog)


if __name__ == "__main__":
    raise SystemExit(main())
````
