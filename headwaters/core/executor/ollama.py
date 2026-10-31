"""Local backend: Ollama NATIVE ``/api/chat`` over loopback only (HDW-115).

Streams NDJSON. Sends the computed ``num_ctx``, ``keep_alive`` and an optional ``format`` JSON
schema. ``message.thinking`` is kept apart from ``message.content``. Proxy environment variables
are ignored (``trust_env=False``), so a local-scope prompt cannot be routed off the machine.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.guard import assert_loopback_url
from headwaters.core.executor.ollama_convert import build_payload, parse_line
from headwaters.core.executor.types import BackendRequest, Delta

DEFAULT_CONNECT_S = 5.0
_MAX_ERROR_BYTES = 4096


def _error_body(raw: bytes) -> str:
    return raw[:_MAX_ERROR_BYTES].decode("utf-8", errors="replace")


class OllamaBackend:
    """Streams ``POST {host}/api/chat``. Construction asserts the host is loopback."""

    def __init__(
        self,
        host: str,
        *,
        connect_s: float = DEFAULT_CONNECT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.host = assert_loopback_url(host)
        self._connect_s = connect_s
        self._transport = transport

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
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
