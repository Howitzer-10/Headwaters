"""``headwaters report``: preview the opt-in usage report, then decide about it (HDW-508).

The order is deliberate and enforced here rather than described in prose: ``preview`` prints the
exact bytes and records that the user has seen payload version 1, and ``enable`` refuses to turn
the report on until that record exists. A switch that can be flipped without seeing its payload is
a switch nobody can consent to.

``send`` is a one-shot and works with the report switched off — "send this once" is not "send this
weekly" — but it obeys the same preview record, so nothing leaves before the user has seen it.

Every command works whether or not the ★ package is installed: when it is absent the state is
reported as ``discovered: False`` with the reason, rather than the command disappearing.
"""

from __future__ import annotations

import importlib
from typing import Annotated, Any

import typer

from headwaters.cli import runtime
from headwaters.cli.output import (
    EXIT_CONFIG,
    EXIT_FAILED,
    EXIT_OK,
    emit_json,
    fail,
    make_console,
    table,
)
from headwaters.core import rules_edit
from headwaters.core.rules import ReportRules
from headwaters.core.usage_report import (
    REPORT_FLAG,
    preview_report,
    report_status,
    send_report_now,
)

PREVIEW_FIRST = (
    "Run `headwaters report preview` first, look at the payload, and then enable the report."
)

REASONS: dict[str, str] = {
    "delivered": "The report was sent.",
    "not_configured": (
        "Nothing was sent: no endpoint is configured (`report.endpoint` is null). "
        "This build ships with no collector, which is why the no-telemetry notice still holds."
    ),
    "not_consented": (
        "Nothing was sent: this payload shape has not been previewed on this machine. "
        "Run `headwaters report preview`."
    ),
    "shape_rejected": "Nothing was sent: the payload failed its own allow-list check.",
    "unavailable": "Nothing was sent: the endpoint could not be reached.",
    "rejected": "The endpoint answered, but refused the report.",
    "off": "Nothing was sent: the usage report is switched off.",
    "not_due": "Nothing was sent: the last report is newer than the interval.",
    "not_installed": "Nothing was sent: the ★ usage_report package is not installed.",
}


def _print_status(status: dict[str, Any]) -> None:
    console = make_console()
    grid = table("Usage report", "Setting", "Value", no_wrap=["Setting"])
    grid.add_row("State", "on" if status.get("enabled") else "off (the shipped default)")
    grid.add_row(
        "★ package",
        "installed" if status.get("discovered") else "not installed",
    )
    endpoint = status.get("endpoint")
    grid.add_row(
        "Endpoint",
        str(endpoint) if status.get("endpoint_set") else "none (nothing can be sent)",
    )
    grid.add_row(
        "Payload",
        f"version {status.get('payload_version')}, {status.get('fields')} counted fields, "
        "none free text"
        if status.get("payload_version")
        else "no payload can be built",
    )
    grid.add_row(
        "Previewed",
        "yes" if status.get("consent_current") else "no — run `headwaters report preview`",
    )
    last_sent = status.get("last_sent")
    grid.add_row("Last sent", "never" if last_sent is None else f"{last_sent:.0f} (epoch seconds)")
    next_due = status.get("next_due")
    grid.add_row(
        "Next due",
        "nothing scheduled" if next_due is None else f"in {float(next_due) / 3600.0:.1f} h",
    )
    survey = status.get("survey")
    grid.add_row(
        "Feedback prompt",
        f"owed at {survey.get('stage')} active days" if isinstance(survey, dict) else "none owed",
    )
    console.print(grid)
    if not status.get("discovered"):
        console.print(str(status.get("note") or ""))
    console.print(
        "Automatic background reporting is off by design (`telemetry: false`). "
        "This report is a separate switch you have to turn on."
    )


def _print_preview(preview: dict[str, Any]) -> None:
    console = make_console()
    console.print("This is the exact payload. Nothing else is sent, and no field is free text:")
    typer.echo(str(preview.get("json", "{}")))
    console.print("")
    shape = table("Allow-list", "Field", "Allowed value", no_wrap=["Field"])
    for path, rule in preview.get("shape") or []:
        shape.add_row(str(path), str(rule))
    console.print(shape)
    console.print("")
    endpoint = preview.get("endpoint")
    if endpoint:
        console.print(f"Endpoint: {endpoint}")
    else:
        console.print("Endpoint: none, so nothing can be sent even if you switch the report on.")
    console.print(
        "Shown and recorded: `headwaters report enable --i-have-seen-the-payload` "
        "may now switch it on."
    )


def _survey_module() -> Any | None:
    """The ★ survey module, or ``None`` when the package is not installed."""
    try:
        return importlib.import_module("headwaters.features.usage_report.survey")
    except ImportError:
        return None


def register(app: typer.Typer) -> None:
    @app.command("status")
    def status(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show the switch, the endpoint, the last send and the payload version.

        Always printed, whether the report is on or off and whether or not the ★ package is
        installed: PRIVACY.md promises the toggle is visible before it is used.
        """
        rt = runtime.load_runtime(as_json=as_json)
        described = report_status(rt.registry)
        if as_json:
            emit_json({"ok": True, **described})
            return
        _print_status(described)

    @app.command("preview")
    def preview(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Print the exact payload a send would post, and record that you have seen it.

        Records the payload *version*, not a bare "yes": if a later release changes the shape, the
        version changes and scheduling stops until you look again.
        """
        rt = runtime.load_runtime(as_json=as_json)
        shown = preview_report(rt.registry)
        if shown is None:
            fail(
                "not_installed",
                "The ★ usage_report package is not installed, so there is no payload to preview.",
                fix="Install the feature package, or check `headwaters features`.",
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        from headwaters.features.usage_report import state as report_state

        report_state.record_consent(
            rt.db, payload_version=int(str(shown.get("payload_version", 0)))
        )
        if as_json:
            emit_json({"ok": True, **shown})
            return
        _print_preview(shown)

    @app.command("enable")
    def enable(
        seen: Annotated[
            bool,
            typer.Option(
                "--i-have-seen-the-payload",
                help="Confirm you have read the payload `headwaters report preview` printed.",
            ),
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Turn the weekly report on. Requires a preview of this payload version first."""
        rt = runtime.load_runtime(as_json=as_json)
        described = report_status(rt.registry)
        if not described.get("discovered"):
            fail(
                "not_installed",
                "The ★ usage_report package is not installed, so there is nothing to enable.",
                fix="Install the feature package, or check `headwaters features`.",
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        if not described.get("consent_current") or not seen:
            fail(
                "preview_required",
                "The usage report cannot be switched on until you have seen its payload.",
                fix=PREVIEW_FIRST,
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        rules_edit.set_nested("features", REPORT_FLAG, True)
        rules = runtime.load_rules_or_exit()
        payload: dict[str, Any] = {
            "ok": True,
            "feature": REPORT_FLAG,
            "enabled": True,
            "endpoint": rules.report.endpoint,
            "sends": bool(rules.report.endpoint),
        }
        if as_json:
            emit_json(payload)
            return
        console = make_console()
        console.print("The usage report is on.")
        if not rules.report.endpoint:
            console.print(
                "No endpoint is configured, so nothing will be sent until you set one "
                "(`headwaters report endpoint`)."
            )
        else:
            console.print(f"It will send about every {rules.report.interval_days} days.")

    @app.command("disable")
    def disable(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Turn the weekly report off. The preview record is kept, so `send` still works once."""
        runtime.load_runtime(as_json=as_json)
        rules_edit.set_nested("features", REPORT_FLAG, False)
        if as_json:
            emit_json({"ok": True, "feature": REPORT_FLAG, "enabled": False})
            return
        make_console().print(
            "The usage report is off. Nothing is sent automatically. "
            "`headwaters report send` still sends one report if you ask it to."
        )

    @app.command("send")
    def send(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Send one report now, whether the report is on or off. Does not switch it on."""
        rt = runtime.load_runtime(as_json=as_json)
        outcome = send_report_now(rt.registry)
        reason = str(outcome.get("reason", "unavailable"))
        message = REASONS.get(reason, f"Nothing was sent ({reason}).")
        if as_json:
            emit_json({"ok": bool(outcome.get("sent")), **outcome, "message": message})
        else:
            make_console().print(message)
        if outcome.get("sent"):
            raise typer.Exit(EXIT_OK)
        code = EXIT_CONFIG if reason == "not_consented" else EXIT_OK
        raise typer.Exit(code if reason not in {"shape_rejected", "rejected"} else EXIT_FAILED)

    @app.command("endpoint")
    def endpoint(
        url: Annotated[
            str | None, typer.Argument(help="The https:// URL to post reports to.")
        ] = None,
        clear: Annotated[
            bool, typer.Option("--clear", help="Remove the endpoint, so nothing can be sent.")
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Set (or clear) where reports go. Must be https; no collector ships in this build."""
        if url is not None and clear:
            fail(
                "ambiguous",
                "Give a URL or --clear, not both.",
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        if url is None and not clear:
            fail(
                "missing_url",
                "Give an https:// URL, or --clear to remove the one that is set.",
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        if url is not None:
            try:
                ReportRules(endpoint=url)
            except ValueError as exc:
                fail(
                    "invalid_endpoint",
                    str(exc),
                    fix="The endpoint must be an https:// URL.",
                    exit_code=EXIT_CONFIG,
                    as_json=as_json,
                )
        runtime.load_runtime(as_json=as_json)
        rules_edit.set_nested("report", "endpoint", None if clear else url)
        if as_json:
            emit_json({"ok": True, "endpoint": None if clear else url})
            return
        console = make_console()
        console.print(
            "No endpoint is set, so nothing can be sent."
            if clear
            else f"Endpoint set to {url}. Reports go there only after you preview and enable."
        )
        if not clear:
            console.print(
                "Note: no collector ships with Headwaters. Pointing this at a real one is a "
                "release decision, not a setting."
            )

    @app.command("survey")
    def survey(
        dismiss: Annotated[
            bool, typer.Option("--dismiss", help="Never ask for product feedback again.")
        ] = False,
        reset: Annotated[
            bool, typer.Option("--reset", help="Forget the prompt history (asks again later).")
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show, dismiss or reset the product-feedback prompt (at most two prompts, ever)."""
        rt = runtime.load_runtime(as_json=as_json)
        module = _survey_module()
        if module is None:
            fail(
                "not_installed",
                "The ★ usage_report package is not installed, so there is no prompt to show.",
                exit_code=EXIT_CONFIG,
                as_json=as_json,
            )
        if dismiss:
            module.dismiss(rt.db)
            if as_json:
                emit_json({"ok": True, "dismissed": True})
            else:
                make_console().print("You will not be asked for product feedback again.")
            return
        if reset:
            module.reset(rt.db)
        owed = module.prompt(rt.db)
        if as_json:
            emit_json({"ok": True, "prompt": owed})
            return
        if owed is None:
            make_console().print("No product-feedback prompt is owed.")
            return
        module.record_shown(rt.db, int(owed["stage"]))
        console = make_console()
        console.print(str(owed["question"]))
        console.print(str(owed["how"]))
        console.print(
            f"{int(owed['remaining'])} of 2 prompts used after this one. "
            "`headwaters report survey --dismiss` stops them for good."
        )
