"""Watch a workspace and feed changed paths to the index (HDW-601).

The watcher is deliberately dumb: ``watchdog`` reports an event, the path is remembered in a set,
and ``drain`` hands the whole set to ``SymbolIndex.update`` in one transaction. Nothing is indexed
on the watchdog thread, so a build can never hold up an event and two builds can never interleave.

``drain`` is caller-driven rather than timer-driven, because the daemon already owns a timer and a
second one here would only add a thread to reason about. ``note`` is public so a watcher can be
exercised without starting an observer: an event and a call are the same thing.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .index import IndexStats, SymbolIndex


class _Handler(FileSystemEventHandler):
    """Turns every watchdog event into the paths that were actually touched."""

    def __init__(self, watcher: IndexWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._watcher.note(str(event.src_path))
        destination = getattr(event, "dest_path", "")
        if destination:
            self._watcher.note(str(destination))


class IndexWatcher:
    """A recursive ``watchdog`` observer over ``index.root``, with a pending-path set."""

    def __init__(self, index: SymbolIndex, *, recursive: bool = True) -> None:
        self._index = index
        self._recursive = recursive
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._observer: Any = None

    @property
    def index(self) -> SymbolIndex:
        return self._index

    @property
    def running(self) -> bool:
        return self._observer is not None

    def start(self) -> None:
        """Start watching. Calling it twice is a no-op."""
        if self._observer is not None:
            return
        observer = Observer()
        observer.schedule(_Handler(self), str(self._index.root), recursive=self._recursive)
        observer.start()
        self._observer = observer

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop watching. Calling it twice is a no-op."""
        observer = self._observer
        self._observer = None
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=timeout)

    def note(self, path: str) -> None:
        """Remember one changed path (absolute, or already root-relative)."""
        rel = self._relative(path)
        if rel is None:
            return
        with self._lock:
            self._pending.add(rel)

    def pending(self) -> list[str]:
        with self._lock:
            return sorted(self._pending)

    def drain(self) -> IndexStats | None:
        """Index everything noted since the last drain. ``None`` when nothing was noted."""
        with self._lock:
            paths = sorted(self._pending)
            self._pending.clear()
        if not paths:
            return None
        return self._index.update(paths, force=True)

    def _relative(self, path: str) -> str | None:
        text = str(path).strip().replace("\\", "/")
        if not text:
            return None
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                text = candidate.relative_to(self._index.root).as_posix()
            except ValueError:
                try:
                    text = candidate.resolve().relative_to(self._index.root.resolve()).as_posix()
                except (OSError, ValueError):
                    return None
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if not parts or ".." in parts:  # never let an event name a path outside the root
            return None
        return "/".join(parts)
