"""Headwaters command line (HDW-118).

Commands live in ``cmd_*`` modules and register themselves here. Every command that prints data
has ``--json`` for scripts; human output uses Rich tables. Exit codes: 0 ok, 1 the command
failed, 2 invalid config or usage, 3 the request cannot be served inside the scope, 4 no model
can serve it right now.
"""

import typer

import headwaters
from headwaters.cli import (
    cmd_catalog,
    cmd_daemon,
    cmd_doctor,
    cmd_features,
    cmd_install,
    cmd_keys,
    cmd_mcp,
    cmd_models,
    cmd_pick,
    cmd_release,
    cmd_scope,
    cmd_status,
    cmd_tray,
)
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
keys_app = typer.Typer(help="API keys: save them in the OS credential store, and test them.")
app.add_typer(models_app, name="models")
app.add_typer(catalog_app, name="catalog")
app.add_typer(keys_app, name="keys")


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
cmd_daemon.register(app)
cmd_doctor.register(app)
cmd_mcp.register(app)
cmd_install.register(app)
cmd_release.register(app)
cmd_scope.register(app)
cmd_features.register(app)
cmd_tray.register(app)
cmd_models.register(models_app)
cmd_catalog.register(catalog_app)
cmd_keys.register(keys_app)
