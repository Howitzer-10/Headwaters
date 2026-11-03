"""``headwaters features``: the core and ★ feature list with the legend.

Core features are free forever (a public promise, v3.5 section B); ★ features are free during the
beta and may become part of Headwaters Pro at 1.0. Core capabilities are a fixed list here so the
split is always visible, even before any ★ package is installed.
"""

from typing import Annotated

import typer

from headwaters.cli import runtime
from headwaters.cli.output import emit_json, make_console, table
from headwaters.core.features import (
    CORE_FEATURES,
    STAR_LEGEND,
    FeatureRegistry,
    FeatureSpec,
    discover_features,
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
