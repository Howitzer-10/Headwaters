"""Helpers for the ``-m live`` tests that talk to a real Ollama (HDW-145).

Every helper skips the test when Ollama or the model is missing, and none of them evicts a model
somebody else loaded: a test that needs a free GPU skips when ``/api/ps`` lists a model.
Only loopback is used, and no prompt text is kept.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

__all__ = [
    "HOST",
    "chat",
    "installed",
    "loaded",
    "require_free_gpu",
    "require_model",
    "unload",
]

HOST = "http://127.0.0.1:11434"
_TIMEOUT_S = 600.0


def _client() -> httpx.Client:
    return httpx.Client(base_url=HOST, timeout=_TIMEOUT_S, trust_env=False)


def installed() -> dict[str, float]:
    """Model name to size in GB, from ``/api/tags``; skips the test when Ollama is not running."""
    try:
        with _client() as http:
            reply = http.get("/api/tags", timeout=5.0)
            reply.raise_for_status()
            models = reply.json().get("models") or []
    except (httpx.HTTPError, ValueError):
        pytest.skip("Ollama is not running on 127.0.0.1:11434")
    return {str(m["name"]): float(m.get("size", 0)) / 1e9 for m in models if "name" in m}


def require_model(name: str) -> float:
    """Size in GB of the installed model ``name``; skips the test when it is not pulled."""
    sizes = installed()
    if name not in sizes:
        pytest.skip(f"model {name} is not pulled")
    return sizes[name]


def loaded() -> list[str]:
    """Names of the models Ollama has in memory (``/api/ps``)."""
    with _client() as http:
        reply = http.get("/api/ps", timeout=10.0)
        reply.raise_for_status()
        return [str(m["name"]) for m in reply.json().get("models") or [] if "name" in m]


def require_free_gpu() -> None:
    """Skip when a model is loaded: a live test must not evict what the developer is using."""
    if loaded():
        pytest.skip("Ollama has a model loaded; the test will not evict it")


def unload(model: str) -> None:
    """Free ``model`` now (``keep_alive: 0``) and wait until ``/api/ps`` no longer lists it."""
    with _client() as http:
        http.post("/api/chat", json={"model": model, "messages": [], "keep_alive": 0})
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and model in loaded():
        time.sleep(0.5)


def chat(
    model: str, *, think: Any = None, num_predict: int = 120, keep_alive: str = "1m"
) -> tuple[int, dict[str, Any]]:
    """One non-streamed ``/api/chat`` (status, JSON). ``think`` is sent only when not ``None``."""
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Write a Python function that reverses a string."}
        ],
        "stream": False,
        "options": {"num_ctx": 4096, "num_predict": num_predict},
        "keep_alive": keep_alive,
    }
    if think is not None:
        body["think"] = think
    with _client() as http:
        reply = http.post("/api/chat", json=body)
    try:
        data = reply.json()
    except ValueError:
        data = {}
    return reply.status_code, data if isinstance(data, dict) else {}
