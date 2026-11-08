import json
from pathlib import Path

import pytest

from headwaters.core.catalog.schema import Catalog
from headwaters.core.workspace_ignore import IgnoreMatcher, _cached_git_config

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


# -- HDW-601 (FU-203-4): git's other exclude sources -----------------------------------


@pytest.fixture
def git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point git at a throwaway global config so the developer's real excludes never leak in."""
    home = tmp_path / "home"
    home.mkdir()
    config = home / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    _cached_git_config.cache_clear()
    yield home
    _cached_git_config.cache_clear()


def test_core_excludes_file_from_git_config_is_honoured(tmp_path: Path, git_env: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    global_ignore = tmp_path / "global-ignore"
    _write(global_ignore, "*.log\n")
    _write(git_env / "gitconfig", f"[core]\n\texcludesFile = {global_ignore.as_posix()}\n")
    _cached_git_config.cache_clear()
    m = IgnoreMatcher(repo)
    assert m.is_ignored("debug.log")
    assert m.is_ignored("a/b/debug.log")
    assert not m.is_ignored("debug.py")


def test_git_default_xdg_ignore_file_is_honoured(tmp_path: Path, git_env: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    _write(git_env / "xdg" / "git" / "ignore", "*.bak\n")
    assert IgnoreMatcher(repo).is_ignored("old.bak")


def test_info_exclude_is_honoured(tmp_path: Path, git_env: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / ".git" / "info" / "exclude", "scratch/\n*.tmp\n")
    m = IgnoreMatcher(repo)
    assert m.is_ignored("notes.tmp")
    assert m.is_ignored("scratch/deep/file.py")


def test_linked_worktree_reads_the_shared_info_exclude(tmp_path: Path, git_env: Path) -> None:
    main = tmp_path / "main"
    _write(main / ".git" / "info" / "exclude", "*.snap\n")
    _write(main / ".git" / "worktrees" / "wt" / "commondir", "../..\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _write(worktree / ".git", "gitdir: ../main/.git/worktrees/wt\n")
    assert IgnoreMatcher(worktree).is_ignored("shot.snap")


def test_ignore_files_above_the_root_apply_to_it(tmp_path: Path, git_env: Path) -> None:
    sandbox = tmp_path / "sandbox"
    (sandbox / ".git").mkdir(parents=True)
    _write(sandbox / ".gitignore", "*.log\n")
    root = sandbox / "pkg"
    root.mkdir()
    m = IgnoreMatcher(root)
    assert m.is_ignored("x/debug.log")
    assert not m.is_ignored("keep.py")


def test_a_root_ignore_file_overrides_one_above_it(tmp_path: Path, git_env: Path) -> None:
    sandbox = tmp_path / "sandbox"
    (sandbox / ".git").mkdir(parents=True)
    _write(sandbox / ".gitignore", "*.log\n")
    root = sandbox / "pkg"
    _write(root / ".gitignore", "!keep.log\n")
    m = IgnoreMatcher(root)
    assert m.is_ignored("debug.log")
    assert not m.is_ignored("keep.log")


def test_git_extras_can_be_switched_off(tmp_path: Path, git_env: Path) -> None:
    sandbox = tmp_path / "sandbox"
    _write(sandbox / ".git" / "info" / "exclude", "*.log\n")
    _write(sandbox / ".gitignore", "*.bak\n")
    root = sandbox / "pkg"
    root.mkdir()
    m = IgnoreMatcher(root, _defaults(), use_git_excludes=False)
    assert not m.is_ignored("debug.log")
    assert not m.is_ignored("old.bak")
    assert m.is_ignored(".env")


def test_the_xdg_ignore_file_is_ignored_outside_a_work_tree(tmp_path: Path, git_env: Path) -> None:
    _write(git_env / "xdg" / "git" / "ignore", "*.bak\n")
    loose = tmp_path / "loose"
    loose.mkdir()
    assert not IgnoreMatcher(loose).is_ignored("old.bak")
