"""``headwaters feedback``: the product-feedback form, with an optional redacted bundle (HDW-508).

This is *product* feedback — what you think of Headwaters. It is not ``outcomes.user_feedback``,
which is your rating of one answer and lives in the state database next to the decision it scores
(D18/D19: the two are always written apart, so a grep can tell them apart).

No feedback collector ships in this build, so the note is written to a directory you own and
printed back to you. ``--send`` is therefore refused rather than silently ignored: the usage
report's allow-list exists precisely to keep free text off the wire, and a feedback body is free
text, so it must never ride that payload (HDW-508's schema test asserts the report has no string
fields at all).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import typer

from headwaters.cli import runtime
from headwaters.cli.output import EXIT_CONFIG, EXIT_OK, emit_json, fail, make_console
from headwaters.core.paths import state_dir

BUNDLE_NOTE = (
    "The bundle is the same redacted diagnostics archive as `headwaters doctor --bundle`: "
    "counts and versions, with your keys and tokens scrubbed out."
)


def _feedback_dir() -> Path:
    return state_dir() / "product-feedback"


def _write(message: str, bundle: Path | None, now: float) -> Path:
    """Write the note and return its path. One directory per note, so bundles never collide."""
    target = _feedback_dir() / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    target.mkdir(parents=True, exist_ok=True)
    note = target / "feedback.md"
    body = [
        "# Headwaters product feedback",
        "",
        f"Written {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))} on this machine.",
        "Nothing here was sent anywhere: this build has no feedback collector.",
        "",
        "## What I think",
        "",
        message.strip(),
        "",
    ]
    if bundle is not None:
        body += ["## Attached redacted bundle", "", f"`{bundle.name}` in this folder.", ""]
    note.write_text("\n".join(body), encoding="utf-8")
    return note


def register(app: typer.Typer) -> None:
    @app.command("feedback")
    def feedback(
        message: Annotated[
            str | None,
            typer.Argument(help="Your product feedback, in your own words."),
        ] = None,
        bundle: Annotated[
            bool,
            typer.Option("--bundle", help=f"Attach a redacted diagnostics bundle. {BUNDLE_NOTE}"),
        ] = False,
        send: Annotated[
            bool,
            typer.Option("--send", help="Ask to send it (refused: this build has no collector)."),
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Write product feedback to a folder you own, optionally with a redacted bundle.

        There is no telemetry behind this: nothing is posted anywhere, and the folder path is the
        only thing that leaves with you.
        """
        text = message if message is not None else typer.prompt("What should Headwaters know")
        if not text or not text.strip():
            fail(
                "empty_feedback",
                "There is nothing to write: the feedback was empty.",
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        if send:
            fail(
                "no_collector",
                "This build has no feedback collector, so nothing was sent.",
                fix=(
                    "The note is saved locally instead; attach it wherever you report issues. "
                    "Sending free text would also break the usage report's no-free-text allow-list."
                ),
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        rt = runtime.load_runtime(as_json=as_json)
        from headwaters.cli.cmd_doctor import _secrets
        from headwaters.control.bundle import build_bundle
        from headwaters.control.doctor import run_doctor

        now = time.time()
        made: Path | None = None
        if bundle:
            report = run_doctor(rt.rules, host=None, hardware=None, registry=rt.registry)
            made = build_bundle(report, secrets=_secrets(rt.rules))
        note = _write(text, made, now)
        payload = {
            "ok": True,
            "note": str(note),
            "bundle": None if made is None else str(made),
            "sent": False,
            "product_feedback": True,
            "rates_an_answer": False,
        }
        if as_json:
            emit_json(payload)
        else:
            console = make_console()
            console.print(f"Saved to {note}")
            if made is not None:
                console.print(f"Redacted bundle: {made}")
            console.print("Nothing was sent: this build has no feedback collector.")

        from headwaters.cli.cmd_report import _survey_module

        module = _survey_module()
        if module is None:
            raise typer.Exit(EXIT_OK)
        owed = module.prompt(rt.db)
        if owed is None:
            raise typer.Exit(EXIT_OK)
        module.record_shown(rt.db, int(owed["stage"]))
        if not as_json:
            console = make_console()
            console.print("")
            console.print(str(owed["question"]))
            console.print(str(owed["how"]))
            console.print(
                f"{int(owed['remaining'])} of 2 prompts used after this one; `headwaters report "
                "survey --dismiss` stops them for good."
            )
        raise typer.Exit(EXIT_OK)
