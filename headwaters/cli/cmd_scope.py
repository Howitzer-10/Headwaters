"""``headwaters scope`` and ``headwaters private``: the user's switches for scope and the lock.

Both edit ``rules.yaml`` in place (comments are kept) and the running engines pick the change up
through the rules hot reload. Only the user loosens a lock, and the CLI is the user: it never
does so on behalf of a request. A project rule or the Private lock still wins over a looser
default scope, and the command says so.
"""

from typing import Annotated, Any, Literal

import typer

from headwaters.cli import runtime
from headwaters.cli.cmd_status import effective_scope, locks_in_force
from headwaters.cli.output import EXIT_CONFIG, emit_json, fail, make_console
from headwaters.core.rules import Scope
from headwaters.core.rules_edit import set_setting
from headwaters.core.rules_io import ConfigError

Switch = Literal["on", "off"]


def _effective_payload(rt: runtime.Runtime) -> tuple[dict[str, Any], str]:
    decision, error = effective_scope(rt)
    locks = locks_in_force(rt, decision)
    if decision is None:
        message = error.message if error else "no scope can be served"
        return {
            "scope": None,
            "tiers": [],
            "locks": locks,
            "error": error.as_dict() if error else None,
        }, (f"Effective scope: none. {message}" + (f"\nfix: {error.fix_hint}" if error else ""))
    tiers = sorted(decision.tiers)
    line = f"Effective scope: {decision.scope} (tiers: {', '.join(tiers)})"
    if locks:
        line += f"; locks in force: {', '.join(locks)}"
    return {"scope": decision.scope, "tiers": tiers, "locks": locks, "error": None}, line


def _apply(key: str, value: bool | str, as_json: bool) -> tuple[Any, Any]:
    try:
        return set_setting(key, value)
    except ConfigError as exc:
        fail(
            "invalid_rules",
            str(exc),
            fix="Fix the problems listed above in rules.yaml, then run the command again.",
            exit_code=EXIT_CONFIG,
            as_json=as_json,
        )


def register(app: typer.Typer) -> None:
    @app.command("scope")
    def scope_command(
        value: Annotated[
            Scope | None,
            typer.Argument(help="auto | free | local | cloud. Omit to show the current scope."),
        ] = None,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show or set the default scope: which tiers may serve requests."""
        previous = None
        if value is not None:
            previous, _ = _apply("default_scope", value, as_json)
        rt = runtime.load_runtime(as_json=as_json)
        effective, line = _effective_payload(rt)
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "default_scope": rt.rules.default_scope,
                    "previous": previous,
                    "changed": value is not None and previous != value,
                    "effective": effective,
                }
            )
            return
        console = make_console()
        if value is None:
            console.print(f"Default scope: {rt.rules.default_scope}")
        elif previous == value:
            console.print(f"Default scope is already {value}.")
        else:
            console.print(f"Default scope: {previous} -> {value}")
        console.print(line)
        if value is not None and effective["scope"] != value:
            console.print(
                "Note: a lock or project rule is narrowing this; the tighter setting always wins."
            )

    @app.command("private")
    def private_command(
        state: Annotated[
            Switch | None,
            typer.Argument(help="on = local models only (the Private lock). Omit to show it."),
        ] = None,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Turn the Private lock on or off. While it is on, nothing leaves this machine."""
        previous = None
        if state is not None:
            previous, _ = _apply("private_lock", state == "on", as_json)
        rt = runtime.load_runtime(as_json=as_json)
        effective, line = _effective_payload(rt)
        lock = rt.rules.private_lock
        if as_json:
            emit_json(
                {
                    "ok": True,
                    "private_lock": lock,
                    "previous": previous,
                    "changed": state is not None and previous != lock,
                    "effective": effective,
                }
            )
            return
        console = make_console()
        word = "on" if lock else "off"
        if state is None:
            console.print(f"Private lock: {word}")
        elif previous == lock:
            console.print(f"Private lock is already {word}.")
        else:
            console.print(f"Private lock: {'on' if previous else 'off'} -> {word}")
        if lock:
            console.print("Only local models will be used; no cloud client is created.")
        console.print(line)
