"""Cloud backend: the ``litellm`` SDK behind the executor interface (HDW-115, D7).

litellm is imported lazily, on the first cloud call only. A local-scoped request never gets here,
so no cloud client is ever constructed for it. All litellm use stays in this module.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import AsyncIterator
from typing import Any

from headwaters.core.executor.cloud_map import delta_from_chunk, map_exception, reasoning_kwargs
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import BackendRequest, Delta


def _load_litellm() -> Any:
    # No network fetch of the model-cost map at import, and no telemetry.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    module = importlib.import_module("litellm")
    setattr(module, "telemetry", False)  # noqa: B010 - litellm ships no type stubs for it
    return module


def build_kwargs(request: BackendRequest) -> dict[str, Any]:
    target = request.target
    options = request.options
    kwargs: dict[str, Any] = {
        "model": target.route,
        "messages": [dict(m) for m in request.messages],
        "stream": True,
        "stream_options": {"include_usage": True},
        "num_retries": 0,
        "max_retries": 0,
    }
    if request.tools:
        kwargs["tools"] = [dict(t) for t in request.tools]
    if request.api_key:
        kwargs["api_key"] = request.api_key
    if target.api_base:
        kwargs["api_base"] = target.api_base
    if request.headers:
        kwargs["extra_headers"] = dict(request.headers)
    if options.max_tokens is not None:
        kwargs["max_tokens"] = options.max_tokens
    if options.temperature is not None:
        kwargs["temperature"] = options.temperature
    if options.json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": dict(options.json_schema)},
        }
    kwargs.update(reasoning_kwargs(target.provider, options.reasoning))
    return kwargs


class LiteLLMBackend:
    """Streams ``litellm.acompletion``. Retries are off: failover is the router's job."""

    def __init__(self, module: Any | None = None) -> None:
        self._module = module

    def _litellm(self) -> Any:
        if self._module is None:
            self._module = _load_litellm()
        return self._module

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        kwargs = build_kwargs(request)
        litellm = self._litellm()
        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                yield delta_from_chunk(chunk)
        except ProviderError:
            raise
        except Exception as exc:  # litellm raises its own exception tree
            raise map_exception(exc) from exc
