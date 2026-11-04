"""``headwaters keys set|list|remove|test``: the API keys Headwaters can spend (HDW-401).

A key is stored in the OS credential store (Windows Credential Manager) under an alias, so it is
never written to ``rules.yaml``, a log, a database row or a diagnostic bundle. ``list`` and
``set`` only ever print the masked value; the secret itself is read from a hidden prompt or from
stdin (``--stdin``), never from the command line, so it stays out of the shell history.

Provider names are one canonical set (``KeyStore.providers()``, shared with the dashboard): a
person types ``nvidia``, never an environment-variable name, and no text here prints one. The
``--json`` payload keeps ``env_var`` as a machine contract. A short form (``hf``) or an
environment-variable spelling is normalised to the provider, and a name that is neither a
provider nor an existing alias is refused instead of storing a key the router can never spend.

``test`` asks the provider whether it accepts the key: one metadata request, no prompt content,
and no request at all while the network is blocked (airplane mode, Private lock, local scope).
The result is written to the shared state (``state_key_tests``) so the dashboard shows the same
last-test line (HDW-503) instead of a second opinion.
"""

import sqlite3
import sys
from typing import Annotated, Any

import typer

from headwaters.cli import runtime
from headwaters.cli.output import EXIT_CONFIG, EXIT_FAILED, emit_json, fail, make_console, table
from headwaters.control.keys import check_and_record
from headwaters.core.catalog import updater
from headwaters.core.keys import KeyInfo, KeyStore, KeyStoreError
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database


def key_store(rules: Rules) -> KeyStore:
    """The store the commands use. Tests replace this with one over a fake backend."""
    return KeyStore(rules)


def _state_db() -> Database | None:
    """The shared state, or ``None`` when it cannot be opened: a key test must still report."""
    try:
        return open_database()
    except (OSError, sqlite3.Error):  # the result is a convenience, the test is the answer
        return None


def _row(row: KeyInfo) -> dict[str, Any]:
    return {
        "alias": row.alias,
        "provider": row.provider,
        "env_var": row.env_var,
        "source": row.source,
        "key": row.masked,
    }


def _resolve_names(
    store: KeyStore, alias: str, provider: str | None, as_json: bool
) -> tuple[str, str | None]:
    """``(alias, provider)`` to save under, from what a person typed.

    A provider name, a short form (``hf``) or an environment-variable spelling all become the one
    canonical provider, so no second alias is stored. A name that is neither a provider nor an
    existing alias, with no ``--provider``, would store a key the router can never spend: refuse.
    """
    typed = alias.strip()
    if provider is not None:
        return typed, store.provider_name(provider) or provider.strip()
    if typed in store.aliases():
        return typed, None
    canonical = store.provider_name(typed)
    if canonical is not None:
        return canonical, None
    known = ", ".join(info.name for info in store.providers())
    fail(
        "unknown_provider",
        f"{typed} is not a provider Headwaters can call. Known providers: {known}.",
        fix="for your own alias add --provider, e.g. headwaters keys set work --provider nvidia",
        exit_code=EXIT_CONFIG,
        as_json=as_json,
    )


def _read_secret(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    return str(typer.prompt("Key", hide_input=True, confirmation_prompt=False))


def register(keys_app: typer.Typer) -> None:
    @keys_app.command("set")
    def keys_set(
        alias: Annotated[
            str,
            typer.Argument(
                help="Provider to save a key for (nvidia, openrouter, ...) or your own alias."
            ),
        ],
        provider: Annotated[
            str | None,
            typer.Option(
                "--provider",
                "-p",
                help="Provider a custom alias is for, to keep several keys for one provider.",
            ),
        ] = None,
        env_var: Annotated[
            str | None,
            typer.Option("--env", help="Environment variable this key falls back to (advanced)."),
        ] = None,
        stdin: Annotated[
            bool, typer.Option("--stdin", help="Read the key from stdin (for scripts).")
        ] = False,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Save a key in the OS credential store (it wins over the environment).

        Example: headwaters keys set nvidia
        """
        rules = runtime.load_rules_or_exit()
        store = key_store(rules)
        name, chosen = _resolve_names(store, alias, provider, as_json)
        secret = _read_secret(stdin)
        try:
            saved = store.save(name, secret, provider=chosen, env_var=env_var)
        except KeyStoreError as exc:
            fail("key_not_saved", str(exc), exit_code=EXIT_CONFIG, as_json=as_json)
        if as_json:
            emit_json({"ok": True, **_row(saved)})
            return
        if saved.alias == saved.provider:
            typer.echo(f"Saved {saved.provider} ({saved.masked}).")
        else:
            typer.echo(f"Saved {saved.alias} for {saved.provider} ({saved.masked}).")
        if saved.env_var:
            typer.echo("If no key is saved, the provider's environment variable is used instead.")

    @keys_app.command("list")
    def keys_list(
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show each known alias with where its key comes from and the masked value."""
        rules = runtime.load_rules_or_exit()
        rows = key_store(rules).entries()
        if as_json:
            emit_json({"ok": True, "keys": [_row(row) for row in rows]})
            return
        if not rows:
            typer.echo("No keys. Save one with: headwaters keys set nvidia")
            return
        console = make_console()
        grid = table(None, "Alias", "Provider", "Source", "Key", no_wrap=["Alias", "Provider"])
        for row in rows:
            grid.add_row(row.alias, row.provider, row.source, row.masked or "-")
        console.print(grid)

    @keys_app.command("remove")
    def keys_remove(
        alias: Annotated[str, typer.Argument(help="Alias to delete.")],
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Delete a stored key. The environment variable, if set, is used again."""
        rules = runtime.load_rules_or_exit()
        store = key_store(rules)
        if alias not in store.aliases():
            alias = store.provider_name(alias) or alias
        removed = store.remove(alias)
        if not removed:
            fail(
                "key_not_found",
                f"no stored key named {alias}",
                fix="check the name with: headwaters keys list",
                as_json=as_json,
            )
        if as_json:
            emit_json({"ok": True, "removed": alias})
            return
        typer.echo(f"Removed {alias}.")

    @keys_app.command("test")
    def keys_test(
        alias: Annotated[
            str | None, typer.Argument(help="Test one alias (default: every key with a value).")
        ] = None,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Ask each provider whether it accepts the stored key. Sends no prompt content."""
        rules = runtime.load_rules_or_exit()
        store = key_store(rules)
        if alias is not None and alias not in store.aliases():
            alias = store.provider_name(alias) or alias
        if alias is not None and alias not in store.aliases():
            fail(
                "key_not_found",
                f"no key named {alias}",
                fix="check the name with: headwaters keys list",
                as_json=as_json,
            )
        catalog, _source = updater.load_active()
        results = check_and_record(
            store,
            rules,
            _state_db(),
            openrouter_base=catalog.endpoints.openrouter_base_url,
            aliases=None if alias is None else [alias],
        )
        if as_json:
            emit_json(
                {
                    "ok": all(result.ok for result in results) if results else True,
                    "results": [
                        {
                            "alias": result.alias,
                            "provider": result.provider,
                            "status": result.status,
                            "detail": result.detail,
                        }
                        for result in results
                    ],
                }
            )
        else:
            if not results:
                typer.echo("No keys to test. Save one with: headwaters keys set nvidia")
                return
            console = make_console()
            grid = table(None, "Alias", "Provider", "Status", "Detail", no_wrap=["Alias", "Status"])
            for result in results:
                grid.add_row(result.alias, result.provider, result.status, result.detail)
            console.print(grid)
        if any(not result.ok for result in results):
            raise typer.Exit(EXIT_FAILED)
