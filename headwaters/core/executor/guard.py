"""Local-scope guard (HDW-115, v3.3 section D).

Enforcement lives here, in the executor, not only in the policy layer: a local-scoped request
never builds a cloud client, and a local target must be a loopback Ollama host.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from headwaters.core.executor.errors import LocalScopeViolation, ScopeViolation
from headwaters.core.policy.types import RouteDecision
from headwaters.core.rules import Rules


def assert_loopback_url(url: str) -> str:
    """Return ``url`` without a trailing slash, or raise ``LocalScopeViolation``.

    Accepts a plain ``http`` URL whose host is ``localhost`` or a loopback IP (127.0.0.0/8,
    ``::1``). Names are not resolved: anything else, including other host names, is refused.
    """
    try:
        parsed = urlparse(url)
        name = parsed.hostname or ""
        parsed.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError as exc:
        raise LocalScopeViolation("Local host URL is not valid.") from exc
    if parsed.scheme != "http" or not name:
        raise LocalScopeViolation("Local host must be a plain http loopback URL.")
    if name.lower() != "localhost":
        try:
            loopback = ipaddress.ip_address(name).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise LocalScopeViolation("Local host is not a loopback address; refusing to send.")
    return url.rstrip("/")


def enforce_scope(decision: RouteDecision, rules: Rules) -> None:
    """Raise unless ``decision`` may run. Called before any backend is constructed."""
    tier = decision.tier
    model = decision.model
    if decision.scope.local_only and tier != "local":
        raise LocalScopeViolation("Local scope never uses a cloud tier.", model=model)
    if tier not in decision.scope.tiers:
        raise ScopeViolation(f"Tier {tier!r} is outside the effective scope.", model=model)
    if rules.airplane and tier != "local":
        raise ScopeViolation("Airplane mode blocks all network egress.", model=model)
    if tier == "local":
        try:
            assert_loopback_url(rules.tiers.local.host)
        except LocalScopeViolation as exc:
            raise LocalScopeViolation(exc.message, model=model) from exc
