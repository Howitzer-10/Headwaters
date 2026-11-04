"""``headwaters tray``: run the tray icon by hand (HDW-504).

The logon task runs ``headwaters start``, and the daemon starts the tray as its child when the
``tray`` feature is on. This command is the same tray, started by a person. With the feature off
it says how to turn it on instead of failing silently - a switched-off feature is a choice, not
an error.
"""

from __future__ import annotations

import typer

from headwaters.cli import runtime
from headwaters.features import tray as tray_feature

HINT = (
    "the tray is switched off: set `features: {"
    + tray_feature.FEATURE_ID
    + ": true}` in rules.yaml, then run `headwaters tray` again"
)


def register(app: typer.Typer) -> None:
    @app.command("tray")
    def tray_command() -> None:
        """Run the tray icon (Windows notification area)."""
        rules = runtime.load_rules_or_exit()
        if not rules.features.get(tray_feature.FEATURE_ID, tray_feature.DEFAULT):
            typer.echo(HINT)
            raise typer.Exit(code=0)  # not an error: the user turned it off
        raise typer.Exit(code=tray_feature.run())
