"""Minimal Ollama native-API client for the classifier layer (HDW-107).

Only ``POST /api/chat`` on a loopback host. Stdlib ``urllib`` with proxies disabled, so a
local-scope prompt can never leave the machine through this path. HDW-115's executor may
replace it; layer 3 only depends on the ``OllamaChatClient`` protocol.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlparse

_MAX_RESPONSE_BYTES = 1_000_000


class ClassifierUnavailableError(RuntimeError):
    """The classifier model could not answer (down, timed out, bad reply)."""


class OllamaChatClient(Protocol):
    def chat(self, payload: Mapping[str, Any], timeout_s: float) -> Mapping[str, Any]: ...


def assert_loopback(host_url: str) -> str:
    """Return ``host_url`` without a trailing slash, or raise if it is not loopback http."""
    parsed = urlparse(host_url)
    name = parsed.hostname or ""
    if parsed.scheme != "http" or not name:
        raise ValueError(f"classifier host must be a plain http loopback URL, got {host_url!r}")
    if name != "localhost":
        try:
            if not ipaddress.ip_address(name).is_loopback:
                raise ValueError(f"classifier host {name!r} is not a loopback address")
        except ValueError as exc:
            raise ValueError(f"classifier host {name!r} is not a loopback address") from exc
    return host_url.rstrip("/")


class OllamaHttpClient:
    def __init__(self, host: str = "http://localhost:11434") -> None:
        self.host = assert_loopback(host)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def chat(self, payload: Mapping[str, Any], timeout_s: float) -> Mapping[str, Any]:
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=timeout_s) as response:
                raw = response.read(_MAX_RESPONSE_BYTES)
            data = json.loads(raw)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise ClassifierUnavailableError(type(exc).__name__) from exc
        if not isinstance(data, dict):
            raise ClassifierUnavailableError("reply is not an object")
        return data
