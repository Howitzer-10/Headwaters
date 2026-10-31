from pathlib import Path

import pytest

from headwaters.core.workspace_files import TRUNCATION_MARK, estimate_tokens, truncate_to_tokens
from headwaters.core.workspace_pack import pack_files
from headwaters.core.workspace_select import resolve_roots

DEFAULTS = [".env", "*.pem", ".git/"]


def _put(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8", newline="")


def test_problems_are_reported_without_content(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _put(ws / ".env", "KEY=sk-secret\n")
    _put(ws / "keys" / "x.pem", "PEM")
    _put(ws / ".gitignore", "skip.txt\n")
    _put(ws / "skip.txt", "ignored")
    _put(ws / "bin.dat", b"abc\x00def")
    _put(ws / "big.txt", "z" * 100)
    _put(ws / "ok.txt", "fine\n")
    (ws / "sub").mkdir()
    outside = tmp_path / "outside.txt"
    _put(outside, "outside-text")
    asked = [".env", "keys/x.pem", "skip.txt", "bin.dat", "big.txt", "sub", "nope.py",
             "../outside.txt", str(outside), "C:foo", "ok.txt"]  # fmt: skip
    result = pack_files(
        resolve_roots([ws]), asked, 1000, default_ignore=DEFAULTS, max_file_bytes=50
    )
    got = {e.requested: e.status for e in result.entries}
    assert got[".env"] == "ignored"
    assert got["keys/x.pem"] == "ignored"
    assert got["skip.txt"] == "ignored"
    assert got["bin.dat"] == "binary"
    assert got["big.txt"] == "too_large"
    assert got["sub"] == "directory"
    assert got["nope.py"] == "missing"
    assert got["../outside.txt"] == "invalid"
    assert got[str(outside)] == "outside"
    assert got["ok.txt"] == "included"
    rendered = result.render()
    assert "sk-secret" not in rendered
    assert "outside-text" not in rendered
    assert all(e.text == "" for e in result.entries if e.status != "included")


def test_unreadable_file_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _put(ws / "a.py", "a\n")
    original = Path.open

    def boom(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == "a.py":
            raise PermissionError("denied")
        return original(self, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", boom)
    assert pack_files(resolve_roots([ws]), ["a.py"], 100).entries[0].status == "unreadable"


def test_truncate_to_tokens_is_line_aligned() -> None:
    text = "".join(f"line {i}\n" for i in range(50))
    cut = truncate_to_tokens(text, 30, estimate_tokens)
    assert cut.endswith(TRUNCATION_MARK)
    assert estimate_tokens(cut) <= 30
    assert cut.removesuffix(TRUNCATION_MARK).split("\n")[-1].startswith("line ")
    assert truncate_to_tokens(text, 2, estimate_tokens) == ""
