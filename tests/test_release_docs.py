"""The generated per-host pages (HDW-407).

The pages are rendered from the same adapters and templates the installer uses, so the drift
check is the real test: what is committed must be what the code would write today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.control import release_docs
from headwaters.installer.hosts import all_adapters, host_ids

REPO_ROOT = Path(__file__).resolve().parents[1]


def rendered_names() -> set[str]:
    return set(release_docs.render_all())


def test_the_committed_pages_are_current() -> None:
    assert release_docs.stale_pages(REPO_ROOT) == []
    for name in rendered_names():
        assert (REPO_ROOT / name).is_file(), name


def test_one_page_per_host_plus_an_index() -> None:
    names = rendered_names()
    assert len(names) == len(host_ids()) + 1
    for identifier in host_ids():
        assert f"docs/hosts/{identifier}.md" in names
    assert "docs/hosts/index.md" in names


def test_a_missing_page_is_stale_and_write_pages_fixes_it(tmp_path: Path) -> None:
    assert sorted(release_docs.stale_pages(tmp_path)) == sorted(rendered_names())
    written = release_docs.write_pages(tmp_path)
    assert sorted(written) == sorted(rendered_names())
    assert release_docs.stale_pages(tmp_path) == []
    assert release_docs.write_pages(tmp_path) == []


def test_written_pages_end_with_one_newline_and_no_crlf(tmp_path: Path) -> None:
    release_docs.write_pages(tmp_path)
    for name in rendered_names():
        data = (tmp_path / name).read_bytes()
        assert b"\r\n" not in data, name
        assert data.endswith(b"\n"), name
        assert not data.endswith(b"\n\n"), name


def test_a_hand_edit_is_reported_and_overwritten(tmp_path: Path) -> None:
    release_docs.write_pages(tmp_path)
    page = tmp_path / "docs/hosts/vscode.md"
    page.write_text("edited by hand\n", encoding="utf-8", newline="\n")
    assert release_docs.stale_pages(tmp_path) == ["docs/hosts/vscode.md"]
    assert release_docs.write_pages(tmp_path) == ["docs/hosts/vscode.md"]
    assert release_docs.stale_pages(tmp_path) == []


def test_crlf_is_not_drift(tmp_path: Path) -> None:
    release_docs.write_pages(tmp_path)
    page = tmp_path / "docs/hosts/cline.md"
    text = page.read_text(encoding="utf-8")
    page.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    assert release_docs.stale_pages(tmp_path) == []


@pytest.mark.parametrize("name", sorted(rendered_names()))
def test_no_page_carries_a_machine_path(name: str) -> None:
    text = release_docs.render_all()[name]
    assert str(Path.home()) not in text
    assert "Users\\" not in text
    assert "/home/" not in text
    assert release_docs._ABSOLUTE.search(text) is None, text


@pytest.mark.parametrize("name", sorted(rendered_names()))
def test_every_page_says_how_it_is_made(name: str) -> None:
    text = release_docs.render_all()[name]
    assert text.startswith(release_docs.GENERATED_NOTE)
    assert "headwaters release-docs" in text


def test_every_page_uses_the_placeholders() -> None:
    pages = release_docs.render_all()
    for name, text in pages.items():
        if name == "docs/hosts/index.md":
            continue
        assert any(f"<{token}>" in text for token in ("project", "home", "state", "appdata")), name
    assert "<state>/daemon.token" in pages["docs/hosts/vscode.md"]
    assert (REPO_ROOT / "docs/hosts/index.md").read_text(encoding="utf-8")


def test_the_index_links_every_host_page() -> None:
    index = release_docs.render_all()["docs/hosts/index.md"]
    for adapter in all_adapters():
        assert f"[{adapter.id}]({adapter.id}.md)" in index
        assert adapter.title in index
    assert "[troubleshooting](../TROUBLESHOOTING.md)" in index


@pytest.mark.parametrize("adapter", all_adapters(), ids=lambda adapter: adapter.id)
def test_each_page_names_the_templates_behind_it(adapter) -> None:
    text = release_docs.render_all()[f"docs/hosts/{adapter.id}.md"]
    assert adapter.title in text
    for name in adapter.template_names:
        assert f"`{name}`" in text, name


def test_display_rewrites_a_home_directory_to_a_placeholder() -> None:
    ctx = release_docs.display_context()
    assert release_docs.display(str(ctx.home), ctx) == "<home>"
    assert release_docs.display(str(ctx.state), ctx) == "<state>"
    assert release_docs.display(str(ctx.state / "daemon.token"), ctx) == "<state>/daemon.token"


def test_display_rewrites_an_unknown_absolute_path() -> None:
    ctx = release_docs.display_context()
    assert release_docs.display("C:\\models\\Qwen3-8B-GGUF", ctx) == "<path>"
    assert release_docs.display("put it in C:\\a\\b or D:/c/d", ctx) == "put it in <path> or <path>"
    assert release_docs.display("~/models", ctx) == "~/models"
    assert release_docs.display("no path at all", ctx) == "no path at all"


def test_display_uses_forward_slashes() -> None:
    ctx = release_docs.display_context()
    out = release_docs.display(str(ctx.project / "sub" / "file.json"), ctx)
    assert out == "<project>/sub/file.json"


def test_display_context_is_off_the_live_profile_and_the_live_port() -> None:
    ctx = release_docs.display_context()
    assert ctx.home == Path(release_docs._MARKERS["home"])
    assert ctx.project == Path(release_docs._MARKERS["project"])
    assert ctx.port == 4000
    assert release_docs.display_context() == ctx


def test_page_name_and_hosts_dir_agree() -> None:
    adapter = all_adapters()[0]
    assert release_docs.page_name(adapter) == f"{adapter.id}.md"
    assert release_docs.hosts_dir(REPO_ROOT) == REPO_ROOT / "docs" / "hosts"
