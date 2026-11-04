"""Keep the local model warm (HDW-201, v3 section 7; heartbeat HDW-518).

One ``tick`` pings Ollama's native ``/api/chat`` with an empty ``messages`` list, which loads (or
re-arms) a model without generating anything and sends no prompt text. Rules:

- ``tiers.local.keep_warm_model``: ``auto`` pings the models Ollama already has loaded (it never
  loads a model on its own); ``off`` disables; any other value names the model to keep warm.
- An explicit model is only pinged when it is already loaded or nothing else is, so a background
  ping can never evict the model a request is using (12 GB: one resident model). That also means an
  explicit model *does* load on a cold device, which is the one way the tick can warm one; ``auto``
  cannot, because nothing records the last model used.
- Paused while another process keeps the GPU busy, on battery, and — with
  ``heartbeat_idle_backoff_s`` — while nothing has been routed for that long.
- Loopback hosts only, and never an Ollama cloud-proxied model (D13).
- ``tiers.local.keep_warm_heartbeat`` (opt-in, default off) adds a literal
  ``heartbeat_interval_s`` instead of the derived ``keep_alive``/2, the idle backoff, and an
  ``expires_at`` read from ``/api/ps`` after a successful ping. Off is the pre-HDW-518 behaviour.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from headwaters.core.catalog.http import HttpClient, SourceError, is_loopback_url
from headwaters.core.duration import parse_duration_s
from headwaters.core.hardware.state import HardwareState
from headwaters.core.ollama_remote import RemotePatterns, is_remote_name
from headwaters.core.rules import Rules

__all__ = ["KeepWarm", "KeepWarmResult", "describe", "parse_duration_s"]

DEFAULT_INTERVAL_S = 900.0
MIN_INTERVAL_S = 30.0
_DISABLED = frozenset({"", "off", "none", "false"})

Action = Literal["pinged", "skipped", "error"]


@dataclass(frozen=True)
class KeepWarmResult:
    action: Action
    reason: str
    models: tuple[str, ...] = ()
    # Only read (one extra `/api/ps`) when `keep_warm_heartbeat` is on; best effort.
    expires_at: str | None = None


def describe(result: KeepWarmResult, interval_s: float) -> str:
    """One line for the daemon log, built from the reason vocabulary above (D18: one source)."""
    who = ", ".join(result.models) or "model"
    if result.action == "pinged":
        until = f" until={result.expires_at}" if result.expires_at else ""
        return f"keep-warm: resident model={who} interval={interval_s:g}s{until}"
    if result.action == "error":
        return f"keep-warm: ping failed for {who} ({result.reason})"
    if result.reason == "idle_backoff":
        return "keep-warm: idle backoff"
    return f"keep-warm: paused ({result.reason})"


class KeepWarm:
    def __init__(
        self,
        rules: Rules,
        http: HttpClient,
        *,
        remote_patterns: RemotePatterns = (),
        hardware: Callable[[], HardwareState | None] = lambda: None,
        last_request: Callable[[], float | None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._rules = rules
        self._http = http
        self._patterns = remote_patterns
        self._hardware = hardware
        # Epoch seconds of the last routed request, or None when nothing was ever routed. The
        # daemon supplies it (HDW-518); without it there is no idle backoff.
        self._last_request = last_request
        self._clock = clock

    def interval_s(self) -> float:
        """Half the ``keep_alive`` window (so one missed tick still leaves time), at least 30 s.

        With ``keep_warm_heartbeat`` on, ``heartbeat_interval_s`` wins when it is > 0.
        """
        local = self._rules.tiers.local
        if local.keep_warm_heartbeat and local.heartbeat_interval_s > 0:
            return local.heartbeat_interval_s
        seconds = parse_duration_s(local.keep_alive)
        if seconds is None or seconds <= 0:
            return DEFAULT_INTERVAL_S
        return max(MIN_INTERVAL_S, seconds / 2)

    def _idle(self) -> bool:
        """True when nothing has been routed for ``heartbeat_idle_backoff_s``.

        A device that has never routed a request counts as idle: that is the cold state the
        heartbeat exists to avoid warming.
        """
        local = self._rules.tiers.local
        if not local.keep_warm_heartbeat or local.heartbeat_idle_backoff_s <= 0:
            return False
        if self._last_request is None:
            return False
        last = self._last_request()
        return last is None or self._clock() - last >= local.heartbeat_idle_backoff_s

    def _loaded(self, host: str) -> list[str]:
        reply = self._http.request_json("GET", f"{host}/api/ps")
        models = reply.get("models") if isinstance(reply, dict) else None
        names: list[str] = []
        for item in models if isinstance(models, list) else []:
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str) and name and not is_remote_name(name, self._patterns):
                names.append(name)
        return names

    def _expires_at(self, host: str, model: str) -> str | None:
        """Best-effort ``expires_at`` for ``model`` from ``/api/ps`` (heartbeat on only)."""
        try:
            reply = self._http.request_json("GET", f"{host}/api/ps")
        except SourceError:
            return None
        models = reply.get("models") if isinstance(reply, dict) else None
        for item in models if isinstance(models, list) else []:
            if isinstance(item, dict) and item.get("name") == model:
                expires = item.get("expires_at")
                return expires if isinstance(expires, str) else None
        return None

    def _blocked(self) -> str | None:
        state = self._hardware()
        if state is None:
            return None
        if state.gpu_busy.busy:
            return "gpu_busy"
        if state.battery.on_battery:
            return "on_battery"
        return None

    def tick(self) -> KeepWarmResult:
        local = self._rules.tiers.local
        setting = local.keep_warm_model.strip()
        if setting.lower() in _DISABLED:
            return KeepWarmResult("skipped", "disabled")
        keep_alive = parse_duration_s(local.keep_alive)
        if keep_alive is None or keep_alive <= 0:
            # Negative = stays loaded forever; zero = unload at once. Neither needs a ping.
            return KeepWarmResult("skipped", "keep_alive_not_timed")
        host = local.host.rstrip("/")
        if not is_loopback_url(host):
            return KeepWarmResult("skipped", "host_not_loopback")
        blocked = self._blocked()
        if blocked is not None:
            return KeepWarmResult("skipped", blocked)
        if self._idle():
            return KeepWarmResult("skipped", "idle_backoff")
        try:
            loaded = self._loaded(host)
        except SourceError:
            return KeepWarmResult("skipped", "ollama_unreachable")
        if setting.lower() == "auto":
            targets = loaded
            if not targets:
                return KeepWarmResult("skipped", "nothing_loaded")
        else:
            if is_remote_name(setting, self._patterns):
                return KeepWarmResult("skipped", "remote_model")
            if loaded and setting not in loaded:
                return KeepWarmResult("skipped", "other_model_loaded")
            targets = [setting]
        done: list[str] = []
        for model in targets:
            body = {"model": model, "messages": [], "stream": False, "keep_alive": local.keep_alive}
            try:
                self._http.request_json("POST", f"{host}/api/chat", body=body)
            except SourceError:
                return KeepWarmResult("error", "ping_failed", tuple(done))
            done.append(model)
        expires_at = self._expires_at(host, done[0]) if local.keep_warm_heartbeat and done else None
        return KeepWarmResult("pinged", "ok", tuple(done), expires_at)
