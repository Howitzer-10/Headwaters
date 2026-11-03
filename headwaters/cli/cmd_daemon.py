"""``headwaters start|stop|daemon-status``: the shared daemon (HDW-201).

``start`` launches ``python -m headwaters.daemon`` as a detached child and waits for ``/health``;
``stop`` asks the running daemon to shut down. Neither imports the server code, so the CLI stays
free of the web framework and the MCP SDK.
"""

from typing import Annotated

import typer

from headwaters.cli.output import EXIT_FAILED, emit_json
from headwaters.core.daemon_client import DaemonError, find_running
from headwaters.core.daemon_control import ControlResult, start_daemon, stop_daemon
from headwaters.core.daemon_info import DaemonInfo


def _info_payload(info: DaemonInfo | None) -> dict[str, object] | None:
    if info is None:
        return None
    return {
        "pid": info.pid,
        "version": info.version,
        "api_url": info.api_url,
        "mcp_url": info.mcp_url,
        "ports": {
            "api": info.ports.api,
            "gateway": info.ports.gateway,
            "dashboard": info.ports.dashboard,
        },
    }


def _report(result: ControlResult, as_json: bool) -> None:
    if as_json:
        emit_json(
            {
                "ok": result.ok,
                "outcome": result.outcome,
                "message": result.message,
                "daemon": _info_payload(result.info),
            }
        )
    else:
        typer.echo(result.message, err=not result.ok)
        if result.info is not None and result.outcome in ("started", "already_running"):
            typer.echo(f"API and MCP: {result.info.mcp_url}")
    if not result.ok:
        raise typer.Exit(EXIT_FAILED)


def register(app: typer.Typer) -> None:
    @app.command("start")
    def start(
        port: Annotated[
            int | None,
            typer.Option("--port", min=1, max=65535, help="Preferred port (a free one is used)."),
        ] = None,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Start the shared daemon in the background (does nothing if it is running)."""
        _report(start_daemon(port), as_json)

    @app.command("stop")
    def stop(
        force: Annotated[
            bool,
            typer.Option(
                "--force",
                help="Kill the pid in daemon.json when the API does not answer.",
            ),
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Stop the shared daemon gracefully; --force kills it when the API is stuck."""
        _report(stop_daemon(force=force), as_json)

    @app.command("daemon-status")
    def daemon_status(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show the running daemon (ports, uptime, keep-warm). No model is called."""
        client = find_running()
        if client is None:
            if as_json:
                emit_json({"ok": True, "running": False})
            else:
                typer.echo("The daemon is not running. Start it with: headwaters start")
            return
        try:
            status = client.status()
        except DaemonError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(EXIT_FAILED) from exc
        if as_json:
            emit_json({"ok": True, "running": True, "daemon": _info_payload(client.info), **status})
            return
        typer.echo(f"Daemon running (pid {status.get('pid')}, version {status.get('version')})")
        typer.echo(f"API and MCP: {client.info.mcp_url}")
        ports = status.get("ports") or {}
        typer.echo(f"Ports: gateway {ports.get('gateway')}, dashboard {ports.get('dashboard')}")
        keep_warm = status.get("keep_warm")
        if keep_warm:
            typer.echo(f"Keep-warm: {keep_warm.get('action')} ({keep_warm.get('reason')})")
