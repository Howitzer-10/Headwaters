"""HDW-508: the one place a usage report may leave this machine.

Three gates stand in front of the socket, and each one is checked here rather than at the call
sites, so no future command can reach the network by forgetting one of them:

1. an endpoint must be configured (``report.endpoint``), and it must be ``https://``;
2. the consent record must exist **and** cover the payload shape version that is being sent — a
   payload whose shape changed after the user looked at it is not consented to;
3. the payload must pass :func:`headwaters.features.usage_report.payload.check_payload`.

``httpx`` is imported inside the send, so importing this module — which happens whenever the
feature is discovered, whether it is on or off — touches no network machinery at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from headwaters.features.usage_report.payload import PAYLOAD_VERSION, check_payload

#: Reasons a send returned without opening a socket. Kept as names so the CLI can translate them.
NOT_CONFIGURED = "not_configured"
NOT_CONSENTED = "not_consented"
SHAPE_REJECTED = "shape_rejected"
UNAVAILABLE = "unavailable"
OFF = "off"
NOT_DUE = "not_due"
DELIVERED = "delivered"
REJECTED = "rejected"


def result(reason: str, *, detail: str = "", status_code: int | None = None) -> dict[str, Any]:
    return {
        "sent": reason == DELIVERED,
        "reason": reason,
        "detail": detail,
        "status_code": status_code,
    }


def refusal(
    endpoint: str | None, *, payload_version: int, consented_version: int | None
) -> str | None:
    """Why a send would be refused, or ``None`` when it may proceed. The only gate definition."""
    if not endpoint:
        return NOT_CONFIGURED
    if not endpoint.startswith("https://"):
        return NOT_CONFIGURED
    if consented_version is None:
        return NOT_CONSENTED
    if consented_version != payload_version:
        return NOT_CONSENTED
    return None


def send(
    payload: Mapping[str, Any],
    *,
    endpoint: str | None,
    consented_version: int | None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """POST ``payload`` to ``endpoint`` when every gate passes; otherwise say why not.

    ``consented_version`` is the payload version the user actually previewed and accepted
    (``state.consent_version``), never the version of the payload being sent unless they match.
    """
    refusal_reason = refusal(
        endpoint, payload_version=PAYLOAD_VERSION, consented_version=consented_version
    )
    if refusal_reason is not None:
        return result(refusal_reason)
    try:
        check_payload(payload)
    except ValueError as exc:
        return result(SHAPE_REJECTED, detail=str(exc))
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a declared dependency
        return result(UNAVAILABLE, detail="httpx is not installed")
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            response = client.post(
                str(endpoint),
                json=dict(payload),
                headers={"content-type": "application/json", "user-agent": _agent()},
            )
    except Exception as exc:  # a network failure is a result, not a crash
        return result(UNAVAILABLE, detail=type(exc).__name__)
    if 200 <= response.status_code < 300:
        return result(DELIVERED, status_code=response.status_code)
    return result(
        REJECTED, detail="the endpoint refused the report", status_code=response.status_code
    )


def _agent() -> str:
    from headwaters import __version__

    return f"headwaters/{__version__}"


__all__ = [
    "DELIVERED",
    "NOT_CONFIGURED",
    "NOT_CONSENTED",
    "NOT_DUE",
    "OFF",
    "REJECTED",
    "SHAPE_REJECTED",
    "UNAVAILABLE",
    "refusal",
    "result",
    "send",
]
