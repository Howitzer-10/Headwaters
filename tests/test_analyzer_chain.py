"""HDW-107: layer chain wiring, model resolution and the loopback-only Ollama client."""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from headwaters.core.analyzer import (
    Analyzer,
    AnalyzerChain,
    AnalyzerInput,
    AnalyzerLayer,
    Requirements,
    TinyLlmClassifier,
    build_chain,
)
from headwaters.core.analyzer.classifier import resolve_classifier_model
from headwaters.core.analyzer.ollama_client import (
    ClassifierUnavailableError,
    OllamaHttpClient,
    assert_loopback,
)
from headwaters.core.catalog.schema import Catalog
from headwaters.core.rules import ClassifierRules, LocalTier
from tests.test_analyzer_classifier import VAGUE, FakeClient, answer, base, catalog, msg

__all__ = ["base", "catalog"]  # re-exported fixtures


class StubLayer:
    def __init__(self, name: str, result: Requirements | None = None, boom: bool = False) -> None:
        self.name = name
        self.result = result
        self.boom = boom
        self.calls = 0

    def refine(self, inp: AnalyzerInput, current: Requirements) -> Requirements | None:
        self.calls += 1
        if self.boom:
            raise RuntimeError("layer bug")
        return self.result


def test_layer3_skipped_once_layer2_settles_it(base: Analyzer) -> None:
    current = base.analyze(msg(VAGUE))
    settled = replace(current, confidence=0.9, ambiguous=False, source="knn")
    layer2, layer3 = StubLayer("knn", settled), StubLayer("tiny_llm", None)
    chain = AnalyzerChain(base, layer2=layer2, layer3=layer3)
    assert chain.analyze(msg(VAGUE)).source == "knn"
    assert (layer2.calls, layer3.calls) == (1, 0)
    assert all(isinstance(x, AnalyzerLayer) for x in chain.layers)


def test_chain_falls_through_when_layer2_crashes(base: Analyzer) -> None:
    layer2 = StubLayer("knn", None, boom=True)
    layer3 = StubLayer("tiny_llm", replace(base.analyze(msg(VAGUE)), confidence=0.8, source="x"))
    result = AnalyzerChain(base, layer2=layer2, layer3=layer3).analyze(msg(VAGUE))
    assert (layer2.calls, layer3.calls) == (1, 1)
    assert result.source == "x"


def test_resolve_classifier_model(catalog: Catalog) -> None:
    assert resolve_classifier_model(catalog, LocalTier()) == "llama3.2:1b"
    desktop = LocalTier(profile="desktop_16gb")
    assert resolve_classifier_model(catalog, desktop) == "llama3.2:1b"
    explicit = LocalTier(classifier=ClassifierRules(model="my-tiny:1b"))
    assert resolve_classifier_model(catalog, explicit) == "my-tiny:1b"


def test_build_chain_wires_layer3_and_can_disable(catalog: Catalog) -> None:
    chain = build_chain(catalog, LocalTier(), client=FakeClient(answer()))
    assert [layer.name for layer in chain.layers] == ["tiny_llm"]
    assert not build_chain(catalog, LocalTier(), enable_layer3=False).layers


@pytest.mark.parametrize(
    "host", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434/"]
)
def test_loopback_hosts_allowed(host: str) -> None:
    assert assert_loopback(host) == host.rstrip("/")


@pytest.mark.parametrize(
    "host", ["https://localhost:1", "http://example.com", "http://10.0.0.5:11434", "ftp://x", ""]
)
def test_non_loopback_hosts_refused(host: str) -> None:
    with pytest.raises(ValueError, match="classifier host"):
        assert_loopback(host)
    with pytest.raises(ValueError, match="classifier host"):
        TinyLlmClassifier("m", host=host)


class _Handler(BaseHTTPRequestHandler):
    body: bytes = b""
    seen: list[dict[str, Any]] = []  # noqa: RUF012

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        _Handler.seen.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(_Handler.body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def server() -> Iterator[HTTPServer]:
    _Handler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def test_http_round_trip_on_loopback(server: HTTPServer) -> None:
    _Handler.body = json.dumps(answer("summarize", 2, "low")).encode()
    layer = TinyLlmClassifier("m", host=f"http://127.0.0.1:{server.server_address[1]}")
    label = layer.classify("please summarize")
    assert label is not None
    assert label.task == "summarize"
    assert _Handler.seen[0]["options"]["num_gpu"] == 0


def test_http_errors_map_to_unavailable(server: HTTPServer) -> None:
    client = OllamaHttpClient(f"http://127.0.0.1:{server.server_address[1]}")
    _Handler.body = b"not json"
    with pytest.raises(ClassifierUnavailableError):
        client.chat({}, 1)
    _Handler.body = b"[1]"
    with pytest.raises(ClassifierUnavailableError, match="not an object"):
        client.chat({}, 1)
    with pytest.raises(ClassifierUnavailableError):
        OllamaHttpClient("http://127.0.0.1:9").chat({}, 0.5)


def test_http_client_ignores_proxy_env(monkeypatch: pytest.MonkeyPatch, server: HTTPServer) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.9:3128")
    monkeypatch.setenv("http_proxy", "http://203.0.113.9:3128")
    assert urllib.request.getproxies().get("http")
    _Handler.body = json.dumps(answer()).encode()
    client = OllamaHttpClient(f"http://127.0.0.1:{server.server_address[1]}")
    assert client.chat({}, 2)["message"]
