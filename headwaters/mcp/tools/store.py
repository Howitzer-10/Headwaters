"""In-memory store behind ``headwaters://result/<id>`` (HDW-204, v3.4 B1).

A tool result is compact: a short answer, unified diffs and verification codes. The full detail
(the whole model answer, the routing decision, every check) is kept here and read on demand through
the resource. It lives in the daemon's memory only: nothing is written to disk, the task text and
file contents are never put in it, and the oldest entries are dropped past ``MAX_RESULTS``.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

__all__ = ["MAX_RESULTS", "RESULT_URI_PREFIX", "ResultStore", "result_uri"]

MAX_RESULTS = 200  # first-draft bound; see docs/ASSUMPTIONS.md
RESULT_URI_PREFIX = "headwaters://result/"


def result_uri(result_id: str) -> str:
    return RESULT_URI_PREFIX + result_id


class ResultStore:
    def __init__(self, max_results: int = MAX_RESULTS) -> None:
        if max_results < 1:
            raise ValueError("max_results must be at least 1")
        self._max = max_results
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, detail: Mapping[str, Any]) -> str:
        """Keep ``detail`` and return its id (URL-safe, unguessable)."""
        result_id = secrets.token_urlsafe(12)
        entry = {"id": result_id, "created_at": round(time.time(), 3), **detail}
        with self._lock:
            self._items[result_id] = entry
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return result_id

    def get(self, result_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._items.get(result_id)
            return None if entry is None else dict(entry)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
