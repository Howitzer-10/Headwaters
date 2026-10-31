import sys
from pathlib import Path

import pytest

from headwaters.core import paths


def test_home_override_redirects_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    p = paths.get_paths()
    root = tmp_path.resolve()
    assert p.config == root / "config"
    assert p.state == root / "state"
    assert p.cache == root / "cache"
    assert p.log == root / "logs"


def test_override_with_spaces_and_ensure_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "dir with spaces"
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(home))
    p = paths.get_paths().ensure_dirs()
    assert all(d.is_dir() for d in (p.config, p.state, p.cache, p.log))
    assert paths.config_dir() == p.config
    assert paths.state_dir() == p.state
    assert paths.cache_dir() == p.cache
    assert paths.log_dir() == p.log


def test_default_dirs_use_app_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    p = paths.get_paths()
    for d in (p.config, p.state, p.cache, p.log):
        assert d.is_absolute()
        assert "Headwaters" in d.parts


@pytest.mark.windows
def test_windows_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    p = paths.get_paths()
    # Config roams (%APPDATA%\Headwaters); state/cache/logs are local (%LOCALAPPDATA%\Headwaters).
    assert "AppData" in p.config.parts
    assert "Roaming" in p.config.parts
    assert "Local" in p.state.parts
    assert "Local" in p.cache.parts
    assert "Local" in p.log.parts
    assert p.config.name == "Headwaters"


@pytest.mark.windows
def test_long_path_gets_extended_prefix(tmp_path: Path) -> None:
    long_dir = tmp_path.joinpath(*["a" * 40] * 8)
    safe = paths.long_safe(long_dir)
    assert str(safe).startswith("\\\\?\\")
    safe.mkdir(parents=True)
    assert safe.is_dir()


def test_short_path_unchanged(tmp_path: Path) -> None:
    assert paths.long_safe(tmp_path) == tmp_path


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows passthrough")
def test_long_path_passthrough_off_windows() -> None:
    long_path = Path("/" + "/".join(["a" * 40] * 8))
    assert paths.long_safe(long_path) == long_path
