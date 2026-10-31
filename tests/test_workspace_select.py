import os
from pathlib import Path

import pytest

from headwaters.core.workspace_paths import WorkspaceError
from headwaters.core.workspace_select import resolve_roots, uri_to_path


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def test_cwd_is_the_fallback(tmp_path: Path) -> None:
    roots = resolve_roots(cwd=tmp_path)
    assert roots.source == "cwd"
    assert roots.roots == (_real(tmp_path),)


def test_missing_cwd_is_refused(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        resolve_roots(cwd=tmp_path / "nope")


def test_configured_root_must_be_an_absolute_directory(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        resolve_roots(["relative/dir"])
    with pytest.raises(WorkspaceError):
        resolve_roots([tmp_path / "missing"])


def test_configured_root_beats_cwd(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    roots = resolve_roots([tmp_path / "ws"], cwd=tmp_path)
    assert roots.source == "configured"
    assert roots.roots == (_real(tmp_path / "ws"),)


def test_host_roots_narrow_the_configured_root(tmp_path: Path) -> None:
    (tmp_path / "ws" / "pkg").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    roots = resolve_roots(
        [tmp_path / "ws"],
        [(tmp_path / "ws" / "pkg").as_uri(), str(tmp_path / "elsewhere"), "https://x/y"],
    )
    assert roots.source == "host"
    assert roots.roots == (_real(tmp_path / "ws" / "pkg"),)
    assert len(roots.dropped_host_roots) == 2
    with pytest.raises(WorkspaceError):
        roots.resolve(str(tmp_path / "ws" / "other.txt"))


def test_host_roots_never_widen_the_configured_root(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    roots = resolve_roots([tmp_path / "ws"], [str(tmp_path)])
    assert roots.source == "configured"
    assert roots.roots == (_real(tmp_path / "ws"),)
    assert roots.dropped_host_roots == (str(tmp_path),)


def test_host_roots_are_used_without_a_configured_root(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    roots = resolve_roots(host_roots=[str(tmp_path / "a"), (tmp_path / "b").as_uri()])
    assert roots.source == "host"
    assert len(roots.roots) == 2
    assert roots.resolve(str(tmp_path / "b" / "f.txt")).display == "b:f.txt"


def test_relative_host_roots_are_dropped(tmp_path: Path) -> None:
    roots = resolve_roots(host_roots=["rel/dir"], cwd=tmp_path)
    assert roots.source == "cwd"
    assert roots.dropped_host_roots == ("rel/dir",)


def test_roots_off_ignores_host_roots(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    roots = resolve_roots(host_roots=[str(tmp_path / "a")], cwd=tmp_path, use_host_roots=False)
    assert roots.source == "cwd"
    assert roots.dropped_host_roots == ()


def test_duplicate_host_roots_collapse(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    roots = resolve_roots(host_roots=[str(tmp_path / "a"), (tmp_path / "a").as_uri()])
    assert len(roots.roots) == 1


def test_uri_to_path() -> None:
    assert uri_to_path("file://server/share/my%20dir", windows=True) == Path(
        "//server/share/my dir"
    )
    assert uri_to_path("file://localhost/tmp/x", windows=False).name == "x"
    with pytest.raises(WorkspaceError):
        uri_to_path("file://server/share/dir", windows=False)
    with pytest.raises(WorkspaceError):
        uri_to_path("http://x/y")
