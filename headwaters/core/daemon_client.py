"""Talk to the running daemon's internal API (HDW-201). Used by the CLI, shims and tests.

Loopback only, no proxies (``UrllibHttp`` bypasses them for loopback hosts). ``/health`` needs no
token; everything else sends ``Authorization: Bearer <token>``. No prompt data passes through here.
"""

from __future__ import annotations

from typing import Any

from headwaters.core.catalog.http import HttpClient, SourceError, UrllibHttp, is_loopback_url
from headwaters.core.daemon_info import DaemonInfo, read_info, read_token

__all__ = ["DaemonClient", "DaemonError", "find_running"]


class DaemonError(Exception):
    """The daemon did not answer or refused the request. The message is safe to show."""


class DaemonClient:
    def __init__(
        self,
        info: DaemonInfo,
        token: str | None = None,
        *,
        http: HttpClient | None = None,
        timeout_s: float = 3.0,
    ) -> None:
        if not is_loopback_url(info.api_url):
            raise DaemonError("The daemon state file does not point at a loopback address.")
        self.info = info
        self._token = token
        self._http: HttpClient = http or UrllibHttp(timeout=timeout_s)

    def _call(
        self, method: str, path: str, *, auth: bool, body: dict[str, Any] | None = None
    ) -> Any:
        headers: dict[str, str] | None = None
        if auth:
            if not self._token:
                raise DaemonError("No daemon token is available (is the daemon set up for you?).")
            headers = {"Authorization": f"Bearer {self._token}"}
        try:
            reply = self._http.request_json(
                method, self.info.api_url + path, headers=headers, body=body
            )
        except SourceError as exc:
            raise DaemonError(str(exc)) from exc
        if not isinstance(reply, dict):
            raise DaemonError("The daemon sent an unexpected reply.")
        return reply

    def health(self) -> dict[str, Any]:
        reply: dict[str, Any] = self._call("GET", "/health", auth=False)
        return reply

    def status(self) -> dict[str, Any]:
        reply: dict[str, Any] = self._call("GET", "/v1/status", auth=True)
        return reply

    def reload(self) -> dict[str, Any]:
        """Ask the daemon to re-read ``rules.yaml`` now; the reply carries the live switches."""
        reply: dict[str, Any] = self._call("POST", "/v1/reload", auth=True, body={})
        return reply

    def shutdown(self) -> dict[str, Any]:
        reply: dict[str, Any] = self._call("POST", "/v1/shutdown", auth=True, body={})
        return reply


def find_running(*, http: HttpClient | None = None, timeout_s: float = 2.0) -> DaemonClient | None:
    """A client for the live daemon, or ``None``.

    "Live" means the state file exists, ``/health`` answers and it names the same pid, so a stale
    file (crash, reboot) or a port reused by another program is never mistaken for the daemon.
    """
    info = read_info()
    if info is None:
        return None
    try:
        client = DaemonClient(info, read_token(), http=http, timeout_s=timeout_s)
        health = client.health()
    except DaemonError:
        return None
    if health.get("ok") is not True or health.get("pid") != info.pid:
        return None
    return client
