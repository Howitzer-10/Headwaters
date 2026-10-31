import pytest

from headwaters.core.workspace_paths import (
    WorkspaceError,
    check_path_text,
    is_within,
    relative_posix,
    strip_extended_prefix,
)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "empty_path"),
        ("   ", "empty_path"),
        ("a\x00b", "invalid_chars"),
        ("a\nb", "invalid_chars"),
        ("../etc/passwd", "traversal"),
        ("a/../../b", "traversal"),
        ("a\\..\\b", "traversal"),
        ("..", "traversal"),
        ("C:\\x\\..\\..\\y", "traversal"),
        ("\\\\?\\C:\\secret", "device_path"),
        ("\\\\.\\PhysicalDrive0", "device_path"),
        ("//?/C:/secret", "device_path"),
    ],
)
@pytest.mark.parametrize("windows", [True, False])
def test_refused_on_every_platform(text: str, code: str, windows: bool) -> None:
    with pytest.raises(WorkspaceError) as info:
        check_path_text(text, windows=windows)
    assert info.value.code == code


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("C:foo", "drive_relative"),
        ("C:", "drive_relative"),
        ("a.txt:hidden", "stream"),
        ("C:\\x\\a.txt::$DATA", "stream"),
        ("a.txt.", "invalid_name"),
        ("a.txt ", "invalid_name"),
        ("a*.txt", "invalid_name"),
        ("dir\\what?.txt", "invalid_name"),
        ("CON", "reserved_name"),
        ("src\\nul.txt", "reserved_name"),
        ("com1", "reserved_name"),
        ("LPT9.log", "reserved_name"),
    ],
)
def test_windows_only_rules(text: str, code: str) -> None:
    with pytest.raises(WorkspaceError) as info:
        check_path_text(text, windows=True)
    assert info.value.code == code


@pytest.mark.parametrize(
    "text",
    ["src/app.py", "C:\\Users\\me\\my project\\a b.py", "C:/x/y.txt", "\\\\srv\\share\\f.txt",
     "dir/.hidden", "a..b/c.py", "...x/y", "./a.py", "comet.txt", "console/x.py"],
)  # fmt: skip
@pytest.mark.parametrize("windows", [True, False])
def test_ordinary_paths_pass(text: str, windows: bool) -> None:
    check_path_text(text, windows=windows)


def test_posix_allows_colons_and_trailing_dots() -> None:
    check_path_text("a:b/c.", windows=False)


def test_is_within_windows_is_case_insensitive_and_drive_aware() -> None:
    assert is_within("c:\\work\\Proj\\a.py", "C:\\Work\\proj", windows=True)
    assert is_within("C:\\Work\\proj", "C:\\Work\\proj", windows=True)
    assert not is_within("D:\\Work\\proj\\a.py", "C:\\Work\\proj", windows=True)
    assert not is_within("C:\\Work\\project2\\a.py", "C:\\Work\\proj", windows=True)
    assert not is_within("C:\\Work\\a.py", "C:\\Work\\proj", windows=True)


def test_is_within_unc_matches_server_and_share() -> None:
    root = "\\\\Server\\Share\\proj"
    assert is_within("\\\\server\\SHARE\\proj\\a.py", root, windows=True)
    assert not is_within("\\\\server\\other\\proj\\a.py", root, windows=True)
    assert not is_within("\\\\evil\\Share\\proj\\a.py", root, windows=True)
    assert is_within("\\\\?\\UNC\\Server\\Share\\proj\\a.py", root, windows=True)


def test_is_within_posix_is_case_sensitive() -> None:
    assert is_within("/w/Proj/a.py", "/w/Proj", windows=False)
    assert not is_within("/w/proj/a.py", "/w/Proj", windows=False)
    assert not is_within("/w/Proj2/a.py", "/w/Proj", windows=False)


def test_extended_prefix_is_stripped_for_comparison() -> None:
    assert strip_extended_prefix("\\\\?\\C:\\x") == "C:\\x"
    assert strip_extended_prefix("\\\\?\\UNC\\s\\sh\\x") == "\\\\s\\sh\\x"
    assert strip_extended_prefix("C:\\x") == "C:\\x"
    assert is_within("\\\\?\\C:\\Work\\a.py", "C:\\work", windows=True)


def test_relative_posix() -> None:
    assert relative_posix("C:\\W\\p\\src\\a.py", "c:\\w\\p", windows=True) == "src/a.py"
    assert relative_posix("/w/p", "/w/p", windows=False) == "."
    assert relative_posix("/w/p/a/b.py", "/w/p", windows=False) == "a/b.py"
