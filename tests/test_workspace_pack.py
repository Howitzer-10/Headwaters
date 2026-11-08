import math
import os
from pathlib import Path

import pytest

from headwaters.core.plugins import ContextPlugin
from headwaters.core.workspace_files import (
    DEFAULT_CHARS_PER_TOKEN,
    TRUNCATION_MARK,
    estimate_tokens,
)
from headwaters.core.workspace_pack import pack_files
from headwaters.core.workspace_select import resolve_roots

DEFAULTS = [".env", "*.pem", ".git/"]


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _put(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8", newline="")


def test_files_are_included_in_the_given_order(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _put(ws / "b.py", "b = 2\n")
    _put(ws / "a.py", "a = 1\n")
    result = pack_files(resolve_roots([ws]), ["b.py", "a.py"], 1000)
    assert [e.display for e in result.files] == ["b.py", "a.py"]
    assert result.render() == "### b.py\nb = 2\n\n### a.py\na = 1\n"
    assert result.used_tokens == estimate_tokens("b = 2\n") + estimate_tokens("a = 1\n")


def test_budget_truncates_the_first_file_that_does_not_fit(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _put(ws / "a.py", "line one\n" * 10)
    _put(ws / "b.py", "line two\n" * 10)
    _put(ws / "c.py", "c\n")
    budget = estimate_tokens("line one\n" * 10) + 6 + estimate_tokens(TRUNCATION_MARK)
    result = pack_files(resolve_roots([ws]), ["a.py", "b.py", "c.py"], budget)
    status = {e.display: e.status for e in result.entries}
    assert status == {"a.py": "included", "b.py": "truncated", "c.py": "budget"}
    cut = result.entries[1]
    assert cut.text.endswith(TRUNCATION_MARK)
    assert cut.text.count("line two") < 10
    assert cut.total_tokens > cut.tokens
    assert result.used_tokens <= budget


def test_nothing_fits_gives_budget_status(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _put(ws / "a.py", "x" * 500)  # one long line: never cut mid-line
    result = pack_files(resolve_roots([ws]), ["a.py"], 20)
    assert result.entries[0].status == "budget"
    assert result.files == ()
    assert result.used_tokens == 0


def test_custom_token_counter_is_used(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _put(ws / "a.txt", "one two three\n")
    result = pack_files(resolve_roots([ws]), ["a.txt"], 100, token_counter=lambda t: len(t.split()))
    assert result.entries[0].tokens == 3


def test_crlf_is_normalized_and_bom_removed(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _put(ws / "w.txt", b"\xef\xbb\xbfa\r\nb\r\n")
    assert pack_files(resolve_roots([ws]), ["w.txt"], 100).entries[0].text == "a\nb\n"


def test_duplicate_paths_are_read_once(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _put(ws / "a.py", "a\n")
    result = pack_files(resolve_roots([ws]), ["a.py", "./a.py", str(ws / "a.py")], 100)
    assert len(result.entries) == 1


def test_symlink_to_secret_outside_is_not_read(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    secret = tmp_path / "secret.txt"
    _put(secret, "top secret")
    try:
        os.symlink(secret, ws / "innocent.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    result = pack_files(resolve_roots([ws]), ["innocent.txt"], 100)
    assert result.entries[0].status == "outside"
    assert "top secret" not in result.render()


def test_pack_can_back_a_context_plugin(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _put(ws / "a.py", "a\n")
    roots = resolve_roots([ws])

    class Packer:
        api_version = 1

        def build_context(self, query: str, budget_tokens: int) -> str:
            return pack_files(roots, [query], budget_tokens).render()

    plugin = Packer()
    assert isinstance(plugin, ContextPlugin)
    assert plugin.build_context("a.py", 50) == "### a.py\na\n"


# -- HDW-601 (FU-203-7): sizes are decided before a large file is read -------------------


def test_a_file_over_the_size_cap_is_rejected_from_one_stat(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _put(ws / "big.py", "x" * 2000)
    result = pack_files(resolve_roots([ws]), ["big.py"], 10_000, max_file_bytes=1000)
    entry = result.entries[0]
    assert entry.status == "too_large"
    assert (entry.tokens, entry.text) == (0, "")


def test_a_file_that_cannot_fit_is_cut_cheaply_and_reports_a_size_bound(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    size = 20_000
    _put(ws / "big.py", "line\n" * 4000)
    result = pack_files(resolve_roots([ws]), ["big.py"], 50)
    entry = result.entries[0]
    assert entry.status == "truncated"
    assert 0 < entry.tokens <= 50
    # not read whole: total_tokens is the byte ceiling, the safe direction for a budget
    assert entry.total_tokens == math.ceil(size / DEFAULT_CHARS_PER_TOKEN)


def test_files_after_the_cut_report_their_own_size_bound(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _put(ws / "first.py", "line\n" * 4000)
    _put(ws / "second.py", "y\n" * 500)
    result = pack_files(resolve_roots([ws]), ["first.py", "second.py"], 20)
    assert [e.status for e in result.entries] == ["truncated", "budget"]
    second = result.entries[1]
    assert (second.tokens, second.text) == (0, "")
    assert second.total_tokens == math.ceil(1000 / DEFAULT_CHARS_PER_TOKEN)


def test_a_wide_character_file_that_fits_is_still_included(tmp_path: Path) -> None:
    """2000 four-byte characters read as 8000 bytes but cost 2000 characters, not 8000."""
    ws = _ws(tmp_path)
    body = "\U0001F600" * 2000 + "\n"
    _put(ws / "emoji.py", body)
    result = pack_files(resolve_roots([ws]), ["emoji.py"], 700)
    entry = result.entries[0]
    assert entry.status == "included"
    assert entry.tokens == entry.total_tokens
    assert entry.text == body


def test_a_token_counter_still_reads_the_whole_file(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    body = "line\n" * 4000
    _put(ws / "big.py", body)
    seen: list[str] = []

    def counter(text: str) -> int:
        seen.append(text)
        return len(text)

    result = pack_files(resolve_roots([ws]), ["big.py"], 25_000, token_counter=counter)
    assert result.entries[0].status == "included"
    assert seen
    assert seen[0] == body
