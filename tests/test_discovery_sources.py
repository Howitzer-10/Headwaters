from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.catalog import http as http_mod
from headwaters.core.catalog import sources
from headwaters.core.catalog.http import SourceError
from headwaters.core.catalog.schema import Endpoints
from headwaters.core.rules import OpenAICompatEndpoint
from headwaters.core.user_agent import USER_AGENT

GIB = 1024**3


class FakeHttp:
    """Maps (method, url) to a payload; an Exception value is raised as a failure."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, url, body, headers))
        key = (method, url)
        if key not in self.routes:
            raise SourceError(f"no route {key}")
        value = self.routes[key]
        if isinstance(value, Exception):
            raise value
        return value


OLLAMA = "http://localhost:11434"
SHOW_QWEN = {
    "capabilities": ["completion", "tools"],
    "model_info": {
        "general.architecture": "qwen2",
        "qwen2.block_count": 48,
        "qwen2.attention.head_count": 40,
        "qwen2.attention.head_count_kv": 8,
        "qwen2.embedding_length": 5120,
        "qwen2.context_length": 32768,
    },
}
SHOW_VISION = {
    "capabilities": ["completion", "vision"],
    "model_info": {
        "general.architecture": "gemma4",
        "gemma4.block_count": 42,
        "gemma4.attention.head_count": 8,
        "gemma4.attention.head_count_kv": 2,
        "gemma4.attention.key_length": 512,
        "gemma4.context_length": 131072,
    },
}


def ollama_routes() -> dict[tuple[str, str], Any]:
    return {
        ("GET", f"{OLLAMA}/api/tags"): {
            "models": [
                {"name": "qwen:14b", "size": 9 * GIB},
                {"name": "vis:latest", "size": 6 * GIB},
                {"name": "embed:1", "size": GIB},
                {"name": "", "size": 1},
            ]
        },
        ("POST", f"{OLLAMA}/api/show"): SHOW_QWEN,
        ("GET", f"{OLLAMA}/api/ps"): {
            "models": [{"name": "qwen:14b", "size": 10 * GIB, "size_vram": 8 * GIB}]
        },
    }


def test_ollama_tags_show_ps() -> None:
    routes = ollama_routes()
    shows = {
        "qwen:14b": SHOW_QWEN,
        "vis:latest": SHOW_VISION,
        "embed:1": {"capabilities": ["embedding"]},
    }

    class Http(FakeHttp):
        def request_json(self, method: str, url: str, **kw: Any) -> Any:
            if url.endswith("/api/show"):
                return shows[kw["body"]["model"]]
            return super().request_json(method, url, **kw)

    models, warnings = sources.ollama_models(Http(routes), OLLAMA + "/")
    by_id = {m.id: m for m in models}
    assert set(by_id) == {"qwen:14b", "vis:latest"}  # embedding-only and nameless dropped
    qwen = by_id["qwen:14b"]
    assert qwen.tier == "local"
    assert qwen.provider == "ollama"
    assert qwen.size_gb == 9.0
    assert qwen.tools_ok is True
    assert qwen.ctx == 32768
    assert qwen.arch == {"n_layers": 48, "n_kv_heads": 8, "head_dim": 128}  # 5120 / 40
    assert qwen.warm is True
    assert qwen.size_vram_gb == 8.0
    vis = by_id["vis:latest"]
    assert vis.modalities == ["text", "image"]
    assert vis.tools_ok is False
    assert vis.arch == {"n_layers": 42, "n_kv_heads": 2, "head_dim": 512}  # key_length wins
    assert vis.warm is False
    assert warnings == []


def test_ollama_refuses_non_loopback_host() -> None:
    http = FakeHttp({})
    with pytest.raises(SourceError, match="not loopback"):
        sources.ollama_models(http, "http://192.168.1.5:11434")
    with pytest.raises(SourceError, match="not loopback"):
        sources.ollama_models(http, "https://ollama.com")
    assert http.calls == []


def test_ollama_unreachable_raises_source_error() -> None:
    with pytest.raises(SourceError):
        sources.ollama_models(FakeHttp({}), OLLAMA)


def test_ollama_partial_failures_become_warnings() -> None:
    routes: dict[tuple[str, str], Any] = {
        ("GET", f"{OLLAMA}/api/tags"): {"models": [{"name": "a:1", "size": GIB}]},
        ("POST", f"{OLLAMA}/api/show"): SourceError("boom"),
        ("GET", f"{OLLAMA}/api/ps"): SourceError("down"),
    }
    models, warnings = sources.ollama_models(FakeHttp(routes), OLLAMA)
    assert [m.id for m in models] == ["a:1"]
    assert models[0].tools_ok is None
    assert models[0].arch is None
    assert len(warnings) == 2
    assert any("/api/show" in w for w in warnings)
    assert any("/api/ps" in w for w in warnings)


def test_ollama_arch_missing_keys() -> None:
    assert sources._arch_from_show({}) == (None, None)
    arch, ctx = sources._arch_from_show({"general.architecture": "x", "x.context_length": 4096})
    assert arch is None
    assert ctx == 4096
    arch, _ = sources._arch_from_show(
        {
            "general.architecture": "x",
            "x.block_count": 2,
            "x.attention.head_count": 4,
            "x.embedding_length": 64,
        }
    )
    assert arch == {"n_layers": 2, "n_kv_heads": 4, "head_dim": 16}  # kv heads default to heads


OR = Endpoints().openrouter_base_url
OR_MODELS = {
    "data": [
        {
            "id": "vendor/big",
            "canonical_slug": "vendor/big-2026",
            "context_length": 200000,
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            "supported_parameters": ["tools", "response_format"],
        },
        {
            "id": "vendor/small:free",
            "context_length": 32000,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0", "completion": "0"},
            "supported_parameters": ["temperature"],
        },
        {
            "id": "vendor/imagegen",
            "architecture": {"output_modalities": ["image"]},
        },
        {"id": 5},
        "junk",
    ]
}


def test_openrouter_models_parse() -> None:
    http = FakeHttp({("GET", f"{OR}/models"): OR_MODELS})
    models = {m.id: m for m in sources.openrouter_models(http, "sk-test")}
    assert set(models) == {"vendor/big", "vendor/small:free"}
    big = models["vendor/big"]
    assert big.tier == "paid"
    assert big.provider == "openrouter"
    assert big.modalities == ["text", "image"]
    assert (big.tools_ok, big.json_ok, big.ctx) == (True, True, 200000)
    assert (big.price_in, big.price_out) == (3.0, 15.0)  # per million tokens
    assert big.match_names == ["vendor/big-2026"]
    free = models["vendor/small:free"]
    assert free.tier == "free_cloud"
    assert (free.tools_ok, free.json_ok) == (False, False)
    assert (free.price_in, free.price_out) == (0.0, 0.0)
    assert http.calls[0][3] == {"Authorization": "Bearer sk-test"}


def test_openrouter_models_without_key_sends_no_auth_or_attribution() -> None:
    http = FakeHttp({("GET", f"{OR}/models"): {"data": []}})
    assert sources.openrouter_models(http) == []
    assert http.calls[0][3] is None


def test_openrouter_bad_payload_is_empty() -> None:
    http = FakeHttp({("GET", f"{OR}/models"): [1, 2]})
    assert sources.openrouter_models(http) == []


def test_openrouter_key_info() -> None:
    payload = {
        "data": {
            "is_free_tier": True,
            "limit_remaining": 1.5,
            "free_model_daily_requests": {"limit": 50, "remaining": 0, "used": 50},
            "rate_limit": {"requests": 1},
        }
    }
    info = sources.openrouter_key(FakeHttp({("GET", f"{OR}/key"): payload}), "sk")
    assert info == sources.OpenRouterKeyInfo(True, 50, 0, 1.5)
    empty = sources.openrouter_key(FakeHttp({("GET", f"{OR}/key"): {}}), "sk")
    assert empty == sources.OpenRouterKeyInfo(None, None, None, None)


def test_openrouter_key_error_propagates() -> None:
    http = FakeHttp({("GET", f"{OR}/key"): SourceError("HTTP 401")})
    with pytest.raises(SourceError):
        sources.openrouter_key(http, "bad")


def test_opencode_from_config_and_user_list(tmp_path: Path) -> None:
    cfg = tmp_path / "opencode.jsonc"
    cfg.write_text(
        '{\n // comment with "quotes"\n "provider": {"opencode": {"models": {"big-pickle": {},'
        ' "grok-free": {},}}}, /* block */ "url": "http://x//y"\n}',
        encoding="utf-8",
    )
    models, warnings = sources.opencode_models(
        [cfg, tmp_path / "missing.json"], ["mine", "opencode/big-pickle"]
    )
    assert warnings == []
    by_id = {m.id: m for m in models}
    assert set(by_id) == {"opencode/mine", "opencode/big-pickle", "opencode/grok-free"}
    assert all(m.tier == "free_cloud" and m.retains_data is True for m in models)
    assert by_id["opencode/mine"].match_names == ["mine"]


def test_opencode_bad_config_warns(tmp_path: Path) -> None:
    bad = tmp_path / "opencode.json"
    bad.write_text("{nope", encoding="utf-8")
    folder = tmp_path / "dir.json"
    folder.mkdir()
    models, warnings = sources.opencode_models([bad, folder], [])
    assert models == []
    assert len(warnings) == 2


def test_opencode_config_paths(tmp_path: Path) -> None:
    assert sources.opencode_config_paths("x.json", {}, tmp_path) == [Path("x.json")]
    found = sources.opencode_config_paths(None, {"OPENCODE_CONFIG": "c.json"}, tmp_path)
    assert found[0] == tmp_path / ".config" / "opencode" / "opencode.json"
    assert found[-1] == Path("c.json")
    assert len(sources.opencode_config_paths(None, {}, tmp_path)) == 2


def test_openai_compatible_explicit_and_listed() -> None:
    explicit = OpenAICompatEndpoint(name="mine", base_url="https://x.example/v1", models=["m1"])
    out = sources.openai_compatible_models(FakeHttp({}), explicit, {})
    assert [(m.id, m.tier, m.price_in) for m in out] == [("mine/m1", "free_cloud", 0.0)]
    listed = OpenAICompatEndpoint(name="l", base_url="https://y.example/v1/", api_key_env="K")
    http = FakeHttp({("GET", "https://y.example/v1/models"): {"data": [{"id": "a"}, {"x": 1}]}})
    out = sources.openai_compatible_models(http, listed, {"K": "secret"})
    assert [m.id for m in out] == ["l/a"]
    assert http.calls[0][3] == {"Authorization": "Bearer secret"}


def test_helpers_tolerate_garbage() -> None:
    assert sources._num(True) is None
    assert sources._num("x") is None
    assert sources._num("2") == 2.0
    assert sources._int(-3) is None
    assert sources._int("7") == 7
    assert sources._as_list(1) == []
    assert sources._as_dict(1) == {}


def test_http_loopback_helpers() -> None:
    for host in ("localhost", "127.0.0.1", "::1", "[::1]", "127.5.5.5"):
        assert http_mod.is_loopback_host(host)
    for other in ("example.com", "10.0.0.1", "", None):
        assert not http_mod.is_loopback_host(other)
    assert http_mod.is_loopback_url("http://localhost:11434")
    assert not http_mod.is_loopback_url("https://ollama.com/api")
    assert not http_mod.is_loopback_url("http://localhost.evil.com")


def test_loopback_hostname_is_canonicalised_to_a_numeric_address() -> None:
    assert (
        http_mod.canonical_loopback_url("http://localhost:11434/api/show")
        == "http://127.0.0.1:11434/api/show"
    )
    assert http_mod.canonical_loopback_url("http://localhost/api") == "http://127.0.0.1/api"
    # Already numeric: unchanged (no name to resolve).
    assert (
        http_mod.canonical_loopback_url("http://127.0.0.1:11434/api")
        == "http://127.0.0.1:11434/api"
    )
    # Not loopback, or not a loopback name: never rewritten.
    assert http_mod.canonical_loopback_url("https://example.com/x") == "https://example.com/x"
    assert (
        http_mod.canonical_loopback_url("http://localhost.evil.com:80/x")
        == "http://localhost.evil.com:80/x"
    )


def test_urllib_http_requests_the_numeric_loopback_but_reports_the_configured_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_opener(monkeypatch, lambda r: _Resp(b'{"ok": true}'))
    assert http_mod.UrllibHttp().request_json("GET", "http://localhost:11434/api/tags") == {
        "ok": True
    }
    assert seen[0].full_url == "http://127.0.0.1:11434/api/tags"

    _patch_opener(monkeypatch, lambda r: (_ for _ in ()).throw(OSError("refused")))
    with pytest.raises(SourceError, match="localhost:11434"):
        http_mod.UrllibHttp().request_json("GET", "http://localhost:11434/api/tags")


def test_urllib_http_guards() -> None:
    client = http_mod.UrllibHttp()
    with pytest.raises(SourceError, match="plain http"):
        client.request_json("GET", "http://example.com/x")
    with pytest.raises(SourceError, match="unsupported"):
        client.request_json("GET", "ftp://example.com/x")


class _Resp:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def read(self, n: int) -> bytes:
        return self.raw[:n]


def _patch_opener(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> list[Any]:
    seen: list[Any] = []

    class Opener:
        def open(self, request: Any, timeout: float) -> Any:
            seen.append(request)
            return behaviour(request)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: Opener())
    return seen


def test_urllib_http_sends_a_product_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Cloudflare answers a generic HTTP-library user agent with 403 "error code: 1010".
    seen = _patch_opener(monkeypatch, lambda r: _Resp(b"{}"))
    http_mod.UrllibHttp().request_json("GET", "https://opencode.ai/inference/v1/models")
    sent = seen[0].get_header("User-agent")
    assert sent == USER_AGENT
    assert sent.startswith("headwaters/")
    assert not sent.lower().startswith(("python-urllib", "urllib3", "httpx", "openai"))


def test_urllib_http_success_and_post_body(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_opener(monkeypatch, lambda r: _Resp(b'{"ok": true}'))
    out = http_mod.UrllibHttp().request_json(
        "POST", "http://localhost:11434/api/show", body={"model": "a"}
    )
    assert out == {"ok": True}
    assert json.loads(seen[0].data) == {"model": "a"}
    assert seen[0].get_method() == "POST"


@pytest.mark.parametrize(
    ("behaviour", "message"),
    [
        (lambda r: _Resp(b"not json"), "valid JSON"),
        (lambda r: _Resp(b"x" * 50), "too large"),
        (lambda r: (_ for _ in ()).throw(OSError("refused")), "cannot reach"),
        (
            lambda r: (_ for _ in ()).throw(
                urllib.error.HTTPError(r.full_url, 503, "x", None, None)  # type: ignore[arg-type]
            ),
            "HTTP 503",
        ),
    ],
)
def test_urllib_http_failures(
    monkeypatch: pytest.MonkeyPatch, behaviour: Any, message: str
) -> None:
    _patch_opener(monkeypatch, behaviour)
    client = http_mod.UrllibHttp(max_bytes=20)
    with pytest.raises(SourceError, match=message):
        client.request_json("GET", "https://example.com/x")
