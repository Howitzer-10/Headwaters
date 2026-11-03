"""``headwaters release-gate`` and ``headwaters release-docs``: the Beta 0.1 gate (HDW-407).

The checks live in ``headwaters.control.release_gate`` and the page renderer in
``headwaters.control.release_docs`` (neither imports Typer), so a test can run the whole gate
against a throwaway tree. This module only resolves the checkout, prints and exits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from headwaters.cli.output import EXIT_CONFIG, EXIT_FAILED, EXIT_OK, emit_json, fail
from headwaters.control import release_docs
from headwaters.control.release_gate import EvidenceError, as_dict, render, run_gate

REPO_ROOT_HELP = "The Headwaters source checkout. Default: the current folder."


def _checkout(value: Path | None, *, option: str, as_json: bool) -> Path:
    root = (value or Path.cwd()).expanduser()
    if not (root / "pyproject.toml").is_file() or not (root / "headwaters").is_dir():
        fail(
            "not_a_checkout",
            f"{root} is not a Headwaters source checkout",
            fix=f"run from the repo root or pass {option} <path>",
            exit_code=EXIT_CONFIG,
            as_json=as_json,
        )
    return root.resolve()


def _path(value: Path | None) -> Path | None:
    return value.expanduser() if value is not None else None


def register(app: typer.Typer) -> None:
    @app.command("release-gate")
    def release_gate(
        repo_root: Annotated[Path | None, typer.Option("--repo-root", help=REPO_ROOT_HELP)] = None,
        evidence: Annotated[
            Path | None,
            typer.Option("--evidence", help="Owner evidence. Default: docs/release/evidence.json."),
        ] = None,
        feed: Annotated[
            Path | None,
            typer.Option("--feed", help="A candidate signed catalog to check against FU-121-3."),
        ] = None,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Prove beta 0.1 is ready: docs, the catalog rule, and every owner step recorded."""
        root = _checkout(repo_root, option="--repo-root", as_json=as_json)
        try:
            result = run_gate(root, evidence_path=_path(evidence), feed_path=_path(feed))
        except EvidenceError as exc:
            fail(
                "bad_evidence",
                str(exc),
                fix="see docs/RELEASE_EVIDENCE.md for the shape",
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        if as_json:
            emit_json(as_dict(result))
        else:
            typer.echo(render(result))
        raise typer.Exit(EXIT_OK if result.ready else EXIT_FAILED)

    @app.command("release-docs")
    def release_docs_command(
        root_option: Annotated[Path | None, typer.Option("--root", help=REPO_ROOT_HELP)] = None,
        write: Annotated[
            bool, typer.Option("--write", help="Rewrite the pages instead of checking them.")
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Check (or regenerate) the per-host setup pages from the installer and its templates."""
        checkout = _checkout(root_option, option="--root", as_json=as_json)
        stale = release_docs.stale_pages(checkout)
        written = release_docs.write_pages(checkout) if write else []
        if as_json:
            emit_json(
                {
                    "ok": write or not stale,
                    "root": str(checkout),
                    "stale": stale,
                    "written": written,
                }
            )
        elif write:
            for name in written:
                typer.echo(f"wrote {name}")
        else:
            for name in stale:
                typer.echo(f"stale: {name}")
            typer.echo(
                "up to date" if not stale else f"{len(stale)} page(s) stale; run with --write"
            )
        raise typer.Exit(EXIT_OK if write or not stale else EXIT_FAILED)
