"""Filesystem locations for Headwaters.

Config lives in the roaming dir (``%APPDATA%\\Headwaters`` on Windows); state, cache and logs
live in the local dir (``%LOCALAPPDATA%\\Headwaters``). Setting ``HEADWATERS_HOME`` redirects
everything under one root (used by tests).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "Headwaters"
HOME_ENV_VAR = "HEADWATERS_HOME"

# Windows legacy MAX_PATH is 260; leave headroom for file names appended later.
_WIN_LONG_PATH_THRESHOLD = 240
_WIN_EXTENDED_PREFIX = "\\\\?\\"
_WIN_UNC_EXTENDED_PREFIX = "\\\\?\\UNC\\"


@dataclass(frozen=True)
class HeadwatersPaths:
    """Resolved directory set. Directories are not created until ``ensure_dirs``."""

    config: Path
    state: Path
    cache: Path
    log: Path

    def ensure_dirs(self) -> HeadwatersPaths:
        for directory in (self.config, self.state, self.cache, self.log):
            long_safe(directory).mkdir(parents=True, exist_ok=True)
        return self


def _home_override() -> Path | None:
    raw = os.environ.get(HOME_ENV_VAR, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def get_paths() -> HeadwatersPaths:
    """Resolve the config/state/cache/log dirs, honouring ``HEADWATERS_HOME``."""
    root = _home_override()
    if root is not None:
        return HeadwatersPaths(
            config=root / "config",
            state=root / "state",
            cache=root / "cache",
            log=root / "logs",
        )
    return HeadwatersPaths(
        config=Path(platformdirs.user_config_dir(APP_NAME, appauthor=False, roaming=True)),
        state=Path(platformdirs.user_state_dir(APP_NAME, appauthor=False)),
        cache=Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False)),
        log=Path(platformdirs.user_log_dir(APP_NAME, appauthor=False)),
    )


def config_dir() -> Path:
    return get_paths().config


def state_dir() -> Path:
    return get_paths().state


def cache_dir() -> Path:
    return get_paths().cache


def log_dir() -> Path:
    return get_paths().log


def long_safe(path: Path | str) -> Path:
    """Return ``path`` in a form the OS can open even when it exceeds MAX_PATH.

    On Windows, absolute paths longer than the threshold get the ``\\\\?\\`` extended prefix.
    Elsewhere, and for short paths, the path is returned unchanged. Paths with spaces need no
    special handling because we never build shell strings from them.
    """
    candidate = Path(path)
    if sys.platform != "win32":
        return candidate
    text = str(candidate)
    if text.startswith(_WIN_EXTENDED_PREFIX) or len(text) < _WIN_LONG_PATH_THRESHOLD:
        return candidate
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):  # UNC share
        return Path(_WIN_UNC_EXTENDED_PREFIX + absolute[2:])
    return Path(_WIN_EXTENDED_PREFIX + absolute)
