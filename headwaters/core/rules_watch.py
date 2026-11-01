"""Hot reload of ``rules.yaml``: debounced file watching with last-good fallback.

``RulesManager.current`` always returns a valid ``Rules``. When an edit is invalid the error
is logged and kept in ``last_error``; the previous good settings stay active.

The watcher covers an idle daemon. A request never waits for it (HDW-148, FU-131-4): ``current``
compares the file's ``(mtime_ns, size)`` with the signature of the last read (one ``os.stat``) and
reloads first when it differs, so a hand edit is seen by the very next read. The signature is
kept even when the file is invalid, so an unfinished save is parsed once, not on every request.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from headwaters.core import paths
from headwaters.core.logging import get_logger
from headwaters.core.rules import Rules, Scope
from headwaters.core.rules_io import (
    ConfigError,
    ensure_rules_file,
    load_rules,
    read_yaml_mapping,
    rules_path,
)

DEFAULT_DEBOUNCE_S = 0.3

_log = get_logger("rules")


def _fail_safe_rules(path: Path) -> tuple[Rules, str]:
    """Rules for an unreadable ``rules.yaml``: keep any valid lock/scope value, else go local."""
    try:
        data = read_yaml_mapping(path)
    except ConfigError:
        data = {}
    kept: dict[str, Any] = {}
    for key in ("private_lock", "airplane"):
        if isinstance(data.get(key), bool):
            kept[key] = data[key]
    if data.get("default_scope") in get_args(Scope):
        kept["default_scope"] = data["default_scope"]
    if not kept:
        return Rules(default_scope="local"), "no setting could be read; starting in local scope"
    return Rules.model_validate(kept), f"keeping {', '.join(sorted(kept))} and defaults"


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


Signature = tuple[int, int] | None


class RulesManager:
    """Owns the live ``Rules``; reloads on file change, at the next read or after a quiet period.

    ``on_reload`` may run on any thread that reads ``current`` (a request thread, the watcher
    timer, the API thread), so it must be safe to call from there. It never runs concurrently
    with itself, and a reader that arrives while it runs waits for it to finish.
    """

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
        self._lock = threading.Lock()  # guards the fields below, held only briefly
        self._reload_lock = threading.RLock()  # one reload at a time, hook included
        self._timer: threading.Timer | None = None
        self._observer: BaseObserver | None = None
        self._rules = Rules()
        self._seen: Signature = None  # signature of the file at the last read
        self._applying = False  # ``on_reload`` is running: readers wait for it
        self.last_error: ConfigError | None = None
        self._load_initial()

    def set_on_reload(self, callback: Callable[[Rules], None] | None) -> None:
        """Set the hook that runs after new valid rules were applied (the daemon's rebuild)."""
        self._on_reload = callback

    def _signature(self) -> Signature:
        try:
            info = os.stat(paths.long_safe(self.path))
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def _load_initial(self) -> None:
        ensure_rules_file(self.path)
        self._seen = self._signature()
        try:
            self._rules = load_rules(self.path)
        except ConfigError as exc:
            # No previous good config exists (F27): keep what can be read of the safety
            # settings; if nothing can be read, start in local scope and say why.
            self.last_error = exc
            self._rules, note = _fail_safe_rules(self.path)
            _log.error("rules invalid at startup, %s: %s", note, exc)

    @property
    def current(self) -> Rules:
        """The live rules. A file that changed since the last read is read first (one stat)."""
        if self._applying or self._signature() != self._seen:
            with self._reload_lock:
                # Re-check under the lock: another thread may have reloaded while this one waited.
                if self._signature() != self._seen:
                    self._reload_locked()
        with self._lock:
            return self._rules

    def reload(self) -> bool:
        """Re-read the file now. Returns True when valid rules are in force after the read."""
        with self._reload_lock:
            return self._reload_locked()

    def _reload_locked(self) -> bool:
        # Signature first: an edit that lands during the read differs next time and is read again.
        seen = self._signature()
        try:
            fresh = load_rules(self.path)
        except ConfigError as exc:
            with self._lock:
                self.last_error = exc
                self._seen = seen
            _log.warning("rules reload rejected, keeping last good config: %s", exc)
            self._call_hook(self._on_error, exc)
            return False
        with self._lock:
            changed = fresh != self._rules
            # ``_applying`` goes up before the new rules and signature are visible, so a reader
            # that sees them also sees the flag and waits for the hook (the engine rebuild).
            self._applying = changed and self._on_reload is not None
            self._rules = fresh
            self.last_error = None
            self._seen = seen
        if changed:
            _log.info("rules reloaded")
            try:
                self._call_hook(self._on_reload, fresh)
            finally:
                self._applying = False
        return True

    @staticmethod
    def _call_hook(hook: Callable[[Any], None] | None, arg: Any) -> None:
        """A failing hook must never fail the request that happened to trigger the reload."""
        if hook is None:
            return
        try:
            hook(arg)
        except Exception:
            _log.exception("rules hook failed")

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
