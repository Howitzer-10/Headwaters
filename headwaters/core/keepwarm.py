"""Keep the local model warm (HDW-201, v3 section 7).

One ``tick`` pings Ollama's native ``/api/chat`` with an empty ``messages`` list, which loads (or
re-arms) a model without generating anything and sends no prompt text. Rules:

- ``tiers.local.keep_warm_model``: ``auto`` pings the models Ollama already has loaded (it never
  loads a model on its own); ``off`` disables; any other value names the model to keep warm.
- An explicit model is only pinged when it is already loaded or nothing else is, so a background
  ping can never evict the model a request is using (12 GB: one resident model).
- Paused while another process keeps the GPU busy, and on battery.
- Loopback hosts only, and never an Ollama cloud-proxied model (D13).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from headwaters.core.catalog.http import HttpClient, SourceError, is_loopback_url
from headwaters.core.hardware.state import HardwareState
from headwaters.core.ollama_remote import RemotePatterns, is_remote_name
from headwaters.core.rules import Rules

__all__ = ["KeepWarm", "KeepWarmResult", "parse_duration_s"]

DEFAULT_INTERVAL_S = 900.0
MIN_INTERVAL_S = 30.0
_DISABLED = frozenset({"", "off", "none", "false"})
_UNIT_S = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}
_PART = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")

Action = Literal["pinged", "skipped", "error"]


def parse_duration_s(text: str) -> float | None:
    """Seconds in an Ollama ``keep_alive`` value (``30m``, ``1h30m``, ``90``).

    ``None`` for a negative value (Ollama: keep loaded forever) and for text that is not a duration.
    """
    raw = text.strip().lower()
    if not raw:
        return None
    if raw.startswith("-"):
        return None
    try:
        return float(raw)  # a bare number is seconds
    except ValueError:
        pass
    total = 0.0
    position = 0
    for match in _PART.finditer(raw):
        if match.start() != position:
            return None
        total += float(match.group(1)) * _UNIT_S[match.group(2)]
        position = match.end()
    return total if position == len(raw) else None


@dataclass(frozen=True)
class KeepWarmResult:
    action: Action
    reason: str
    models: tuple[str, ...] = ()


class KeepWarm:
    def __init__(
        self,
        rules: Rules,
        http: HttpClient,
        *,
        remote_patterns: RemotePatterns = (),
        hardware: Callable[[], HardwareState | None] = lambda: None,
    ) -> None:
        self._rules = rules
        self._http = http
        self._patterns = remote_patterns
        self._hardware = hardware

    def interval_s(self) -> float:
        """Half the ``keep_alive`` window (so one missed tick still leaves time), at least 30 s."""
        seconds = parse_duration_s(self._rules.tiers.local.keep_alive)
        if seconds is None or seconds <= 0:
            return DEFAULT_INTERVAL_S
        return max(MIN_INTERVAL_S, seconds / 2)

    def _loaded(self, host: str) -> list[str]:
        reply = self._http.request_json("GET", f"{host}/api/ps")
        models = reply.get("models") if isinstance(reply, dict) else None
        names: list[str] = []
        for item in models if isinstance(models, list) else []:
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str) and name and not is_remote_name(name, self._patterns):
                names.append(name)
        return names

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
        return KeepWarmResult("pinged", "ok", tuple(done))
