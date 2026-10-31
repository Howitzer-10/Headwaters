"""Structured, redacted logging.

Logs go to stderr and a rotating file, never stdout (MCP stdio carries JSON-RPC only).
Prompts, outputs and keys are never logged: fields named ``prompt``, ``content`` or
``messages`` are dropped, and key/token-like strings are masked.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from headwaters.core import paths

REDACTED = "[REDACTED]"
LOGGER_NAME = "headwaters"
LOG_FILE_NAME = "headwaters.log"
DEBUG_ENV_VAR = "HEADWATERS_DEBUG"
_HANDLER_NAME = "headwaters-managed"

# Extra-field names that may carry user content: dropped entirely.
DROPPED_FIELDS = frozenset({"prompt", "content", "messages"})
# Extra-field names whose values are always secrets.
SECRET_FIELDS = frozenset(
    {"api_key", "apikey", "token", "access_token", "secret", "password", "authorization"}
)

# Token-shaped strings. These are log-safety patterns, not routing data; secret *detection*
# for routing (with placeholder handling) is a separate core module.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),  # OpenAI, Anthropic (sk-ant-), OpenRouter (sk-or-)
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}(?![A-Za-z0-9+/_-])"),  # long opaque blobs
)
# name=value / name: value assignments; keeps the name, masks the value.
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|authorization)"
    r"(\s*[=:]\s*)(['\"]?)[^\s'\",;&]{4,}"
)


def redact_text(text: str) -> str:
    """Mask key/token-like substrings in ``text``."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", text)
    return text


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Redact a value of any JSON-ish shape; drops forbidden fields inside mappings."""
    if key is not None and key.lower() in SECRET_FIELDS:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(k): redact_value(v, key=str(k))
            for k, v in value.items()
            if str(k).lower() not in DROPPED_FIELDS
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value))


# Attributes present on every LogRecord; anything else came from ``extra=``.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class RedactionFilter(logging.Filter):
    """Scrubs secrets from the message, exception text and ``extra`` fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        for name in list(record.__dict__):
            if name in _STANDARD_ATTRS:
                continue
            if name.lower() in DROPPED_FIELDS:
                del record.__dict__[name]
            else:
                record.__dict__[name] = redact_value(record.__dict__[name], key=name)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, plus extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, value in record.__dict__.items():
            if name not in _STANDARD_ATTRS:
                payload[name] = value
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False, default=str)


def _is_debug_env() -> bool:
    return os.environ.get(DEBUG_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def configure_logging(
    *,
    debug: bool | None = None,
    log_file: Path | None = None,
    to_file: bool = True,
    max_bytes: int = 1_000_000,
    backup_count: int = 3,
) -> logging.Logger:
    """Configure the ``headwaters`` logger: stderr + rotating file, redacted, JSON lines.

    Idempotent: previous Headwaters handlers are removed first. ``debug`` defaults to the
    ``HEADWATERS_DEBUG`` environment variable. Never writes to stdout.
    """
    if debug is None:
        debug = _is_debug_env()
    logger = logging.getLogger(LOGGER_NAME)
    shutdown_logging()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if to_file:
        target = log_file or (paths.log_dir() / LOG_FILE_NAME)
        paths.long_safe(target.parent).mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                paths.long_safe(target),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
        )
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RedactionFilter())
        handler.set_name(_HANDLER_NAME)
        logger.addHandler(handler)
    return logger


def shutdown_logging() -> None:
    """Remove and close handlers installed by ``configure_logging``."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if handler.get_name() == _HANDLER_NAME:
            logger.removeHandler(handler)
            handler.close()


def get_logger(name: str | None = None) -> logging.Logger:
    """Child logger under ``headwaters`` (``get_logger('router')`` -> ``headwaters.router``)."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
