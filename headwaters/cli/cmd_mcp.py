"""``headwaters mcp-path``: absolute launch paths for host MCP configs (HDW-202).

Pure path resolution: no daemon is contacted and the MCP SDK is not imported.
"""

import json

import typer

from headwaters.cli.output import emit_json
from headwaters.core.mcp_launch import config_snippet, launch_options


def register(app: typer.Typer) -> None:
    @app.command("mcp-path")
    def mcp_path(
        as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    ) -> None:
        """Show the absolute command to put in an IDE's MCP config (never a bare python)."""
        options = launch_options()
        best = options[0]
        snippet = config_snippet(best)
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "command": best.command,
                    "args": list(best.args),
                    "kind": best.kind,
                    "options": [option.as_dict() for option in options],
                    "config": snippet,
                }
            )
            return
        typer.echo(f"Command: {best.command}")
        typer.echo(f"Arguments: {' '.join(best.args) if best.args else '(none)'}")
        if len(options) > 1:
            alt = options[-1]
            typer.echo(f"Alternative: {alt.command} {' '.join(alt.args)}")
        typer.echo("Config snippet:")
        typer.echo(json.dumps(snippet, indent=2))
