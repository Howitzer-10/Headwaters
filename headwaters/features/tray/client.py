"""The tray's client for the daemon's internal API (HDW-504).

Three calls and no more: read ``/v1/status`` and ``/v1/keys``, and write ``/v1/scope``. The
token is read from ``daemon.token`` per request - a restarted daemon has a new port and the same
token, and a missing state file simply means the daemon is not there. A request that fails or
times out is "daemon down" for that tick, never an exception the menu has to handle.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from headwaters.core.daemon_info import DaemonInfo, read_info, read_token
from headwaters.features.tray.model import TrayState

__all__ = ["DEFAULT_TIMEOUT_S", "KEYS_PATH", "SCOPE_PATH", "STATUS_PATH", "TrayClient"]

STATUS_PATH = "/v1/status"
KEYS_PATH = "/v1/keys"
SCOPE_PATH = "/v1/scope"
DEFAULT_TIMEOUT_S = 1.5


class TrayClient:
    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        info: Callable[[], DaemonInfo | None] = read_info,
        token: Callable[[], str | None] = read_token,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._http = (
            http if http is not None else httpx.Client(timeout=timeout, follow_redirects=False)
        )
        self._info = info
        self._token = token

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        token = self._token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _get(self, url: str) -> Any:
        try:
            reply = self._http.get(url, headers=self._headers())
            reply.raise_for_status()
            return reply.json()
        except (httpx.HTTPError, ValueError):
            return None

    def fetch(self) -> TrayState:
        """One reading of the daemon, or a red icon when it is not there."""
        info = self._info()
        if info is None:
            return TrayState.daemon_down()
        status = self._get(info.api_url + STATUS_PATH)
        if not isinstance(status, dict):
            return TrayState.daemon_down()
        return TrayState.from_payloads(
            status, self._get(info.api_url + KEYS_PATH), dashboard_url=info.dashboard_url
        )

    def set_setting(self, key: str, value: bool | str) -> dict[str, Any] | None:
        """``POST /v1/scope``: the daemon writes the setting and says what is in force now.

        A refusal (409) still carries a JSON body with a reason, so it is read like any reply;
        only a transport failure returns None.
        """
        info = self._info()
        if info is None:
            return None
        try:
            reply = self._http.post(
                info.api_url + SCOPE_PATH,
                json={"key": key, "value": value},
                headers=self._headers(),
            )
            body = reply.json()
        except (httpx.HTTPError, ValueError):
            return None
        return body if isinstance(body, dict) else None
