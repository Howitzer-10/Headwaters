"""VRAM that Ollama itself holds for its loaded models (HDW-144, FU-201-2).

``GET {host}/api/ps`` lists the loaded models with ``size_vram`` in bytes. The GPU-busy check
subtracts this from the used VRAM, so Ollama's own model is never counted as another app's.
Loopback hosts only; any failure gives ``None`` (unknown), never an exception.
"""

from __future__ import annotations

from collections.abc import Callable

from headwaters.core.catalog.http import HttpClient, SourceError, UrllibHttp, is_loopback_url

_MIB = 1024 * 1024
_TIMEOUT_S = 2.0


def parse_ps_vram_mb(reply: object) -> float | None:
    """Sum of ``size_vram`` over the loaded models, in MiB; ``None`` for an unreadable reply."""
    models = reply.get("models") if isinstance(reply, dict) else None
    if models is None and isinstance(reply, dict) and not reply:
        return 0.0  # an empty object: nothing loaded
    if not isinstance(models, list):
        return None
    total = 0.0
    for item in models:
        size = item.get("size_vram") if isinstance(item, dict) else None
        if isinstance(size, int | float) and not isinstance(size, bool) and size > 0:
            total += size / _MIB
    return total


def read_ollama_vram_mb(host: str, http: HttpClient | None = None) -> float | None:
    """MiB of VRAM Ollama holds right now, or ``None`` when it cannot be read."""
    base = host.rstrip("/")
    if not is_loopback_url(base):
        return None
    client = http or UrllibHttp(timeout=_TIMEOUT_S)
    try:
        return parse_ps_vram_mb(client.request_json("GET", f"{base}/api/ps"))
    except (SourceError, OSError, ValueError):
        return None


def ollama_vram_reader(host: str, http: HttpClient | None = None) -> Callable[[], float | None]:
    return lambda: read_ollama_vram_mb(host, http)
