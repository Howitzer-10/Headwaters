"""``headwaters install`` and ``headwaters uninstall``: put Headwaters into a host (HDW-403).

Every host is shown as a diff first, backed up, written, and read back. Nothing is written without
a yes, an unknown host is a usage error, and a machine with no launcher that exists is refused
before a single file is touched (FU-202-5). ``--dry-run`` shows the plan and stops; ``--json`` is
for scripts and never prompts, so it cannot approve anything by accident.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from headwaters.cli.output import emit_json, fail
from headwaters.core.mcp_launch import MissingLauncher
from headwaters.installer import engine
from headwaters.installer.base import HostAdapter
from headwaters.installer.context import Context, default_context
from headwaters.installer.hosts import all_adapters, get_host, host_ids

__all__ = ["register"]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
HOST_HELP = f"Host id: {', '.join(host_ids())}, or all."
PROJECT_HELP = "The project folder the host reads. Default: the current one."


def register(app: typer.Typer) -> None:
    @app.command("install")
    def install(
        host: Annotated[str, typer.Option("--host", help=HOST_HELP)] = "all",
        project: Annotated[Path | None, typer.Option("--project", help=PROJECT_HELP)] = None,
        yes: Annotated[bool, typer.Option("--yes", "-y", help="Write without asking.")] = False,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the diff and stop.")] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Write the MCP entry, the BYOK endpoint and the command files a host needs."""
        chosen = _choose(host, as_json=as_json)
        ctx = _context(project, as_json=as_json)
        results = [
            _install_one(adapter, ctx, yes=yes, dry_run=dry_run, quiet=as_json)
            for adapter in chosen
        ]
        _report(results, as_json=as_json)

    @app.command("uninstall")
    def uninstall(
        host: Annotated[str, typer.Option("--host", help=HOST_HELP)] = "all",
        project: Annotated[Path | None, typer.Option("--project", help=PROJECT_HELP)] = None,
        yes: Annotated[bool, typer.Option("--yes", "-y", help="Restore without asking.")] = False,
        force: Annotated[
            bool,
            typer.Option("--force", help="Restore a file even if it was edited after the install."),
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Put back every file this installer changed, from the backups it kept."""
        chosen = _choose(host, as_json=as_json)
        ctx = _context(project, as_json=as_json)
        results = [
            _uninstall_one(adapter, ctx, yes=yes, force=force, quiet=as_json) for adapter in chosen
        ]
        _report(results, as_json=as_json)


def _choose(host: str, *, as_json: bool) -> list[HostAdapter]:
    name = host.strip().lower()
    if name and name != "all":
        adapter = get_host(name)
        if adapter is None:
            fail(
                "unknown_host",
                f"unknown host {host!r}",
                fix="one of: " + ", ".join([*host_ids(), "all"]),
                exit_code=EXIT_USAGE,
                as_json=as_json,
            )
        return [adapter]
    return list(all_adapters())


def _context(project: Path | None, *, as_json: bool) -> Context:
    try:
        return default_context(project)
    except MissingLauncher as exc:
        fail("no_launcher", str(exc), exit_code=EXIT_USAGE, as_json=as_json)
        raise  # unreachable; fail() exits


def _install_one(
    adapter: HostAdapter, ctx: Context, *, yes: bool, dry_run: bool, quiet: bool
) -> engine.HostResult:
    planned = engine.plan(adapter, ctx)
    if not quiet:
        typer.echo(f"\n{adapter.title}  ({adapter.id})")
        typer.echo(planned.diff())
    if not planned.pending:
        if not quiet:
            typer.echo("  already installed: nothing to change")
        return planned
    if dry_run:
        planned.notes = [*planned.notes, "dry run: nothing was written"]
        if not quiet:
            _echo_notes(planned)
        return planned
    question = f"Write these {len(planned.pending)} change(s) for {adapter.title}?"
    approved = yes
    if not approved:
        if quiet:
            planned.notes = [*planned.notes, "nothing was written: --json needs --yes"]
            return planned
        approved = typer.confirm(question, default=False)
    if not approved:
        planned.notes = [*planned.notes, "nothing was written: you said no"]
        if not quiet:
            _echo_notes(planned)
        return planned
    done = engine.apply(adapter, ctx)
    done.notes = planned.notes
    if not quiet:
        _show_result(done)
    return done


def _uninstall_one(
    adapter: HostAdapter, ctx: Context, *, yes: bool, force: bool, quiet: bool
) -> engine.HostResult:
    approved = yes
    if not approved:
        if quiet:
            planned = engine.plan(adapter, ctx)
            planned.notes = [*planned.notes, "nothing was restored: --json needs --yes"]
            return planned
        question = f"Restore the backup of every file the {adapter.title} install changed?"
        approved = typer.confirm(question, default=False)
    if not approved:
        planned = engine.plan(adapter, ctx)
        planned.notes = [*planned.notes, "nothing was restored: you said no"]
        _echo_notes(planned)
        return planned
    return engine.uninstall(adapter, ctx, force=force)


def _echo_notes(result: engine.HostResult) -> None:
    for line in result.notes:
        typer.echo(f"  note       {line}")


def _show_result(result: engine.HostResult) -> None:
    for path in result.written:
        typer.echo(f"  wrote      {path}")
    for path in result.restored:
        typer.echo(f"  restored   {path}")
    if result.written or result.restored:
        typer.echo(f"  checked    {'ok' if result.verified else 'NOT verified'}")
    for line in result.problems:
        typer.echo(f"  problem    {line}")
    for line in result.notes:
        typer.echo(f"  note       {line}")
    for line in result.errors:
        typer.echo(f"  error      {line}", err=True)


def _report(results: list[engine.HostResult], *, as_json: bool) -> None:
    errors = [line for result in results for line in result.errors]
    changed = any(result.written or result.restored for result in results)
    if as_json:
        emit_json(
            {
                "ok": not errors,
                "changed": changed,
                "results": [result.as_dict() for result in results],
            }
        )
    else:
        typer.echo("")
        typer.echo("Done." if changed else "Nothing changed.")
    if errors:
        raise typer.Exit(EXIT_FAILED)
    raise typer.Exit(EXIT_OK)
