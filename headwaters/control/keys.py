"""Key validation pings (HDW-401): one cheap metadata request per provider, no prompt content.

``headwaters keys test`` asks a provider whether it accepts a stored key. The request carries the
key and nothing else: ``GET {openrouter}/key`` (its balance endpoint), ``GET /v1/models`` on
Anthropic and OpenAI, ``GET {base_url}/models`` on a user-configured endpoint. A provider with no
known metadata endpoint is reported ``unsupported`` instead of being called blindly. A provider
registered in ``catalog.providers`` (HDW-514) names its own ``metadata_url``, or none.

Results carry a status word and a detail that are safe to show and to log: the key appears in
neither, and a failed request keeps only the host and the HTTP code from ``SourceError``.

Blocking is the same rule as discovery and the key-poll timer (``network_block``): airplane mode,
the Private lock and a local default scope send nothing at all.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from headwaters.core.catalog.discovery import network_block
from headwaters.core.catalog.http import HttpClient, SourceError, UrllibHttp
from headwaters.core.catalog.schema import Endpoints
from headwaters.core.keys import KeyStore, KeyStoreError
from headwaters.core.logging import get_logger
from headwaters.core.providers import Registry, default_registry
from headwaters.core.rules import Rules
from headwaters.core.state import Database
from headwaters.core.state_key_tests import clear_key_test, record_key_tests

__all__ = [
    "DashboardKeys",
    "PingResult",
    "check_and_record",
    "check_keys",
    "ping",
    "target_for",
]

_log = get_logger("control.keys")

ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"

# status values
OK = "ok"
UNAUTHORIZED = "unauthorized"
HTTP_ERROR = "http_error"
UNREACHABLE = "unreachable"
BLOCKED = "blocked"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class PingResult:
    """What one provider answered about one key."""

    alias: str
    provider: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == OK


def target_for(
    provider: str,
    rules: Rules,
    openrouter_base: str | None = None,
    registry: Registry | None = None,
) -> tuple[str, str] | None:
    """``(url, header style)`` for a provider, or ``None`` when no safe ping endpoint is known."""
    if provider == "openrouter":
        base = (openrouter_base or Endpoints().openrouter_base_url).rstrip("/")
        return f"{base}/key", "bearer"
    if provider == "anthropic":
        return ANTHROPIC_MODELS_URL, "anthropic"
    if provider == "openai":
        return OPENAI_MODELS_URL, "bearer"
    for endpoint in rules.models.openai_compatible:
        if endpoint.name == provider:
            return f"{endpoint.base_url.rstrip('/')}/models", "bearer"
    entry = registry.get(provider) if registry is not None else None
    if entry is not None and entry.metadata_url is not None:
        return entry.metadata_url, entry.auth_style
    return None


def _headers(style: str, secret: str) -> dict[str, str]:
    if style == "anthropic":
        return {"x-api-key": secret, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {secret}"}


def _classify(message: str) -> str:
    if message.startswith("HTTP 401") or message.startswith("HTTP 403"):
        return UNAUTHORIZED
    if message.startswith("cannot reach"):
        return UNREACHABLE
    return HTTP_ERROR


def ping(
    alias: str,
    provider: str,
    secret: str,
    *,
    http: HttpClient,
    url: str,
    style: str,
) -> PingResult:
    """Ask ``url`` whether it accepts ``secret``. The secret is not echoed or stored."""
    try:
        http.request_json("GET", url, headers=_headers(style, secret))
    except SourceError as exc:
        return PingResult(alias, provider, _classify(str(exc)), str(exc))
    return PingResult(alias, provider, OK, "accepted")


def check_keys(
    store: KeyStore,
    rules: Rules,
    *,
    http: HttpClient | None = None,
    openrouter_base: str | None = None,
    aliases: Sequence[str] | None = None,
    registry: Registry | None = None,
) -> list[PingResult]:
    """Test every key with a value (or just ``aliases``). Blocked means nothing was sent."""
    client = http if http is not None else UrllibHttp()
    wanted = None if aliases is None else set(aliases)
    chosen = [
        (row.alias, row.provider)
        for row in store.entries()
        if row.source != "not set" and (wanted is None or row.alias in wanted)
    ]
    blocked = network_block(rules)
    if blocked is not None:
        return [PingResult(alias, provider, BLOCKED, blocked) for alias, provider in chosen]
    providers = registry if registry is not None else default_registry()
    results: list[PingResult] = []
    for alias, provider in chosen:
        secret = store.secret(alias)
        if not secret:
            continue
        target = target_for(provider, rules, openrouter_base, providers)
        if target is None:
            results.append(
                PingResult(alias, provider, UNSUPPORTED, "no validation endpoint is known")
            )
            continue
        results.append(ping(alias, provider, secret, http=client, url=target[0], style=target[1]))
    return results


def check_and_record(
    store: KeyStore,
    rules: Rules,
    db: Database | None = None,
    *,
    http: HttpClient | None = None,
    openrouter_base: str | None = None,
    aliases: Sequence[str] | None = None,
    registry: Registry | None = None,
) -> list[PingResult]:
    """``check_keys``, and remember the answers when there is a state database (HDW-503).

    ``headwaters keys test`` and the dashboard both come through here, so a test run on either
    surface is the answer the other one shows. A state write that fails must not fail the test:
    the person asked the provider, not the database.
    """
    results = check_keys(
        store,
        rules,
        http=http,
        openrouter_base=openrouter_base,
        aliases=aliases,
        registry=registry,
    )
    if db is not None:
        try:
            record_key_tests(db, results)
        except sqlite3.Error:
            _log.warning("key test results not recorded", exc_info=True)
    return results


@dataclass
class DashboardKeys:
    """The dashboard's whole write surface (HDW-503): save, remove and test - and nothing else.

    The store and the rules are read per call (production passes ``Daemon.key_store``, which
    builds a store over the *live* rules), so a rules change is seen by the next click. Each call
    returns the provider whose section the page comes back to; a refusal raises ``KeyStoreError``
    with a sentence that is safe to show and never holds a key or an environment-variable name.
    ``http`` and ``openrouter_base`` exist so a test never reaches the network.
    """

    store: Callable[[], KeyStore]
    rules: Callable[[], Rules]
    db: Database
    http: HttpClient | None = None
    openrouter_base: str | None = None

    def save(self, alias: str, provider: str, secret: str) -> str:
        store = self.store()
        canonical = self._provider(store, provider.strip() or alias)
        info = store.save(alias.strip() or canonical, secret, provider=canonical)
        clear_key_test(self.db, info.alias)  # a new key has not been tested yet
        return info.provider

    def remove(self, alias: str) -> str:
        """Remove a stored key. A key that only comes from the environment stays: the page says so.

        ``KeyStore.remove`` returns False when nothing was in the credential store, and that is not
        an error here - the honest answer is the note the page then shows, not a refusal the
        person cannot act on.
        """
        store = self.store()
        name = self._alias(store, alias)
        provider = store.provider_for(name) or name
        store.remove(name)
        clear_key_test(self.db, name)
        return provider

    def test(self, alias: str) -> str:
        store = self.store()
        name = self._alias(store, alias)
        results = check_and_record(
            store,
            self.rules(),
            self.db,
            http=self.http,
            openrouter_base=self.openrouter_base,
            aliases=[name],
        )
        if not results:
            raise KeyStoreError(f"there is no key to test for {name}")
        return results[0].provider

    @staticmethod
    def _provider(store: KeyStore, typed: str) -> str:
        """The canonical provider (the registry's spelling), or a refusal a person can read."""
        name = store.provider_name(typed)
        if name is None:
            raise KeyStoreError(f"{typed.strip()} is not a provider this install can call")
        return name

    @staticmethod
    def _alias(store: KeyStore, typed: str) -> str:
        name = typed.strip()
        if not name:
            raise KeyStoreError("no key was named")
        if name in store.aliases():
            return name
        canonical = store.provider_name(name)
        if canonical is not None and canonical in store.aliases():
            return canonical
        raise KeyStoreError(f"no key named {name}")
