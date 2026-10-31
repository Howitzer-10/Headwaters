import json
from pathlib import Path

import pytest

from headwaters.core.catalog.schema import Catalog
from headwaters.core.workspace_ignore import IgnoreMatcher

_SEED = Path(__file__).resolve().parents[1] / "catalog" / "seed_catalog.json"


def _defaults() -> list[str]:
    return Catalog.model_validate(
        json.loads(_SEED.read_text(encoding="utf-8"))
    ).default_ignore_patterns


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "rel",
    [".env", ".env.local", ".env.production", "config/.env", "app/prod.env", ".envrc",
     "certs/server.pem", "keys/id_rsa", "a/id_ed25519", "deploy/key.p12", ".aws/credentials",
     ".ssh/config", "x/.ssh/id_rsa", ".git/config", "secrets.yaml", "gcp/service-account-1.json",
     ".npmrc", ".netrc", ".headwaters/state.db", "CERT.PEM", ".ENV"],
)  # fmt: skip
def test_default_secret_patterns_are_ignored(tmp_path: Path, rel: str) -> None:
    assert IgnoreMatcher(tmp_path, _defaults()).is_ignored(rel)


@pytest.mark.parametrize(
    "rel", [".env.example", ".env.sample", "src/app.py", "README.md", "environment.py", "a/b.txt"]
)
def test_ordinary_files_are_not_ignored(tmp_path: Path, rel: str) -> None:
    assert not IgnoreMatcher(tmp_path, _defaults()).is_ignored(rel)


def test_gitignore_is_honored(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "build/\n*.log\n!keep.log\n/top.txt\n# comment\n")
    m = IgnoreMatcher(tmp_path)
    assert m.is_ignored("build/out.js")
    assert m.is_ignored("build", is_dir=True)
    assert m.is_ignored("a/b/x.log")
    assert not m.is_ignored("keep.log")
    assert m.is_ignored("top.txt")
    assert not m.is_ignored("sub/top.txt")
    assert not m.is_ignored("src/app.py")


def test_headwatersignore_is_honored(tmp_path: Path) -> None:
    _write(tmp_path / ".headwatersignore", "private/\n*.draft\n")
    m = IgnoreMatcher(tmp_path)
    assert m.is_ignored("private/notes.md")
    assert m.is_ignored("a/b.draft")
    assert not m.is_ignored("a/b.md")


def test_headwatersignore_overrides_gitignore_in_one_directory(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "*.gen.py\n")
    _write(tmp_path / ".headwatersignore", "!api.gen.py\n")
    m = IgnoreMatcher(tmp_path)
    assert m.is_ignored("other.gen.py")
    assert not m.is_ignored("api.gen.py")


def test_nested_ignore_file_applies_only_below_its_directory(tmp_path: Path) -> None:
    _write(tmp_path / "pkg" / ".gitignore", "local.txt\n/only_here.txt\n")
    m = IgnoreMatcher(tmp_path)
    assert m.is_ignored("pkg/local.txt")
    assert m.is_ignored("pkg/deep/local.txt")
    assert m.is_ignored("pkg/only_here.txt")
    assert not m.is_ignored("pkg/deep/only_here.txt")
    assert not m.is_ignored("local.txt")


def test_deeper_file_can_reinclude_a_shallower_rule(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "*.txt\n")
    _write(tmp_path / "docs" / ".gitignore", "!readme.txt\n")
    m = IgnoreMatcher(tmp_path)
    assert m.is_ignored("a.txt")
    assert not m.is_ignored("docs/readme.txt")
    assert m.is_ignored("docs/other.txt")


def test_children_of_an_ignored_directory_stay_ignored(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "vendor/\n!vendor/keep.py\n")
    assert IgnoreMatcher(tmp_path).is_ignored("vendor/keep.py")


def test_user_negation_cannot_reinclude_a_secret_file(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "!.env\n!*.pem\n")
    _write(tmp_path / ".headwatersignore", "!.env\n")
    m = IgnoreMatcher(tmp_path, _defaults())
    assert m.is_ignored(".env")
    assert m.is_ignored("a/server.pem")


def test_root_and_empty_paths_are_never_ignored(tmp_path: Path) -> None:
    m = IgnoreMatcher(tmp_path, _defaults())
    assert not m.is_ignored(".")
    assert not m.is_ignored("")


def test_oversized_ignore_file_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "*\n" + "# pad\n" * 60000)
    assert not IgnoreMatcher(tmp_path).is_ignored("a.py")


def test_ignore_file_cannot_be_a_directory(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").mkdir()
    assert not IgnoreMatcher(tmp_path).is_ignored("a.py")


def test_seed_catalog_defaults_validate() -> None:
    assert ".env" in _defaults()
