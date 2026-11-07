"""Local backend: Ollama NATIVE ``/api/chat``, ``/api/pull`` and ``/api/tags`` over loopback.

Streams NDJSON. ``/api/chat`` sends the computed ``num_ctx``, ``keep_alive`` and an optional
``format`` JSON schema; ``message.thinking`` is kept apart from ``message.content``. The pull and
tag calls are the setup wizard's (HDW-505): the same client, the same loopback assertion, so
there is never a second HTTP path to Ollama. Proxy environment variables are ignored
(``trust_env=False``), so a local-scope prompt cannot be routed off the machine.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import httpx

from headwaters.core.executor.errors import LocalScopeViolation, ProviderError
from headwaters.core.executor.guard import REMOTE_MODEL_MESSAGE, assert_loopback_url
from headwaters.core.executor.ollama_convert import build_payload, parse_line
from headwaters.core.executor.types import BackendRequest, Delta
from headwaters.core.ollama_remote import RemotePatterns, is_remote_name
from headwaters.core.rules import ExecutorRules

_MAX_ERROR_BYTES = 4096


def _error_body(raw: bytes) -> str:
    return raw[:_MAX_ERROR_BYTES].decode("utf-8", errors="replace")


class OllamaBackend:
    """Streams ``POST {host}/api/chat``. Construction asserts the host is loopback."""

    def __init__(
        self,
        host: str,
        *,
        connect_s: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        remote_patterns: RemotePatterns = (),
    ) -> None:
        self.host = assert_loopback_url(host)
        self._connect_s = ExecutorRules().connect_s if connect_s is None else connect_s
        self._transport = transport
        self._remote_patterns = remote_patterns

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        # Defence in depth (A-OLL-9): a cloud-proxied model is never called as a local one.
        if is_remote_name(request.target.route, self._remote_patterns):
            raise LocalScopeViolation(REMOTE_MODEL_MESSAGE, model=request.target.model_id)
        payload = build_payload(request)
        timeout = httpx.Timeout(None, connect=self._connect_s)
        seen_calls = 0
        try:
            async with (
                httpx.AsyncClient(
                    timeout=timeout, trust_env=False, transport=self._transport
                ) as client,
                client.stream("POST", f"{self.host}/api/chat", json=payload) as response,
            ):
                if response.status_code >= 400:
                    body = _error_body(await response.aread())
                    raise ProviderError(response.status_code, response.headers, body)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError as exc:
                        raise ProviderError(
                            502, None, "invalid stream line", kind="stream_error"
                        ) from exc
                    if not isinstance(data, dict):
                        continue
                    if data.get("error"):
                        text = str(data["error"])[:_MAX_ERROR_BYTES]
                        raise ProviderError(500, None, text, kind="stream_error")
                    delta, seen_calls = parse_line(data, seen_calls)
                    yield delta
        except httpx.TimeoutException as exc:
            raise ProviderError(408, None, "", kind="timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(0, None, "", kind="connection") from exc


#: A cloud-proxied tag by shape alone (``gpt-oss:120b-cloud``, ``foo:cloud``). The catalog's
#: ``ollama_remote_patterns`` is the authority (D13); this is the fallback for the case where the
#: catalog is unavailable, so a pull can never be talked into a cloud tag by a missing catalog.
_CLOUD_TAG = re.compile(r"(?i)[:-]cloud$")


@dataclass(frozen=True)
class PullLine:
    """One NDJSON line of ``POST /api/pull``: a layer's bytes so far, or a status sentence.

    Ollama reports ``total`` and ``completed`` per layer and repeats a layer's ``digest``, so a
    caller can tell "no layer has a size yet" (``total == 0``) from "0 of known bytes" without
    inventing a percentage.
    """

    status: str
    digest: str = ""
    total: int = 0
    completed: int = 0


def cloud_tag(model: str, patterns: RemotePatterns = ()) -> str | None:
    """Why ``model`` may never be pulled, or ``None`` when it is an ordinary local tag.

    D13: a catalog profile is data and could name a ``:cloud`` model, so the pull path refuses one
    itself rather than trusting the recommendation step that produced the name.
    """
    if is_remote_name(model, patterns) or _CLOUD_TAG.search(model):
        return (
            f"{model} is a cloud-proxied model, not a local one; "
            "Headwaters never pulls or routes to it"
        )
    return None


def _int_field(data: dict[str, object], name: str) -> int:
    value = data.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def list_installed(
    host: str,
    *,
    connect_s: float | None = None,
    read_s: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, ...]:
    """The model tags ``GET /api/tags`` reports, sorted. Raises ``ProviderError`` when it fails."""
    base = assert_loopback_url(host)
    timeout = httpx.Timeout(read_s, connect=connect_s if connect_s is not None else read_s)
    try:
        with httpx.Client(timeout=timeout, trust_env=False, transport=transport) as client:
            response = client.get(f"{base}/api/tags")
            if response.status_code >= 400:
                raise ProviderError(
                    response.status_code, response.headers, _error_body(response.content)
                )
            data = response.json()
    except httpx.TimeoutException as exc:
        raise ProviderError(408, None, "", kind="timeout") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(0, None, "", kind="connection") from exc
    except ValueError as exc:
        raise ProviderError(502, None, "invalid JSON", kind="stream_error") from exc
    items = data.get("models") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ProviderError(502, None, "no models list", kind="stream_error")
    names = {
        str(item.get("name") or item.get("model") or "") for item in items if isinstance(item, dict)
    }
    return tuple(sorted(name for name in names if name))


def pull_stream(
    host: str,
    model: str,
    *,
    connect_s: float | None = None,
    transport: httpx.BaseTransport | None = None,
    remote_patterns: RemotePatterns = (),
) -> Iterator[PullLine]:
    """``POST /api/pull`` as a blocking stream of ``PullLine``, Ollama's native API.

    Refuses a cloud-proxied tag before any request is made (D13), asserts loopback, and maps a
    refused connection or an ``{"error": ...}`` line to ``ProviderError`` — so a pull with no
    Ollama listening is an error the person is told about, never a silent no-op.
    """
    refusal = cloud_tag(model, remote_patterns)
    if refusal is not None:
        raise LocalScopeViolation(REMOTE_MODEL_MESSAGE, model=model)
    base = assert_loopback_url(host)
    timeout = httpx.Timeout(None, connect=connect_s if connect_s is not None else 10.0)
    with httpx.Client(timeout=timeout, trust_env=False, transport=transport) as client:
        try:
            with client.stream(
                "POST", f"{base}/api/pull", json={"model": model, "stream": True}
            ) as (response):
                if response.status_code >= 400:
                    body = _error_body(response.read())
                    raise ProviderError(response.status_code, response.headers, body)
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError as exc:
                        raise ProviderError(
                            502, None, "invalid stream line", kind="stream_error"
                        ) from exc
                    if not isinstance(data, dict):
                        continue
                    if data.get("error"):
                        raise ProviderError(
                            500, None, str(data["error"])[:_MAX_ERROR_BYTES], kind="stream_error"
                        )
                    yield PullLine(
                        status=str(data.get("status") or "")[:200],
                        digest=str(data.get("digest") or "")[:64],
                        total=_int_field(data, "total"),
                        completed=_int_field(data, "completed"),
                    )
        except httpx.TimeoutException as exc:
            raise ProviderError(408, None, "", kind="timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(0, None, "", kind="connection") from exc
