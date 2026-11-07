"""``headwaters doctor``: diagnose this machine, and export a redacted bundle (HDW-402).

The checks live in ``headwaters.control.doctor`` (no Typer, no Rich) so they can be tested without
a daemon, a GPU or a network. This module only validates ``--host``, wires the probes the CLI owns
(the GPU snapshot and the reporting) and prints the fix list.
"""

from __future__ import annotations

from typing import Annotated

import typer

from headwaters.cli.output import (
    EXIT_CONFIG,
    EXIT_FAILED,
    EXIT_OK,
    emit_json,
    fail,
    make_console,
    table,
)
from headwaters.cli.runtime import feature_registry, hardware_state, load_rules_or_exit
from headwaters.control.bundle import build_bundle
from headwaters.control.doctor import HOSTS, Report, run_doctor
from headwaters.core.daemon_info import read_token
from headwaters.core.keys import KeyStore
from headwaters.core.rules import Rules

NO_CHECKS = "no checks ran"


def _secrets(rules: Rules) -> list[str]:
    """What the bundle must never contain: the daemon token and every stored key."""
    found = [read_token() or ""]
    try:
        store = KeyStore(rules)
        for entry in store.entries():
            secret = store.secret(entry.alias)
            if secret:
                found.append(secret)
    except Exception:  # a broken keyring must not stop the bundle
        pass
    return [secret for secret in found if secret]


def _render(report: Report) -> None:
    console = make_console()
    grid = table("Doctor", "Check", "Status", "Detail")
    for check in report.checks:
        grid.add_row(check.title, check.status, check.detail or NO_CHECKS)
    console.print(grid)
    if report.fixes:
        typer.echo("")
        typer.echo("Fix list:")
        for title, fix in report.fixes:
            typer.echo(f"- {title}: {fix}")


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    def doctor(
        host: Annotated[
            str | None,
            typer.Option("--host", help=f"Also test this host end to end: {', '.join(HOSTS)}."),
        ] = None,
        bundle: Annotated[
            bool,
            typer.Option("--bundle", help="Also write a redacted diagnostic zip to the state dir."),
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Check Ollama, the GPU, ports, the daemon, keys and the catalog, then list the fixes."""
        if host is not None and host not in HOSTS:
            fail(
                "unknown_host",
                f"unknown host {host!r}",
                fix="one of: " + ", ".join(HOSTS),
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        rules = load_rules_or_exit()
        report = run_doctor(
            rules,
            host=host,
            hardware=hardware_state(rules),
            registry=feature_registry(rules),
        )
        payload = report.as_dict()
        written: str | None = None
        if bundle:
            written = str(build_bundle(report, secrets=_secrets(rules)))
            payload["bundle"] = written
        if as_json:
            emit_json(payload)
        else:
            _render(report)
            if written is not None:
                typer.echo("")
                typer.echo(f"Bundle: {written}")
        raise typer.Exit(EXIT_OK if report.ok else EXIT_FAILED)
