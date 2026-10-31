import os
import subprocess
import sys
from pathlib import Path

import pytest

from headwaters.core.workspace_paths import WorkspaceError
from headwaters.core.workspace_roots import WorkspaceRoots
from headwaters.core.workspace_select import resolve_roots


def _roots(tmp_path: Path) -> WorkspaceRoots:
    ws = tmp_path / "my project"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return resolve_roots([ws])


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")


def test_relative_and_absolute_paths_resolve(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    rel = roots.resolve("src/a.py")
    assert rel.rel == "src/a.py"
    assert rel.display == "src/a.py"
    assert roots.resolve(str(roots.roots[0] / "src" / "a.py")).rel == "src/a.py"
    assert roots.resolve(".").rel == "."
    assert roots.resolve("./src/./a.py").rel == "src/a.py"


@pytest.mark.parametrize("text", ["../outside.txt", "src/../../x", "..\\x", "src\\..\\..\\x"])
def test_traversal_is_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(WorkspaceError) as info:
        _roots(tmp_path).resolve(text)
    assert info.value.code == "traversal"


def test_absolute_path_outside_is_rejected(tmp_path: Path) -> None:
    other = tmp_path / "other.txt"
    other.write_text("secret", encoding="utf-8")
    with pytest.raises(WorkspaceError) as info:
        _roots(tmp_path).resolve(str(other))
    assert info.value.code == "outside_workspace"


def test_sibling_with_common_prefix_is_outside(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    sibling = tmp_path / "my project-evil"
    sibling.mkdir()
    with pytest.raises(WorkspaceError):
        roots.resolve(str(sibling / "f.txt"))


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    _symlink(roots.roots[0] / "link", outside)
    _symlink(roots.roots[0] / "file_link", outside / "secret.txt")
    for text in ("link/secret.txt", "file_link", "link"):
        with pytest.raises(WorkspaceError) as info:
            roots.resolve(text)
        assert info.value.code == "outside_workspace"


def test_symlink_inside_the_workspace_is_allowed(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _symlink(roots.roots[0] / "alias", roots.roots[0] / "src")
    assert roots.resolve("alias/a.py").rel == "src/a.py"


def test_dangling_link_pointing_outside_is_rejected(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _symlink(roots.roots[0] / "gone", tmp_path / "does-not-exist")
    with pytest.raises(WorkspaceError):
        roots.resolve("gone")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path rules")
@pytest.mark.parametrize("text", ["C:foo", "\\Windows\\win.ini", "a.txt:stream", "nul"])
def test_windows_specific_forms_are_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(WorkspaceError):
        _roots(tmp_path).resolve(text)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows case folding")
def test_windows_compares_case_insensitively(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    upper = str(roots.roots[0]).upper() + "\\SRC\\A.PY"
    assert roots.resolve(upper).rel.lower() == "src/a.py"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_junction_escape_is_rejected(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = roots.roots[0] / "junc"
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip("cannot create a junction here")
    with pytest.raises(WorkspaceError):
        roots.resolve("junc/anything.txt")


def test_empty_roots_are_refused() -> None:
    with pytest.raises(WorkspaceError):
        WorkspaceRoots((), "cwd")
