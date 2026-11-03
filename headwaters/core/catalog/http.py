"""Tiny JSON-over-HTTP client behind a Protocol, so discovery can be tested with mocks.

Loopback requests bypass system proxies. Plain ``http`` is only allowed for loopback hosts, so
an API key is never sent in clear text across a network. No prompt data passes through here.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from typing import Any, Protocol
from urllib.parse import urlparse

from headwaters.core.user_agent import USER_AGENT


class SourceError(Exception):
    """A discovery source could not be read; the message is safe to show to the user."""


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    name = host.strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def is_loopback_url(url: str) -> bool:
    return is_loopback_host(urlparse(url).hostname)


class HttpClient(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any: ...


class UrllibHttp:
    def __init__(self, timeout: float = 10.0, max_bytes: int = 20_000_000) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        parsed = urlparse(url)
        loopback = is_loopback_host(parsed.hostname)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise SourceError(f"unsupported URL {url!r}")
        if parsed.scheme == "http" and not loopback:
            raise SourceError("plain http is only allowed for loopback hosts; use https")
        merged = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            merged["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=merged, method=method)
        opener = urllib.request.build_opener(
            *([urllib.request.ProxyHandler({})] if loopback else [])
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw: bytes = response.read(self.max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise SourceError(f"HTTP {exc.code} from {parsed.netloc}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise SourceError(f"cannot reach {parsed.netloc}: {exc}") from exc
        if len(raw) > self.max_bytes:
            raise SourceError(f"response from {parsed.netloc} is too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise SourceError(f"{parsed.netloc} did not return valid JSON") from exc
