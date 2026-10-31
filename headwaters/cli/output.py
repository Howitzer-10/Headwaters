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
