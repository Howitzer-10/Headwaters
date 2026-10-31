"""Hot reload of ``rules.yaml``: debounced file watching with last-good fallback.

``RulesManager.current`` always returns a valid ``Rules``. When an edit is invalid the error
is logged and kept in ``last_error``; the previous good settings stay active.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from headwaters.core import paths
from headwaters.core.logging import get_logger
from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError, ensure_rules_file, load_rules, rules_path

DEFAULT_DEBOUNCE_S = 0.3

_log = get_logger("rules")


class _Handler(FileSystemEventHandler):
    def __init__(self, target: Path, on_change: Callable[[], None]) -> None:
        self._name = target.name.lower()
        self._on_change = on_change

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # Editors save by rename/replace, so check the destination as well as the source.
        for raw in (event.src_path, getattr(event, "dest_path", "")):
            if raw and Path(str(raw)).name.lower() == self._name:
                self._on_change()
                return


class RulesManager:
    """Owns the live ``Rules``; reloads on file change after a quiet period."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        debounce_s: float = DEFAULT_DEBOUNCE_S,
        on_reload: Callable[[Rules], None] | None = None,
        on_error: Callable[[ConfigError], None] | None = None,
    ) -> None:
        self.path = path or rules_path()
        self._debounce_s = debounce_s
        self._on_reload = on_reload
        self._on_error = on_error
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._observer: BaseObserver | None = None
        self._rules = Rules()
        self.last_error: ConfigError | None = None
        self._load_initial()

    def _load_initial(self) -> None:
        ensure_rules_file(self.path)
        try:
            self._rules = load_rules(self.path)
        except ConfigError as exc:
            # No previous good config exists: run on built-in defaults and report.
            self.last_error = exc
            _log.error("rules invalid at startup, using defaults: %s", exc)

    @property
    def current(self) -> Rules:
        with self._lock:
            return self._rules

    def reload(self) -> bool:
        """Re-read the file now. Returns True when new valid rules were applied."""
        try:
            fresh = load_rules(self.path)
        except ConfigError as exc:
            with self._lock:
                self.last_error = exc
            _log.warning("rules reload rejected, keeping last good config: %s", exc)
            if self._on_error:
                self._on_error(exc)
            return False
        with self._lock:
            changed = fresh != self._rules
            self._rules = fresh
            self.last_error = None
        if changed:
            _log.info("rules reloaded")
            if self._on_reload:
                self._on_reload(fresh)
        return True

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self._debounce_s, self.reload)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def start(self) -> None:
        if self._observer is not None:
            return
        directory = paths.long_safe(self.path.parent)
        directory.mkdir(parents=True, exist_ok=True)
        observer = Observer()
        observer.schedule(_Handler(self.path, self._schedule), str(directory), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
