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

from collections.abc import Sequence
from dataclasses import dataclass

from headwaters.core.catalog.discovery import network_block
from headwaters.core.catalog.http import HttpClient, SourceError, UrllibHttp
from headwaters.core.catalog.schema import Endpoints
from headwaters.core.keys import KeyStore
from headwaters.core.providers import Registry, default_registry
from headwaters.core.rules import Rules

__all__ = ["PingResult", "check_keys", "ping", "target_for"]

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
