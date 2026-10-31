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
