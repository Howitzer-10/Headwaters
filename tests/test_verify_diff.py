from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from headwaters.core.verify_basic import BasicVerifier

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

DIFF = """--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
 def f():
-    return 1
+    return 2
 # end
"""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(
    tmp_path: Path, *, eol: str, autocrlf: str | None = None, parents: bool = False
) -> Path:
    repo = tmp_path / "repo with space"
    repo.mkdir(parents=parents)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "core.autocrlf", autocrlf or "false")
    (repo / "app.py").write_bytes(
        "def f():\n    return 1\n# end\n".replace("\n", eol).encode("utf-8")
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def wrap(diff: str) -> str:
    return f"```diff\n{diff}```\n"


def verify(repo: Path | None, text: str) -> list[str]:
    v = BasicVerifier(repo=repo)
    return [r.code for r in v.verify_report(text).reasons]


def test_valid_diff_applies_lf_repo(tmp_path: Path) -> None:
    assert verify(make_repo(tmp_path, eol="\n"), wrap(DIFF)) == []


def test_bad_diff_fails(tmp_path: Path) -> None:
    broken = DIFF.replace("return 1", "return 99")
    assert verify(make_repo(tmp_path, eol="\n"), wrap(broken)) == ["diff_does_not_apply"]


def test_lf_diff_applies_to_crlf_working_tree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, eol="\r\n")
    assert verify(repo, wrap(DIFF)) == []


def test_crlf_diff_applies_to_crlf_working_tree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, eol="\r\n")
    assert verify(repo, wrap(DIFF.replace("\n", "\r\n"))) == []


def test_crlf_diff_applies_to_lf_working_tree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, eol="\n")
    assert verify(repo, wrap(DIFF.replace("\n", "\r\n"))) == []


def test_gitattributes_eol_crlf(tmp_path: Path) -> None:
    repo = tmp_path / "attr"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / ".gitattributes").write_text("*.py text eol=crlf\n", encoding="utf-8")
    (repo / "app.py").write_bytes(b"def f():\r\n    return 1\r\n# end\r\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    assert verify(repo, wrap(DIFF)) == []


def test_new_file_diff(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, eol="\n")
    new = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+x = 1\n+y = 2\n"
    assert verify(repo, wrap(new)) == []


def test_unfenced_diff_is_detected(tmp_path: Path) -> None:
    assert verify(make_repo(tmp_path, eol="\n"), DIFF) == []
    assert verify(
        make_repo(tmp_path / "b", eol="\n", parents=True), DIFF.replace("return 1", "nope")
    ) == ["diff_does_not_apply"]


def test_placeholder_only_checked_on_added_lines(tmp_path: Path) -> None:
    from headwaters.core.catalog.schema import NamedPattern

    v = BasicVerifier([NamedPattern(name="todo", regex=r"\bTODO\b")])
    removed = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-# TODO old\n+x = 1\n"
    added = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+# TODO new\n"
    assert v.verify_report(wrap(removed)).passed
    assert [r.code for r in v.verify_report(wrap(added)).reasons if r.check == "placeholders"] == [
        "placeholder"
    ]


def test_no_repo_skips_the_apply_check() -> None:
    report = BasicVerifier().verify_report(wrap(DIFF))
    assert report.passed
    assert next(c for c in report.checks if c.name == "diff").status == "skipped"


def test_git_missing_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("headwaters.core.verify_diff.shutil.which", lambda _name: None)
    report = BasicVerifier(repo=tmp_path).verify_report(wrap(DIFF))
    assert report.passed
    assert next(c for c in report.checks if c.name == "diff").status == "skipped"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CRLF autocrlf case")
def test_windows_autocrlf_true_crlf_diff(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, eol="\r\n", autocrlf="true")
    assert verify(repo, wrap(DIFF.replace("\n", "\r\n"))) == []
    assert verify(repo, wrap(DIFF)) == []
