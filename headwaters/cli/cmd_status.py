"""``headwaters status``: scope, locks, walls with ETAs and the warm model.

The table describes the daemon that is running *now*, so the command asks that daemon first: a
``/health`` answer on the API port that names the pid in ``daemon.json``. Nothing printed below is
cached state presented as live - when the daemon does not answer, the command says so and stops.
"""

import time
import webbrowser
from typing import Annotated, Any

import typer

from headwaters.cli import runtime
from headwaters.cli.output import emit_json, fail, fmt_eta, make_console, table
from headwaters.core.daemon_client import find_running
from headwaters.core.daemon_control import pid_alive, start_daemon
from headwaters.core.daemon_info import read_info, remove_info
from headwaters.core.quotas import wall_label
from headwaters.core.rules import tier_order_label
from headwaters.core.scope import ScopeDecision, ScopeError, paid_scopes_available, resolve_scope
from headwaters.core.state_limits import WallRow, active_walls
from headwaters.core.state_models import ModelRow, list_models

_KIND_TEXT = {
    "rate_limit": "rate limited",
    "exhausted": "out of credit",
    "overloaded": "overloaded",
}

DASHBOARD_FIX = (
    "Set dashboard.enabled: true in rules.yaml, or start the daemon by hand and read the "
    "dashboard port from `headwaters daemon-status`."
)
DAEMON_DOWN_FIX = "Start it with `headwaters start`, then run `headwaters status` again."


def live_daemon() -> tuple[Any, str | None]:
    """The client for the daemon that answers ``/health`` now, or ``None`` and a note.

    Liveness is an actual ``/health`` probe on the API port that names the same pid - the test the
    tray and every shim use - never the existence of ``daemon.json``. When the file survives the
    process it names, it is removed here: a stale pid file must not keep faking a live daemon.
    """
    client = find_running()
    if client is not None:
        return client, None
    return None, _clear_stale_info()


def _clear_stale_info() -> str | None:
    """Delete ``daemon.json`` when the pid it names is gone. Returns a line about what happened."""
    info = read_info()
    if info is None or pid_alive(info.pid):
        return None
    removed = remove_info(info.pid)
    note = f"the process in daemon.json (pid {info.pid}) is gone"
    return f"Removed a stale daemon.json: {note}." if removed else f"daemon.json is stale: {note}."


def status_page_url(
    rt: runtime.Runtime,
    *,
    find: Any = find_running,
    start: Any = start_daemon,
) -> tuple[str | None, str | None]:
    """The status page URL, starting the daemon if needed. Returns ``(url, problem)``.

    ``--open`` is what the Start Menu shortcut and the itch "Open status page" action run, so it
    has to work on a cold machine: the daemon is started and its bound dashboard port is read
    from ``daemon.json`` rather than assumed from the rules.
    """
    if not rt.rules.dashboard.enabled:
        return None, "the dashboard is disabled in rules (dashboard.enabled: false)"
    client = find()
    if client is None:
        started = start(None)
        if not started.ok or started.info is None:
            return None, started.message
        client = find()
    if client is None:
        return None, "the daemon started but did not report its ports"
    url = client.info.dashboard_url
    if url is None:
        return None, "the daemon is not serving the dashboard on any port"
    return url, None


def _open_status_page(rt: runtime.Runtime, *, as_json: bool) -> None:
    url, problem = status_page_url(rt)
    if url is None:
        fail(
            "no_dashboard",
            problem or "the status page is unavailable",
            fix=DASHBOARD_FIX,
            as_json=as_json,
        )
    if as_json:
        emit_json({"ok": True, "url": url})
        return
    typer.echo(f"Status page: {url}")
    webbrowser.open(url)


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
    """Every lock in force. ``decision.active_locks`` is authoritative (FU-128-1).

    Without a decision (an unsatisfiable scope) the locks still apply, so they are read from the
    rules: the scope itself could not be resolved, not the locks.
    """
    if decision is not None:
        return list(decision.active_locks)
    found = ["private_lock"] if rt.rules.private_lock else []
    if rt.rules.airplane:
        found.append("airplane")
    return found


def _wall_eta(row: WallRow, now: float) -> float | None:
    return None if row.until is None else max(0.0, row.until - now)


def _wall_payload(row: WallRow, now: float) -> dict[str, Any]:
    return {
        "subject": row.subject,
        "label": wall_label(row.subject),
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
        open_page: Annotated[
            bool,
            typer.Option(
                "--open",
                help="Start the daemon if needed and open the status page in the browser.",
            ),
        ] = False,
    ) -> None:
        """Show the scope in force, locks, walls with their reset times, and the warm model.

        With --open, do not print the table: start the daemon if it is not running and open the
        dashboard page the daemon is serving. Without a daemon answering ``/health``, there is no
        live status to show: the command says so and stops instead of printing cached state.
        """
        rt = runtime.load_runtime(as_json=as_json)
        if open_page:
            _open_status_page(rt, as_json=as_json)
            return
        client, stale_note = live_daemon()
        if client is None:
            message = "The daemon is not running, so there is no live status to show."
            if stale_note:
                message = f"{message} {stale_note}"
            fail("daemon_not_running", message, fix=DAEMON_DOWN_FIX, as_json=as_json)
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
                    "tier_order": rt.rules.router.tier_order,
                    "tier_direction": rt.rules.router.tier_direction,
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
        top.add_row(
            "Fallback order",
            tier_order_label(rt.rules.router.tier_order, rt.rules.router.tier_direction),
        )
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
            wall_table = table("Walls", "Subject", "State", "Ends in", "Hits", no_wrap=["Subject"])
            for row in walls:
                eta = _wall_eta(row, now)
                wall_table.add_row(
                    wall_label(row.subject),
                    _KIND_TEXT.get(row.kind, row.kind),
                    # HDW-122 gives every wall an end time; a row without one is unknown, not
                    # "after a balance check" (FU-122-4).
                    fmt_eta(eta) if eta is not None else "unknown",
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
