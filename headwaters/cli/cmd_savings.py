"""``headwaters savings``: what requests cost, and what the reference model would have cost.

The figure is an estimate, not money in hand, and every line the command prints says so: the
reference model, the date its rates were published, and how many requests the estimate covers.
Anything the catalog cannot price is left out of the total and counted, so a reader can tell
"$X across 8 of 10 requests" from "$X across 10 of 10" (D7, HDW-501).

The command asks the feature registry for a dashboard summary instead of naming a feature, so a
machine without the ledger feature installed prints why there is no estimate and exits zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any

import typer

from headwaters.cli import runtime
from headwaters.cli.output import emit_json, make_console, table

NO_PLUGIN = (
    "No dashboard feature is installed and enabled, so there is no estimate to show. "
    "Install the ledger feature, or check `headwaters features`."
)


def _usd(value: object) -> str:
    """Six places: the finest a per-million-token rate can justify."""
    return f"${float(value or 0.0):.6f}"  # type: ignore[arg-type]


def _counts(bucket: Mapping[str, Any]) -> str:
    total = int(bucket.get("requests", 0))
    priced = int(bucket.get("priced_requests", 0))
    if total == 0:
        return "no requests"
    return f"{priced} of {total} priced"


def _bucket_rows(bucket: Mapping[str, Any]) -> list[list[str]]:
    return [
        ["Requests", str(bucket.get("requests", 0))],
        ["Priced (in the estimate)", str(bucket.get("priced_requests", 0))],
        ["Unpriced (left out)", str(bucket.get("unpriced_requests", 0))],
        ["Served free or locally", str(bucket.get("free_or_local_requests", 0))],
        ["Actual spend", _usd(bucket.get("actual_usd"))],
        ["Reference model would charge", _usd(bucket.get("counterfactual_usd"))],
        ["Estimated difference", _usd(bucket.get("estimated_savings_usd"))],
    ]


def _reference_text(reference: Mapping[str, Any] | None) -> str:
    if not reference:
        return "none"
    when = reference.get("price_date") or "undated"
    return (
        f"{reference.get('model')} "
        f"(${reference.get('price_in_usd_per_mtok')}/Mtok in, "
        f"${reference.get('price_out_usd_per_mtok')}/Mtok out, rates dated {when}; "
        f"{reference.get('source')})"
    )


def _by_day_rows(days: Sequence[Any]) -> list[list[str]]:
    return [
        [
            str(day.get("day", "")),
            str(day.get("requests", 0)),
            _counts(day),
            _usd(day.get("actual_usd")),
            _usd(day.get("counterfactual_usd")),
            _usd(day.get("estimated_savings_usd")),
        ]
        for day in days
        if isinstance(day, Mapping)
    ]


def _by_model_rows(models: Sequence[Any]) -> list[list[str]]:
    return [
        [
            str(entry.get("model", "")),
            str(entry.get("requests", 0)),
            _counts(entry),
            _usd(entry.get("actual_usd")),
            _usd(entry.get("counterfactual_usd")),
            _usd(entry.get("estimated_savings_usd")),
        ]
        for entry in models
        if isinstance(entry, Mapping)
    ]


def _print_summary(summary: Mapping[str, Any]) -> None:
    console = make_console()
    if not summary.get("available"):
        console.print(str(summary.get("label") or summary.get("reason") or NO_PLUGIN))
        return
    reference = summary.get("reference")
    console.print(
        f"Reference model: {_reference_text(reference if isinstance(reference, Mapping) else None)}"
    )
    console.print(f"Window: last {summary.get('window_days')} days")
    console.print()
    totals = summary.get("totals")
    if isinstance(totals, Mapping):
        totals_table = table("Estimate", "Measure", "Value", no_wrap=["Measure"])
        for label, value in _bucket_rows(totals):
            totals_table.add_row(label, value)
        console.print(totals_table)
    week = summary.get("week")
    if isinstance(week, Mapping):
        console.print()
        week_table = table("This week", "Measure", "Value", no_wrap=["Measure"])
        for label, value in _bucket_rows(week):
            week_table.add_row(label, value)
        console.print(week_table)
    days = summary.get("by_day") or ()
    if days:
        console.print()
        console.print(
            table(
                "By day (UTC)",
                "Day",
                "Requests",
                "Priced",
                "Actual",
                "Reference",
                "Difference",
                no_wrap=["Day", "Priced"],
            )
        )
        day_table = table(
            "By day (UTC)",
            "Day",
            "Requests",
            "Priced",
            "Actual",
            "Reference",
            "Difference",
            no_wrap=["Day", "Priced"],
        )
        for row in _by_day_rows(days):
            day_table.add_row(*row)
        console.print(day_table)
    models = summary.get("by_model") or ()
    if models:
        console.print()
        model_table = table(
            "By model",
            "Model",
            "Requests",
            "Priced",
            "Actual",
            "Reference",
            "Difference",
            no_wrap=["Priced"],
        )
        for row in _by_model_rows(models):
            model_table.add_row(*row)
        console.print(model_table)
    console.print()
    console.print(str(summary.get("label", "")))


def register(app: typer.Typer) -> None:
    @app.command("savings")
    def savings(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Estimate what the reference model would have charged for the same requests.

        An estimate of a different model's price for the same tokens, not money that was not
        spent. Requests the catalog cannot price are left out of the total and counted, so the
        figure always says how many requests it covers.
        """
        rt = runtime.load_runtime(as_json=as_json)
        summary = rt.registry.dashboard_summary()
        if summary is None:
            payload: dict[str, Any] = {
                "ok": True,
                "available": False,
                "reason": NO_PLUGIN,
            }
            if as_json:
                emit_json(payload)
            else:
                make_console().print(NO_PLUGIN)
            return
        if as_json:
            emit_json({"ok": True, **summary})
            return
        _print_summary(summary)
