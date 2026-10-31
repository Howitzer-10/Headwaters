from __future__ import annotations

import base64
import io
import json
import socket
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from typer.testing import CliRunner

from headwaters.cli.main import app
from headwaters.core.catalog import trust, updater
from headwaters.core.catalog.updater import FetchResult
from headwaters.core.rules import Rules

REPO_ROOT = Path(__file__).resolve().parent.parent
URL = "https://example.invalid/catalog.json"
SEED_BYTES = (REPO_ROOT / "catalog" / "seed_catalog.json").read_bytes()
runner = CliRunner()


def _keys() -> tuple[Ed25519PrivateKey, str]:
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, base64.b64encode(raw).decode("ascii")


KEY, PUB = _keys()
OTHER_KEY, _ = _keys()


def sign(body: bytes, key: Ed25519PrivateKey = KEY) -> bytes:
    return base64.b64encode(key.sign(body))


def make_catalog(version: str = "1.0.0") -> bytes:
    data: dict[str, Any] = json.loads(SEED_BYTES)
    data["catalog_version"] = version
    return json.dumps(data).encode("utf-8")


class FakeServer:
    def __init__(self, body: bytes, signature: bytes, etag: str = '"v1"') -> None:
        self.body, self.signature, self.etag = body, signature, etag
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, url: str, etag: str | None) -> FetchResult:
        self.calls.append((url, etag))
        if url.endswith(".sig"):
            return FetchResult(200, self.signature)
        if etag is not None and etag == self.etag:
            return FetchResult(304, None, etag)
        return FetchResult(200, self.body, self.etag)


def rules(**kw: Any) -> Rules:
    return Rules.model_validate({"catalog": {"url": URL}, **kw})


def run_update(r: Rules, server: Any, tmp_path: Path, **kw: Any) -> updater.UpdateResult:
    return updater.update(r, fetch=server, public_key_b64=PUB, directory=tmp_path, **kw)


def forbid_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Socket-level mock: any attempt to connect is recorded and fails the call."""
    opened: list[object] = []

    def spy(*a: Any, **k: Any) -> Any:
        opened.append(a)
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "create_connection", spy)
    monkeypatch.setattr(socket.socket, "connect", spy)
    monkeypatch.setattr(urllib.request, "urlopen", spy)
    return opened


def test_update_installs_signed_catalog(tmp_path: Path) -> None:
    body = make_catalog("2.0.0")
    result = run_update(rules(), FakeServer(body, sign(body)), tmp_path)
    assert result.outcome == "updated"
    catalog, source = updater.load_active(PUB, tmp_path)
    assert (source, catalog.catalog_version) == ("cache", "2.0.0")


def test_tampered_catalog_is_rejected(tmp_path: Path) -> None:
    good = make_catalog("2.0.0")
    tampered = good.replace(b"2.0.0", b"9.9.9")
    result = run_update(rules(), FakeServer(tampered, sign(good)), tmp_path)
    assert result.outcome == "error"
    assert "signature" in result.message
    assert updater.load_active(PUB, tmp_path)[1] == "seed"


def test_wrong_key_and_garbage_signature_rejected(tmp_path: Path) -> None:
    body = make_catalog()
    wrong = FakeServer(body, sign(body, OTHER_KEY))
    assert run_update(rules(), wrong, tmp_path).outcome == "error"
    assert run_update(rules(), FakeServer(body, b"not base64!"), tmp_path).outcome == "error"


def test_signed_but_schema_invalid_is_rejected(tmp_path: Path) -> None:
    body = b'{"schema_version": 1}'
    result = run_update(rules(), FakeServer(body, sign(body)), tmp_path)
    assert result.outcome == "error"
    assert updater.load_active(PUB, tmp_path)[1] == "seed"


def test_no_embedded_key_rejects_everything(tmp_path: Path) -> None:
    body = make_catalog()
    result = updater.update(
        rules(), fetch=FakeServer(body, sign(body)), public_key_b64="", directory=tmp_path
    )
    assert result.outcome == "error"
    assert "public key" in result.message


def test_304_leaves_cache_unchanged(tmp_path: Path) -> None:
    body = make_catalog("2.0.0")
    server = FakeServer(body, sign(body))
    run_update(rules(), server, tmp_path)
    cache = tmp_path / updater.CURRENT_NAME
    before = cache.read_bytes()
    result = run_update(rules(), server, tmp_path)
    assert result.outcome == "not_modified"
    assert cache.read_bytes() == before
    assert server.calls[-1] == (URL, '"v1"')
    assert not (tmp_path / updater.PREVIOUS_NAME).exists()


def test_failed_update_keeps_last_good_and_previous_is_kept(tmp_path: Path) -> None:
    v1, v2 = make_catalog("1.0.0"), make_catalog("2.0.0")
    run_update(rules(), FakeServer(v1, sign(v1), '"a"'), tmp_path)
    run_update(rules(), FakeServer(v2, sign(v2), '"b"'), tmp_path)
    assert updater.load_active(PUB, tmp_path)[0].catalog_version == "2.0.0"
    bad = FakeServer(v2.replace(b"2.0.0", b"6.6.6"), sign(v2), '"c"')
    assert run_update(rules(), bad, tmp_path).outcome == "error"
    assert updater.load_active(PUB, tmp_path)[0].catalog_version == "2.0.0"
    (tmp_path / updater.CURRENT_NAME).write_text("{broken", encoding="utf-8")
    catalog, source = updater.load_active(PUB, tmp_path)
    assert (source, catalog.catalog_version) == ("previous", "1.0.0")


def test_tampered_cache_on_disk_is_not_trusted(tmp_path: Path) -> None:
    body = make_catalog("2.0.0")
    run_update(rules(), FakeServer(body, sign(body)), tmp_path)
    path = tmp_path / updater.CURRENT_NAME
    path.write_text(path.read_text("utf-8").replace("2.0.0", "7.7.7"), encoding="utf-8")
    assert updater.load_active(PUB, tmp_path)[1] == "seed"


def test_network_error_is_reported_not_raised(tmp_path: Path) -> None:
    def boom(url: str, etag: str | None) -> FetchResult:
        raise OSError("offline")

    assert run_update(rules(), boom, tmp_path).outcome == "error"
    assert updater.load_active(PUB, tmp_path)[1] == "seed"


def test_offline_startup_uses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = make_catalog("3.0.0")
    run_update(rules(), FakeServer(body, sign(body)), tmp_path)
    opened = forbid_network(monkeypatch)
    catalog, source = updater.load_active(PUB, tmp_path)
    assert (source, catalog.catalog_version) == ("cache", "3.0.0")
    assert not opened


_LOCAL = {"url": URL, "disable_when_local": True}


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"airplane": True}, "airplane"),
        ({"catalog": _LOCAL, "private_lock": True}, "local-only"),
        ({"catalog": _LOCAL, "default_scope": "local"}, "local-only"),
        ({"catalog": {"url": None}}, "not configured"),
    ],
)
def test_blocked_states_make_zero_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], reason: str
) -> None:
    opened = forbid_network(monkeypatch)
    r = Rules.model_validate({"catalog": {"url": URL}, **overrides})
    result = updater.update(r, directory=tmp_path, public_key_b64=PUB)  # real default fetcher
    assert result.outcome == "disabled"
    assert reason in result.message
    assert not opened
    assert not list(tmp_path.iterdir())


def test_auto_update_off_blocks_background_but_not_manual() -> None:
    r = rules(catalog={"url": URL, "auto_update": False})
    assert updater.blocked_reason(r, manual=False) == "catalog.auto_update is off"
    assert updater.blocked_reason(r, manual=True) is None


class _Resp(io.BytesIO):
    status = 200

    def __init__(self, data: bytes, etag: str | None = None) -> None:
        super().__init__(data)
        self.headers = Message()
        if etag:
            self.headers["ETag"] = etag

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


def test_default_fetch_requires_https() -> None:
    with pytest.raises(ValueError, match="https"):
        updater.default_fetch("http://example.invalid/c.json", None)


def test_default_fetch_handles_response_and_304(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, timeout: float) -> _Resp:
        seen.append(req)
        if req.get_header("If-none-match"):
            raise urllib.error.HTTPError(req.full_url, 304, "nm", Message(), None)
        return _Resp(b"{}", '"x"')

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    first = updater.default_fetch(URL, None)
    assert (first.status, first.body, first.etag) == (200, b"{}", '"x"')
    assert updater.default_fetch(URL, '"x"').status == 304
    assert set(seen[0].headers) == {"User-agent"}  # no prompt data, nothing else sent


def test_default_fetch_other_http_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(req: urllib.request.Request, timeout: float) -> _Resp:
        raise urllib.error.HTTPError(req.full_url, 500, "boom", Message(), None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    with pytest.raises(urllib.error.HTTPError):
        updater.default_fetch(URL, None)


def test_default_fetch_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "MAX_BYTES", 4)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Resp(b"12345678"))
    with pytest.raises(ValueError, match="too large"):
        updater.default_fetch(URL, None)


def test_trust_verify_paths() -> None:
    body = b"hello"
    trust.verify(body, sign(body), PUB)
    trust.verify(body, sign(body).decode(), PUB)
    with pytest.raises(trust.SignatureError):
        trust.verify(body + b"x", sign(body), PUB)
    with pytest.raises(trust.SignatureError, match="malformed"):
        trust.verify(body, sign(body), "###")


def test_status_reports_sources(tmp_path: Path) -> None:
    r = rules()
    info = updater.status(r, PUB, tmp_path)
    assert (info.source, info.etag, info.update_blocked) == ("seed", None, None)
    body = make_catalog("4.0.0")
    run_update(r, FakeServer(body, sign(body)), tmp_path, now=1000.0)
    info = updater.status(r, PUB, tmp_path)
    assert (info.source, info.catalog_version, info.etag) == ("cache", "4.0.0", '"v1"')
    assert info.fetched_at == 1000.0
    assert updater.status(rules(airplane=True), PUB, tmp_path).update_blocked is not None


def _write_rules(home: Path, text: str) -> None:
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "rules.yaml").write_text(text, encoding="utf-8")


def test_cli_status_and_airplane_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWATERS_HOME", str(tmp_path))
    status = runner.invoke(app, ["catalog", "status"])
    assert status.exit_code == 0
    assert "Active catalog: seed" in status.output
    assert "not configured" in status.output
    _write_rules(tmp_path, f"airplane: true\ncatalog:\n  url: {URL}\n")
    opened = forbid_network(monkeypatch)
    result = runner.invoke(app, ["catalog", "update"])
    assert result.exit_code == 0
    assert "airplane" in result.output
    assert not opened


def test_cli_update_error_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWATERS_HOME", str(tmp_path))
    _write_rules(tmp_path, f"catalog:\n  url: {URL}\n")

    def down(url: str, etag: str | None) -> FetchResult:
        raise OSError("down")

    monkeypatch.setattr(updater, "default_fetch", down)
    result = runner.invoke(app, ["catalog", "update"])
    assert result.exit_code == 1
    assert "rejected" in result.output


def test_cli_status_shows_cache_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWATERS_HOME", str(tmp_path))
    body = make_catalog("5.0.0")
    cache = tmp_path / "cache" / updater.CACHE_SUBDIR
    run_update(rules(), FakeServer(body, sign(body)), cache, now=1000.0)
    monkeypatch.setattr(trust, "EMBEDDED_PUBLIC_KEY_B64", PUB)
    result = runner.invoke(app, ["catalog", "status"])
    assert "Active catalog: cache (version 5.0.0)" in result.output
    assert "Last fetched:" in result.output


def test_cli_bad_rules_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWATERS_HOME", str(tmp_path))
    _write_rules(tmp_path, "airplane: maybe\n")
    assert runner.invoke(app, ["catalog", "status"]).exit_code == 2
