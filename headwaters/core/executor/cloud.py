"""Cloud backend: the ``litellm`` SDK behind the executor interface (HDW-115, D7).

litellm is imported lazily, on the first cloud call only. A local-scoped request never gets here,
so no cloud client is ever constructed for it. All litellm use stays in this module.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from collections.abc import AsyncIterator
from typing import Any

from headwaters.core.executor.cloud_map import delta_from_chunk, map_exception, reasoning_kwargs
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import BackendRequest, Delta
from headwaters.core.logging import RedactionFilter

LITELLM_LOGGERS = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")
_HANDLER_NAME = "headwaters-litellm"


def _quiet_litellm_loggers() -> None:
    """Route litellm's loggers through redaction and keep them off stdout (F8).

    litellm installs its own handler that writes records below WARNING (and proxy warnings) to
    ``sys.stdout``, which would corrupt MCP stdio. Its handlers are replaced by one stderr handler
    carrying ``RedactionFilter``; the level is capped at WARNING. Idempotent.
    """
    for name in LITELLM_LOGGERS:
        logger = logging.getLogger(name)
        for old in list(logger.handlers):
            logger.removeHandler(old)  # litellm's own, or ours from an earlier load
        handler = logging.StreamHandler(sys.stderr)  # current stderr, never stdout
        handler.set_name(_HANDLER_NAME)
        handler.addFilter(RedactionFilter())
        logger.addHandler(handler)
        if not any(isinstance(f, RedactionFilter) for f in logger.filters):
            logger.addFilter(RedactionFilter())
        logger.setLevel(logging.WARNING)
        logger.propagate = False


def _load_litellm() -> Any:
    # No network fetch of the model-cost map at import, and no telemetry.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    module = importlib.import_module("litellm")
    # litellm 1.106.6 attribute names (A-LLM-6); no type stubs, so setattr.
    setattr(module, "telemetry", False)  # noqa: B010
    setattr(module, "suppress_debug_info", True)  # noqa: B010 - stops print() in exception mapping
    setattr(module, "set_verbose", False)  # noqa: B010
    _quiet_litellm_loggers()
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
