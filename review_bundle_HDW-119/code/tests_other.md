# Tests: everything else

Optional. Read only to check a specific claim.

43 files.

| File | Lines | Purpose |
| --- | ---: | --- |
| `tests/__init__.py` | 1 |  |
| `tests/analyzer_fixtures_a.py` | 138 | Fixture prompts for HDW-106, part A: code, vision, agentic. |
| `tests/analyzer_fixtures_b.py` | 115 | Fixture prompts for HDW-106, part B: JSON, chat, reasoning, duck, sizing. |
| `tests/contracts/__init__.py` | 1 |  |
| `tests/contracts/harness.py` | 83 | Contract harness: run any plugin implementation against its interface. |
| `tests/contracts/test_example_contracts.py` | 23 | Run the contract harness against every plugin registered by discovered features. |
| `tests/test_analyzer_chain.py` | 153 | HDW-107: layer chain wiring, model resolution and the loopback-only Ollama client. |
| `tests/test_analyzer_classifier.py` | 187 | HDW-107: analyzer layer 3 (tiny CPU classifier): payload, cache, fallback, parsing. |
| `tests/test_analyzer_requirements.py` | 160 | HDW-106: analyzer layer 1. Fixture prompts produce the expected requirements. |
| `tests/test_catalog.py` | 132 |  |
| `tests/test_catalog_updater.py` | 329 |  |
| `tests/test_cli.py` | 26 |  |
| `tests/test_cli_misc.py` | 97 | HDW-118: ``version``, ``features``, ``catalog`` and the shared JSON/exit-code conventions. |
| `tests/test_cli_models.py` | 98 | HDW-118: ``headwaters models list`` shows fit and num_ctx per model; refresh keeps working. |
| `tests/test_cli_scope.py` | 136 | HDW-118: ``headwaters scope`` and ``private`` (and the rules.yaml edit under them). |
| `tests/test_cli_status.py` | 87 | HDW-118: ``headwaters status``: scope, locks, project rule, walls with ETAs, warm model. |
| `tests/test_discovery_merge.py` | 147 |  |
| `tests/test_discovery_refresh.py` | 215 |  |
| `tests/test_discovery_sources.py` | 374 |  |
| `tests/test_executor_cloud.py` | 188 | HDW-115: litellm backend and mapping helpers, with litellm fully mocked. |
| `tests/test_executor_ollama.py` | 133 | HDW-115: Ollama native /api/chat backend against a mocked transport (no real network). |
| `tests/test_executor_ollama_convert.py` | 52 | HDW-115: OpenAI-style messages -> Ollama native messages. |
| `tests/test_executor_timeouts.py` | 60 | HDW-115: first-token timeout is longer for a cold local model; idle timeout after streaming. |
| `tests/test_features.py` | 80 | Feature registry, plugin interfaces and discovery. |
| `tests/test_features_discovery.py` | 128 | Discovery of ★ features and entry points; core must not import them. |
| `tests/test_hardware_monitor.py` | 84 | HDW-108: HardwareMonitor detection order, graceful no-GPU degrade, caching. |
| `tests/test_hardware_probes.py` | 139 | HDW-108: NVML and nvidia-smi probes with mocked driver output. |
| `tests/test_hardware_state.py` | 152 | HDW-108: reserve, profile, battery, GPU-busy and the HardwareState snapshot. |
| `tests/test_hardware_sysmem.py` | 77 | HDW-109: sysmem-fallback detection and residency (warm bonus / eviction) inputs. |
| `tests/test_logging.py` | 133 |  |
| `tests/test_paths.py` | 72 |  |
| `tests/test_project_config.py` | 76 |  |
| `tests/test_rules.py` | 135 |  |
| `tests/test_rules_edit.py` | 108 | HDW-118: targeted, validated, atomic edits of rules.yaml. |
| `tests/test_rules_watch.py` | 91 |  |
| `tests/test_sign_catalog_script.py` | 35 |  |
| `tests/test_state.py` | 139 |  |
| `tests/test_state_records.py` | 152 |  |
| `tests/test_stdio.py` | 69 |  |
| `tests/test_verify_basic.py` | 95 |  |
| `tests/test_verify_diff.py` | 134 |  |
| `tests/test_verify_scan.py` | 85 |  |
| `tests/test_verify_schema.py` | 97 |  |

## tests/__init__.py

````python

````

## tests/analyzer_fixtures_a.py

````python
"""Fixture prompts for HDW-106, part A: code, vision, agentic."""

from __future__ import annotations

from typing import Any

from headwaters.core.analyzer import AnalyzerInput

PNG = "data:image/png;base64,AAAA"
TOOL = {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}
CLINE_SYSTEM = "You are Cline, a highly skilled software engineer. " + "Rules and tools. " * 500
IMG = {"type": "image_url", "image_url": {"url": PNG}}

Fixture = tuple[str, AnalyzerInput, dict[str, Any]]


def user(text: Any) -> dict[str, Any]:
    return {"role": "user", "content": text}


def case(**kwargs: Any) -> dict[str, Any]:
    return kwargs


FIXTURES_A: list[Fixture] = [
    (
        "code_refactor",
        AnalyzerInput([user("Please refactor this function to be shorter")]),
        case(task="code", tools=False, vision=False, json=False, agentic=False),
    ),
    (
        "code_fence_python",
        AnalyzerInput([user("Why is this slow?\n```python\nfor i in range(10):\n    pass\n```")]),
        case(task="code", languages=("python",)),
    ),
    (
        "code_fence_alias_ts",
        AnalyzerInput([user("fix:\n```ts\nconst a: number = 'x'\n```")]),
        case(task="code", languages=("typescript",)),
    ),
    (
        "code_two_languages",
        AnalyzerInput([user("```rs\nfn main(){}\n```\nand\n```sh\nls\n```\nimplement a class")]),
        case(task="code", languages=("rust", "shell")),
    ),
    (
        "code_file_path_ext",
        AnalyzerInput([user("implement the function")], file_paths=["C:\\src\\app\\main.go"]),
        case(task="code", languages=("go",)),
    ),
    (
        "code_ext_in_prose",
        AnalyzerInput([user("There is a bug in utils.py and server.ts, fix it")]),
        case(task="code", languages=("python", "typescript")),
    ),
    (
        "code_stacktrace",
        AnalyzerInput([user("Here is the stack trace, find the bug")]),
        case(task="code"),
    ),
    (
        "vision_openai_image_url",
        AnalyzerInput([user([{"type": "text", "text": "what is this"}, IMG])]),
        case(vision=True, task="vision", confidence_ok=True),
    ),
    (
        "vision_anthropic_image",
        AnalyzerInput(
            [user([{"type": "image", "source": {"type": "base64"}}, {"type": "text", "text": "x"}])]
        ),
        case(vision=True, task="vision"),
    ),
    (
        "vision_screenshot_bug",
        AnalyzerInput([user([IMG, {"type": "text", "text": "fix this bug"}])]),
        case(vision=True, task="vision"),
    ),
    (
        "vision_tokens_counted",
        AnalyzerInput([user([IMG])]),
        case(vision=True, min_tokens=800),
    ),
    (
        "agentic_cline_system_prompt",
        AnalyzerInput(
            [{"role": "system", "content": CLINE_SYSTEM}, user("add a --verbose flag to the cli")]
        ),
        case(agentic=True, tools=False),
    ),
    (
        "agentic_tools_array",
        AnalyzerInput([user("read the readme")], tools=[TOOL]),
        case(agentic=True, tools=True),
    ),
    (
        "agentic_anthropic_system_blocks",
        AnalyzerInput([user("hi")], system=[{"type": "text", "text": CLINE_SYSTEM}], tools=[TOOL]),
        case(agentic=True, tools=True),
    ),
    (
        "agentic_system_prompt_only",
        AnalyzerInput([user("hi")], system=CLINE_SYSTEM),
        case(agentic=True, tools=False),
    ),
    (
        "agentic_user_agent_cline",
        AnalyzerInput([user("hello there")], headers={"User-Agent": "Cline/3.2 vscode"}),
        case(agentic=True, tools=False),
    ),
    (
        "agentic_user_agent_lowercase_header",
        AnalyzerInput([user("hello there")], headers={"user-agent": "OpenCode/1.0"}),
        case(agentic=True),
    ),
    (
        "agentic_tool_result_turn",
        AnalyzerInput(
            [
                user("list files"),
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "1", "function": {"name": "ls", "arguments": "{}"}}],
                },
                {"role": "tool", "content": "a.py\nb.py"},
                user("now open a.py"),
            ],
            tools=[TOOL],
        ),
        case(agentic=True, tools=True),
    ),
    (
        "not_agentic_plain_user_agent",
        AnalyzerInput([user("hello there")], headers={"User-Agent": "python-httpx/0.28"}),
        case(agentic=False),
    ),
]
````

## tests/analyzer_fixtures_b.py

````python
"""Fixture prompts for HDW-106, part B: JSON, chat, reasoning, duck, sizing."""

from __future__ import annotations

from headwaters.core.analyzer import AnalyzerInput
from tests.analyzer_fixtures_a import Fixture, case, user

FIXTURES_B: list[Fixture] = [
    (
        "json_object_format",
        AnalyzerInput(
            [user("Extract the name and date from: 'Bob, 2026-01-02'")],
            response_format={"type": "json_object"},
        ),
        case(json=True, task="extract"),
    ),
    (
        "json_schema_format",
        AnalyzerInput(
            [user("parse this invoice")],
            response_format={"type": "json_schema", "json_schema": {"name": "inv", "schema": {}}},
        ),
        case(json=True, task="extract"),
    ),
    (
        "json_text_format_is_not_json",
        AnalyzerInput([user("hello")], response_format={"type": "text"}),
        case(json=False),
    ),
    (
        "json_extract_no_format",
        AnalyzerInput([user("Extract all emails from this text and parse them")]),
        case(json=False, task="extract"),
    ),
    (
        "chat_greeting",
        AnalyzerInput([user("hi, how are you today?")]),
        case(
            task="chat",
            agentic=False,
            tools=False,
            vision=False,
            json=False,
            risk="low",
            complexity=1,
            confidence_ok=True,
        ),
    ),
    (
        "chat_summarize",
        AnalyzerInput([user("Summarize this article in three bullets: lorem ipsum")]),
        case(task="summarize"),
    ),
    (
        "chat_creative",
        AnalyzerInput([user("write a poem about autumn")]),
        case(task="creative"),
    ),
    (
        "chat_long_vague_is_ambiguous",
        AnalyzerInput([user("I was thinking about stuff and things around the house. " * 40)]),
        case(task="chat", confidence_ok=False),
    ),
    (
        "reasoning_proof",
        AnalyzerInput([user("Prove that the square root of 2 is irrational")]),
        case(task="reasoning", reasoning_hint=True),
    ),
    (
        "reasoning_architecture",
        AnalyzerInput([user("Design an architecture for a multi-tenant billing system")]),
        case(reasoning_hint=True, min_complexity=2),
    ),
    (
        "reasoning_security_audit",
        AnalyzerInput([user("do a security audit of our login flow")]),
        case(reasoning_hint=True, risk="medium"),
    ),
    (
        "risk_high",
        AnalyzerInput([user("delete the production credentials table in the migration")]),
        case(risk="high"),
    ),
    (
        "duck_help_me_think",
        AnalyzerInput([user("help me think through why my cache keeps going stale")]),
        case(duck_hint=True),
    ),
    (
        "duck_why_does",
        AnalyzerInput([user("why does my loop print twice?")]),
        case(duck_hint=True, reasoning_hint=False),
    ),
    (
        "duck_explain_my_bug",
        AnalyzerInput([user("explain my bug:\n```js\nlet x = [] + {}\n```")]),
        case(duck_hint=True, languages=("javascript",), task="code"),
    ),
    (
        "no_duck_plain_task",
        AnalyzerInput([user("write a function that adds two numbers")]),
        case(duck_hint=False),
    ),
    (
        "min_ctx_includes_max_output",
        AnalyzerInput([user("hello")], max_output_tokens=4000),
        case(min_ctx_at_least=4000),
    ),
    (
        "huge_prompt_complexity_up",
        AnalyzerInput([user("lorem ipsum dolor " * 3000)]),
        case(min_complexity=3),
    ),
]
````

## tests/contracts/__init__.py

````python

````

## tests/contracts/harness.py

````python
"""Contract harness: run any plugin implementation against its interface.

Usage from a test::

    from tests.contracts.harness import run_contract
    run_contract("VerifierPlugin", MyVerifier())
"""

from __future__ import annotations

from collections.abc import Callable

from headwaters.core.plugins import (
    CURRENT_API_VERSION,
    INTERFACES,
    SUPPORTED_API_VERSIONS,
    check_plugin,
)


def _surface(p: object) -> None:
    assert isinstance(p.surface_id, str)  # type: ignore[attr-defined]
    assert p.surface_id  # type: ignore[attr-defined]
    p.start()  # type: ignore[attr-defined]
    p.stop()  # type: ignore[attr-defined]


def _verifier(p: object) -> None:
    result = p.verify("print('hello')")  # type: ignore[attr-defined]
    assert isinstance(result, list)
    assert all(isinstance(item, str) for item in result)


def _context(p: object) -> None:
    result = p.build_context("find the router", 256)  # type: ignore[attr-defined]
    assert isinstance(result, str)


def _bridge(p: object) -> None:
    assert isinstance(p.host_id, str)  # type: ignore[attr-defined]
    assert p.host_id  # type: ignore[attr-defined]
    assert isinstance(p.supports(p.host_id), bool)  # type: ignore[attr-defined]


def _dashboard(p: object) -> None:
    assert isinstance(p.render_summary(), dict)  # type: ignore[attr-defined]


def _ledger(p: object) -> None:
    # Decision metadata only: never prompts, outputs or keys.
    assert p.record({"route": "local", "wall": None}) is None  # type: ignore[attr-defined]


def _classifier(p: object) -> None:
    scores = p.classify({"length": 1.0})  # type: ignore[attr-defined]
    assert isinstance(scores, dict)
    assert all(isinstance(k, str) and isinstance(v, float) for k, v in scores.items())


def _escalation(p: object) -> None:
    assert isinstance(p.should_escalate(1, ["syntax error"]), bool)  # type: ignore[attr-defined]


BEHAVIOUR: dict[str, Callable[[object], None]] = {
    "SurfacePlugin": _surface,
    "VerifierPlugin": _verifier,
    "ContextPlugin": _context,
    "BridgePlugin": _bridge,
    "DashboardPlugin": _dashboard,
    "LedgerPlugin": _ledger,
    "ClassifierLayerPlugin": _classifier,
    "EscalationPlugin": _escalation,
}


def run_contract(interface: str, plugin: object) -> None:
    """Assert that ``plugin`` satisfies ``interface``: structure, api_version and behaviour."""
    assert interface in INTERFACES, f"unknown interface {interface}"
    check_plugin(interface, plugin)
    assert plugin.api_version in SUPPORTED_API_VERSIONS  # type: ignore[attr-defined]
    assert CURRENT_API_VERSION in SUPPORTED_API_VERSIONS
    BEHAVIOUR[interface](plugin)
````

## tests/contracts/test_example_contracts.py

````python
"""Run the contract harness against every plugin registered by discovered features."""

from __future__ import annotations

import pytest

from headwaters.core.features import FeatureRegistry, discover_features
from tests.contracts.harness import run_contract

_registry = FeatureRegistry()
discover_features(_registry)
_CASES = [
    (iface, plugin)
    for spec in _registry.all()
    for iface in spec.interfaces
    for plugin in _registry.plugins_for(iface)
]


@pytest.mark.parametrize(("interface", "plugin"), _CASES)
def test_registered_plugin_meets_contract(interface: str, plugin: object) -> None:
    run_contract(interface, plugin)
````

## tests/test_analyzer_chain.py

````python
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
    assert resolve_classifier_model(catalog, LocalTier()) == "example-local-vision:7b"
    assert resolve_classifier_model(catalog, LocalTier(profile="desktop_16gb")) is None
    explicit = LocalTier(classifier=ClassifierRules(model="my-tiny:1b"))
    assert resolve_classifier_model(catalog, explicit) == "my-tiny:1b"


def test_build_chain_wires_layer3_and_can_disable(catalog: Catalog) -> None:
    chain = build_chain(catalog, LocalTier(), client=FakeClient(answer()))
    assert [layer.name for layer in chain.layers] == ["tiny_llm"]
    assert not build_chain(catalog, LocalTier(), enable_layer3=False).layers
    assert not build_chain(catalog, LocalTier(profile="desktop_16gb")).layers


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
````

## tests/test_analyzer_classifier.py

````python
"""HDW-107: analyzer layer 3 (tiny CPU classifier): payload, cache, fallback, parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.analyzer import Analyzer, AnalyzerChain, AnalyzerInput, TinyLlmClassifier
from headwaters.core.analyzer.classifier import parse_label, prompt_text
from headwaters.core.analyzer.ollama_client import ClassifierUnavailableError
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import Catalog
from headwaters.core.rules import ClassifierRules

REPO_ROOT = Path(__file__).resolve().parent.parent
VAGUE = "I was thinking about stuff and things around the house. " * 40
CLEAR = "write a poem about autumn"


class FakeClient:
    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.payloads: list[Mapping[str, Any]] = []
        self.timeouts: list[float] = []

    def chat(self, payload: Mapping[str, Any], timeout_s: float) -> Mapping[str, Any]:
        self.payloads.append(payload)
        self.timeouts.append(timeout_s)
        if self.error is not None:
            raise self.error
        assert isinstance(self.reply, dict)
        return self.reply


def answer(task: str = "code", complexity: int = 4, risk: str = "medium") -> dict[str, Any]:
    body = {"task": task, "complexity": complexity, "risk": risk}
    return {"message": {"role": "assistant", "content": json.dumps(body)}}


def msg(text: str) -> AnalyzerInput:
    return AnalyzerInput([{"role": "user", "content": text}])


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


@pytest.fixture(scope="module")
def base(catalog: Catalog) -> Analyzer:
    return Analyzer.from_catalog(catalog)


def test_no_gpu_option_and_schema_sent() -> None:
    client = FakeClient(answer())
    TinyLlmClassifier("tiny:1b", client=client).classify("hello")
    payload = client.payloads[0]
    assert payload["options"]["num_gpu"] == 0
    assert payload["stream"] is False
    assert payload["format"]["required"] == ["task", "complexity", "risk"]
    assert payload["format"]["properties"]["complexity"]["maximum"] == 5
    assert payload["model"] == "tiny:1b"


def test_default_timeout_is_two_seconds_and_cpu_only() -> None:
    client = FakeClient(answer())
    TinyLlmClassifier("m", client=client).classify("x")
    assert client.timeouts == [2]
    assert ClassifierRules().num_gpu == 0


def test_cache_hit_skips_second_call() -> None:
    client = FakeClient(answer("extract", 2, "low"))
    layer = TinyLlmClassifier("m", client=client)
    first = layer.classify("same prompt")
    second = layer.classify("same prompt")
    assert first == second
    assert layer.calls == 1
    assert len(client.payloads) == 1
    layer.classify("other prompt")
    assert layer.calls == 2


def test_cache_key_is_sha256_and_model_scoped() -> None:
    a = TinyLlmClassifier("m1", client=FakeClient(answer()))
    b = TinyLlmClassifier("m2", client=FakeClient(answer()))
    assert len(a.cache_key("secret prompt")) == 64
    assert a.cache_key("p") != b.cache_key("p")
    assert "secret" not in a.cache_key("secret prompt")


def test_cache_evicts_oldest() -> None:
    layer = TinyLlmClassifier("m", client=FakeClient(answer()), cache_size=2)
    for text in ("a", "b", "c"):
        layer.classify(text)
    layer.classify("c")
    assert layer.calls == 3
    layer.classify("a")
    assert layer.calls == 4


ERRORS = [TimeoutError(), OSError("down"), ClassifierUnavailableError("x")]


@pytest.mark.parametrize("error", ERRORS)
def test_timeout_and_errors_fall_back_to_layer1(base: Analyzer, error: Exception) -> None:
    layer = TinyLlmClassifier("m", client=FakeClient(error=error))
    expected = base.analyze(msg(VAGUE))
    assert expected.ambiguous
    assert AnalyzerChain(base, layer3=layer).analyze(msg(VAGUE)) == expected
    assert layer.calls == 1


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1]",
        json.dumps({"task": "bogus", "complexity": 2, "risk": "low"}),
        json.dumps({"task": "code", "complexity": "2", "risk": "low"}),
        json.dumps({"task": "code", "complexity": True, "risk": "low"}),
        json.dumps({"task": "code", "complexity": 2, "risk": "x"}),
    ],
)
def test_bad_replies_fall_back_and_are_not_cached(base: Analyzer, content: str) -> None:
    reply = {"message": {"content": content}}
    layer = TinyLlmClassifier("m", client=FakeClient(reply))
    assert parse_label(reply) is None
    assert AnalyzerChain(base, layer3=layer).analyze(msg(VAGUE)) == base.analyze(msg(VAGUE))
    layer.classify("p")
    layer.classify("p")
    assert layer.calls == 3  # failures are retried, never cached


def test_reply_without_message_is_rejected() -> None:
    assert parse_label({}) is None
    assert parse_label({"message": {"content": 5}}) is None


def test_complexity_is_clamped() -> None:
    label = parse_label(answer("code", 9, "low"))
    assert label is not None
    assert label.complexity == 5


def test_runs_only_when_ambiguous(base: Analyzer) -> None:
    client = FakeClient(answer("creative", 3, "low"))
    chain = AnalyzerChain(base, layer3=TinyLlmClassifier("m", client=client))
    assert chain.analyze(msg(CLEAR)) == base.analyze(msg(CLEAR))
    assert client.payloads == []
    refined = chain.analyze(msg(VAGUE))
    assert len(client.payloads) == 1
    assert refined.source == "classifier"
    soft = refined.soft
    assert (soft.task, soft.complexity, soft.risk) == ("creative", 3, "low")
    assert refined.confidence >= chain.threshold
    assert not refined.ambiguous
    assert refined.hard == base.analyze(msg(VAGUE)).hard  # hard needs never change


def test_vision_task_is_kept_for_image_requests(base: Analyzer) -> None:
    image = {"type": "image_url", "image_url": {"url": "data:,"}}
    inp = AnalyzerInput([{"role": "user", "content": [image, {"type": "text", "text": VAGUE}]}])
    current = replace(base.analyze(inp), confidence=0.1)
    layer = TinyLlmClassifier("m", client=FakeClient(answer("chat", 2, "low")))
    refined = layer.refine(inp, current)
    assert refined is not None
    assert refined.soft.task == "vision"
    assert refined.hard.vision


def test_empty_prompt_does_not_call_model(base: Analyzer) -> None:
    client = FakeClient(answer())
    layer = TinyLlmClassifier("m", client=client)
    assert layer.refine(AnalyzerInput([]), base.analyze(AnalyzerInput([]))) is None
    assert client.payloads == []


def test_long_prompt_is_clipped_before_sending() -> None:
    assert len(prompt_text(msg("x" * 50000))) < 2100
````

## tests/test_analyzer_requirements.py

````python
"""HDW-106: analyzer layer 1. Fixture prompts produce the expected requirements."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.analyzer import Analyzer, AnalyzerInput, Requirements
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import AnalyzerHints, Catalog
from tests.analyzer_fixtures_a import CLINE_SYSTEM, FIXTURES_A, TOOL, user
from tests.analyzer_fixtures_b import FIXTURES_B

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = [*FIXTURES_A, *FIXTURES_B]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


@pytest.fixture(scope="module")
def analyzer(catalog: Catalog) -> Analyzer:
    return Analyzer.from_catalog(catalog)


def check(expected: dict[str, Any], req: Requirements, threshold: float) -> None:
    hard, soft = req.hard, req.soft
    for key, want in expected.items():
        if key in ("tools", "vision", "json"):
            assert getattr(hard, key) is want, key
        elif key == "min_ctx_at_least":
            assert hard.min_ctx >= want, key
        elif key == "min_tokens":
            assert req.est_tokens >= want, key
        elif key == "min_complexity":
            assert soft.complexity >= want, key
        elif key == "confidence_ok":
            assert (req.confidence >= threshold) is want, (key, req.confidence)
        else:
            assert getattr(soft, key) == want, (key, getattr(soft, key))


def test_fixture_count_meets_acceptance() -> None:
    assert len(FIXTURES) >= 30
    assert len({name for name, _, _ in FIXTURES}) == len(FIXTURES)


@pytest.mark.parametrize(("name", "inp", "expected"), FIXTURES, ids=[f[0] for f in FIXTURES])
def test_fixture(
    analyzer: Analyzer, name: str, inp: AnalyzerInput, expected: dict[str, Any]
) -> None:
    req = analyzer.analyze(inp)
    assert req.source == "deterministic"
    assert 1 <= req.soft.complexity <= 5
    check(expected, req, analyzer.hints.confidence_threshold)
    assert req.ambiguous == (req.confidence < analyzer.hints.confidence_threshold)


def test_token_estimate_uses_code_and_prose_rates() -> None:
    analyzer = Analyzer(AnalyzerHints())
    assert analyzer.estimate_tokens(["a" * 400], 0, 0) == 100
    fenced = "```py\n" + "x" * 350 + "```"
    # 350 code chars / 3.5 = 100; the 9 fence-marker chars count as prose: 9 / 4 -> ceil 103
    assert analyzer.estimate_tokens([fenced], 0, 0) == 103


def test_injected_token_counter_wins() -> None:
    analyzer = Analyzer(AnalyzerHints(), token_counter=lambda _text: 7)
    req = analyzer.analyze(AnalyzerInput([user("anything at all")]))
    assert req.est_tokens == 7


def test_empty_hints_are_safe() -> None:
    req = Analyzer(AnalyzerHints()).analyze(AnalyzerInput([user("Prove x")]))
    assert not req.soft.reasoning_hint
    assert req.soft.task == "chat"


def test_malformed_messages_do_not_crash(analyzer: Analyzer) -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user"},
        {"role": "user", "content": None},
        {"role": "user", "content": [42, None, {"type": "weird"}, "plain text"]},
        {"role": "assistant", "tool_calls": "oops"},
        {"role": "assistant", "tool_calls": [{"function": {"arguments": {"a": 1}}}, "bad"]},
    ]
    req = analyzer.analyze(AnalyzerInput(messages))
    assert req.hard.vision is False


def test_nested_tool_result_image_counts(analyzer: Analyzer) -> None:
    messages = [user([{"type": "tool_result", "content": [{"type": "image", "source": {}}]}])]
    assert analyzer.analyze(AnalyzerInput(messages)).hard.vision is True


def test_anthropic_tool_use_counts_as_code_tokens(analyzer: Analyzer) -> None:
    block = {"type": "tool_use", "input": {"content": "x" * 700}}
    req = analyzer.analyze(AnalyzerInput([{"role": "assistant", "content": [block]}]))
    assert req.est_tokens >= 200


def test_response_format_string_and_schema_key(analyzer: Analyzer) -> None:
    assert analyzer.analyze(AnalyzerInput([user("x")], response_format="json")).hard.json
    schema_only = AnalyzerInput([user("x")], response_format={"schema": {"a": 1}})
    assert analyzer.analyze(schema_only).hard.json


def test_does_not_mutate_input(analyzer: Analyzer) -> None:
    messages = [user("```python\nx=1\n```")]
    before = repr(messages)
    analyzer.analyze(AnalyzerInput(messages))
    assert repr(messages) == before


def test_long_user_message_scans_head_and_tail(analyzer: Analyzer) -> None:
    text = "prove this\n" + "filler " * 5000 + "\nthanks"
    assert analyzer.analyze(AnalyzerInput([user(text)])).soft.reasoning_hint


def test_under_five_ms_for_typical_request(analyzer: Analyzer) -> None:
    inp = AnalyzerInput(
        [{"role": "system", "content": CLINE_SYSTEM}, user("refactor main.py\n```py\nx=1\n```")],
        tools=[TOOL],
        headers={"User-Agent": "Cline"},
    )
    analyzer.analyze(inp)  # warm up
    runs = 20
    start = time.perf_counter()
    for _ in range(runs):
        analyzer.analyze(inp)
    per_call_ms = (time.perf_counter() - start) / runs * 1000
    assert per_call_ms < 5, per_call_ms


def test_seed_hints_are_data_not_code(catalog: Catalog) -> None:
    hints = catalog.analyzer_hints
    assert hints.agent_user_agents
    assert hints.duck_keywords
    assert hints.extension_languages[".py"] == "python"


@pytest.mark.parametrize(
    "bad",
    [
        {"reasoning_keywords": ["(unclosed"]},
        {"task_keywords": {"nonsense": ["x"]}},
        {"task_keywords": {"code": ["("]}},
        {"extension_languages": {"py": "python"}},
        {"confidence_threshold": 2},
    ],
)
def test_hint_validation_rejects_bad_data(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"."):
        AnalyzerHints.model_validate(bad)
````

## tests/test_catalog.py

````python
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.catalog.loader import (
    SCHEMA_RELATIVE_PATH,
    SEED_RELATIVE_PATH,
    CatalogIndex,
    load_catalog,
    main,
    schema_json,
)
from headwaters.core.rules_io import ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent


def seed_data() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((REPO_ROOT / SEED_RELATIVE_PATH).read_text("utf-8"))
    return data


def write(tmp_path: Path, data: Any) -> Path:
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def test_seed_validates_and_is_marked_verify() -> None:
    catalog = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
    assert catalog.models
    assert all(m.verify for m in catalog.models)
    assert all(p.verify for p in catalog.wall_patterns)
    assert {"laptop_12gb", "desktop_16gb"} <= set(catalog.gpu_profiles)


def test_committed_schema_is_current() -> None:
    committed = (REPO_ROOT / SCHEMA_RELATIVE_PATH).read_bytes().decode("utf-8")
    assert committed.replace("\r\n", "\n") == schema_json()


def test_schema_cli_write_and_check(tmp_path: Path) -> None:
    target = tmp_path / "s.json"
    assert main(["--check", str(target)]) == 1
    assert main(["--write", str(target)]) == 0
    assert main(["--check", str(target)]) == 0


def test_schema_cli_prints(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert capsys.readouterr().out == schema_json()


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read file"):
        load_catalog(tmp_path / "nope.json")


def test_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON syntax error at line 1"):
        load_catalog(path)


def test_top_level_must_be_object(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="top level must be an object"):
        load_catalog(write(tmp_path, []))


def _mutated(mutate: Any) -> dict[str, Any]:
    data = copy.deepcopy(seed_data())
    mutate(data)
    return data


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["models"][0].update(typo=1), "models.0.typo"),
        (lambda d: d["models"][0]["scores"].update(code=1.5), "models.0.scores.code"),
        (lambda d: d["models"][0].update(price_date=None), "prices require price_date"),
        (lambda d: d["models"][0].update(deprecated_by="ghost"), "deprecated_by 'ghost'"),
        (lambda d: d["models"][1].update(id=d["models"][0]["id"]), "is used by"),
        (lambda d: d["wall_patterns"][0].update(body_regex="(["), "invalid regular expression"),
        (
            lambda d: d["wall_patterns"][1].update(status_codes=[], body_regex=None),
            "needs status_codes or body_regex",
        ),
        (lambda d: d["secret_patterns"][0].update(regex="("), "invalid regular expression"),
        (
            lambda d: d["gpu_profiles"]["laptop_12gb"]["recommended"].update(general=["ghost"]),
            "unknown 'ghost'",
        ),
        (lambda d: d["models"][2]["expected_tps"].update(laptop_12gb=0), "must be positive"),
        (lambda d: d.pop("unknown_model_defaults"), "unknown_model_defaults"),
    ],
)
def test_bad_catalogs_are_rejected_clearly(tmp_path: Path, mutate: Any, message: str) -> None:
    with pytest.raises(ConfigError) as info:
        load_catalog(write(tmp_path, _mutated(mutate)))
    assert message in str(info.value)


def test_known_model_resolves_by_id_and_alias() -> None:
    index = CatalogIndex(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH))
    by_id = index.resolve("example-local:14b")
    assert by_id.unscored is False
    assert index.resolve("EXAMPLE-LOCAL-14B") is by_id
    assert index.find("nothing") is None


def test_unknown_model_gets_conservative_defaults() -> None:
    catalog = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
    index = CatalogIndex(catalog)
    model = index.resolve("mystery/model-9", provider="ollama")
    assert model.unscored is True
    assert model.provider == "ollama"
    assert model.tools_ok is False
    assert model.json_ok is False
    assert model.ctx == catalog.unknown_model_defaults.ctx
    known_max = max(
        v for m in catalog.models for v in m.scores.model_dump().values() if v is not None
    )
    unknown_max = max(v for v in model.scores.model_dump().values() if v is not None)
    assert unknown_max < known_max
````

## tests/test_catalog_updater.py

````python
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
````

## tests/test_cli.py

````python
from typer.testing import CliRunner

import headwaters
from headwaters.cli.main import app

runner = CliRunner()


def test_help_prints_stub() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Headwaters" in result.output


def test_features_lists_legend_and_hides_example() -> None:
    result = runner.invoke(app, ["features"])
    assert result.exit_code == 0
    assert "★ = free during beta; may become part of Headwaters Pro at 1.0" in result.output
    assert "_example" not in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == headwaters.__version__
````

## tests/test_cli_misc.py

````python
"""HDW-118: ``version``, ``features``, ``catalog`` and the shared JSON/exit-code conventions."""

from __future__ import annotations

from pathlib import Path

import pytest

import headwaters
from headwaters.core.features import STAR_LEGEND
from tests.cli_support import home, invoke, invoke_json, write_rules


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


def test_version_command_and_json(cli_home: Path) -> None:
    assert invoke("version").stdout.strip() == headwaters.__version__
    _, data = invoke_json("version")
    assert data == {"ok": True, "version": headwaters.__version__}


def test_features_lists_core_and_star_with_legend(cli_home: Path) -> None:
    result = invoke("features")
    assert result.exit_code == 0
    assert "Core (free forever)" in result.stdout
    assert "Free and local scopes, Private mode" in result.stdout
    assert STAR_LEGEND in result.stdout
    assert "_example" not in result.stdout  # internal example feature stays hidden


def test_features_json_separates_core_from_star(cli_home: Path) -> None:
    _, data = invoke_json("features")
    assert data["legend"] == STAR_LEGEND
    assert {f["id"] for f in data["core"]} >= {"gateway", "mcp", "failover", "verify_basic"}
    assert all(f["enabled"] for f in data["core"])  # core features cannot be switched off
    assert data["star"] == []  # no real star feature is installed yet


def test_features_respects_user_flags(cli_home: Path) -> None:
    # The internal example feature is hidden, so check the flag through the JSON of a real run:
    # switching a feature off in rules.yaml must be reflected, never crash the command.
    write_rules(cli_home, "features:\n  _example: false\n  something_else: false\n")
    result, data = invoke_json("features")
    assert result.exit_code == 0
    assert data["star"] == []
    assert all(f["enabled"] for f in data["core"])


def test_catalog_status_plain_and_json(cli_home: Path) -> None:
    out = invoke("catalog", "status").stdout
    assert "Active catalog: seed" in out
    assert "Feed URL: (not configured)" in out
    _, data = invoke_json("catalog", "status")
    assert data["source"] == "seed"
    assert data["url"] is None
    assert data["updates"] == "catalog.url is not configured"


def test_catalog_update_blocked_is_reported_as_json(cli_home: Path) -> None:
    write_rules(cli_home, "airplane: true\n")
    result, data = invoke_json("catalog", "update")
    assert result.exit_code == 0
    assert data["ok"] is True
    assert "airplane" in data["message"]


def test_invalid_rules_is_exit_2_for_every_command(cli_home: Path) -> None:
    write_rules(cli_home, "airplane: maybe\n")
    for args in (
        ["status"],
        ["models", "list"],
        ["catalog", "status"],
        ["features"],
        ["pick", "x"],
    ):
        result = invoke(*args)
        assert result.exit_code == 2, args
        assert "airplane" in result.output


def test_help_lists_every_command(cli_home: Path) -> None:
    out = invoke("--help").stdout
    for name in ("version", "pick", "status", "models", "scope", "private", "features", "catalog"):
        assert name in out


def test_json_output_is_pure_json_on_stdout(cli_home: Path) -> None:
    import json

    for args in (["status"], ["models", "list"], ["features"], ["scope"], ["private"]):
        result = invoke(*args, "--json")
        assert result.exit_code == 0, args
        json.loads(result.stdout)  # nothing but the document
````

## tests/test_cli_models.py

````python
"""HDW-118: ``headwaters models list`` shows fit and num_ctx per model; refresh keeps working."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from headwaters.core.state import open_database
from tests.cli_support import add_model, gpu_state, home, invoke, invoke_json, seed_models


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home(tmp_path, monkeypatch, gpu_state(16))
    seed_models(tmp_path)
    return tmp_path


def _by_id(data: dict[str, Any]) -> dict[str, Any]:
    return {m["id"]: m for m in data["models"]}


def test_list_shows_fit_and_num_ctx_for_local_models(cli_home: Path) -> None:
    result, data = invoke_json("models", "list")
    assert result.exit_code == 0
    local = _by_id(data)["qwen:14b"]
    assert local["fit"] == {"status": "fits", "num_ctx": 32768, "reason": "ok"}
    assert data["gpu_budget_gb"] == 14.0  # 16 GB card minus the 2 GB display reserve
    out = invoke("models", "list").stdout
    assert "fits" in out
    assert "32768" in out
    assert "GPU budget for local models: 14.0 GB" in out


def test_cloud_models_have_no_fit(cli_home: Path) -> None:
    _, data = invoke_json("models", "list")
    models = _by_id(data)
    assert models["vendor/free:free"]["fit"] is None
    assert models["vendor/big"]["fit"] is None


def test_small_gpu_changes_the_fit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home(tmp_path, monkeypatch, gpu_state(8))  # 6 GB budget: 9 GB of weights only fit offloaded
    seed_models(tmp_path)
    _, data = invoke_json("models", "list")
    assert _by_id(data)["qwen:14b"]["fit"]["status"] == "offload"
    add_model(open_database(), "huge:70b", "local", 90.0, size_gb=40.0)
    _, big = invoke_json("models", "list")
    fit = _by_id(big)["huge:70b"]["fit"]
    assert fit["status"] == "no_fit"
    assert fit["num_ctx"] is None
    assert "no_fit (" in invoke("models", "list").stdout


def test_tokens_option_changes_the_computed_context(cli_home: Path) -> None:
    _, small = invoke_json("models", "list", "--tokens", "1000")
    _, large = invoke_json("models", "list", "--tokens", "20000")
    assert _by_id(small)["qwen:14b"]["fit"]["num_ctx"] == 32768
    assert _by_id(large)["qwen:14b"]["fit"]["num_ctx"] == 32768
    _, too_big = invoke_json("models", "list", "--tokens", "200000")
    assert _by_id(too_big)["qwen:14b"]["fit"]["status"] == "no_fit"


def test_tier_filter(cli_home: Path) -> None:
    _, data = invoke_json("models", "list", "--tier", "local")
    assert list(_by_id(data)) == ["qwen:14b"]
    result = invoke("models", "list", "--tier", "nonsense")
    assert result.exit_code == 2
    assert "--tier must be one of" in result.output


def test_flags_are_shown(cli_home: Path) -> None:
    add_model(open_database(), "old:7b", "local", 40.0, unscored=True, deprecated_by="new:7b")
    out = invoke("models", "list").stdout
    assert "warm" in out
    assert "unscored" in out
    assert "deprecated, use new:7b" in out


def test_no_gpu_says_fit_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from headwaters.cli import runtime

    home(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "hardware_state", lambda rules: None)
    seed_models(tmp_path)
    out = invoke("models", "list").stdout
    assert "unknown (no GPU detected)" in out
    assert "No NVIDIA GPU detected" in out


def test_empty_list_points_to_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home(tmp_path, monkeypatch)
    assert "headwaters models refresh" in invoke("models", "list").stdout
    _, data = invoke_json("models", "list")
    assert data["models"] == []
````

## tests/test_cli_scope.py

````python
"""HDW-118: ``headwaters scope`` and ``private`` (and the rules.yaml edit under them)."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.rules_io import load_rules
from tests.cli_support import home, invoke, invoke_json, write_rules


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


def _rules_file(root: Path) -> Path:
    return root / "config" / "rules.yaml"


def test_scope_shows_default_and_effective(cli_home: Path) -> None:
    result, data = invoke_json("scope")
    assert result.exit_code == 0
    assert data["default_scope"] == "auto"
    # Without the paid-scopes feature, auto behaves like free (paid tiers are excluded).
    assert data["effective"]["scope"] == "free"
    assert data["changed"] is False
    assert "Default scope: auto" in invoke("scope").stdout


@pytest.mark.parametrize("scope", ["auto", "free", "local", "cloud"])
def test_scope_sets_each_scope(cli_home: Path, scope: str) -> None:
    result, data = invoke_json("scope", scope)
    assert result.exit_code == 0, result.output
    assert data["default_scope"] == scope
    assert load_rules(_rules_file(cli_home)).default_scope == scope


def test_scope_reports_change_and_no_change(cli_home: Path) -> None:
    assert "auto -> local" in invoke("scope", "local").stdout
    assert "already local" in invoke("scope", "local").stdout
    _, data = invoke_json("scope", "free")
    assert (data["previous"], data["changed"]) == ("local", True)


def test_scope_rejects_unknown_value_without_touching_the_file(cli_home: Path) -> None:
    invoke("scope", "free")
    before = _rules_file(cli_home).read_bytes()
    assert invoke("scope", "everywhere").exit_code == 2
    assert _rules_file(cli_home).read_bytes() == before


def test_scope_edit_keeps_comments_and_other_settings(cli_home: Path) -> None:
    write_rules(
        cli_home,
        "# my notes\nversion: 1\ndefault_scope: auto  # keep me\nstrategy: quality_first\n",
    )
    assert invoke("scope", "local").exit_code == 0
    text = _rules_file(cli_home).read_text(encoding="utf-8")
    assert "# my notes" in text
    assert "default_scope: local  # keep me" in text
    assert "strategy: quality_first" in text


def test_scope_edit_keeps_crlf_line_endings(cli_home: Path) -> None:
    path = _rules_file(cli_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"version: 1\r\ndefault_scope: auto\r\nprivate_lock: false\r\n")
    assert invoke("scope", "free").exit_code == 0
    raw = path.read_bytes()
    assert b"default_scope: free\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_scope_adds_the_key_when_the_file_does_not_have_it(cli_home: Path) -> None:
    write_rules(cli_home, "version: 1\n")
    assert invoke("scope", "local").exit_code == 0
    assert load_rules(_rules_file(cli_home)).default_scope == "local"


def test_invalid_rules_file_is_reported_and_left_alone(cli_home: Path) -> None:
    write_rules(cli_home, "default_scope: auto\nairplane: maybe\n")
    before = _rules_file(cli_home).read_bytes()
    result = invoke("scope", "local")
    assert result.exit_code == 2
    assert "airplane" in result.output
    assert _rules_file(cli_home).read_bytes() == before


def test_private_on_off_and_effective_scope(cli_home: Path) -> None:
    result, data = invoke_json("private", "on")
    assert result.exit_code == 0
    assert data["private_lock"] is True
    assert data["effective"]["tiers"] == ["local"]
    assert "private_lock" in data["effective"]["locks"]
    assert load_rules(_rules_file(cli_home)).private_lock is True
    off, after = invoke_json("private", "off")
    assert off.exit_code == 0
    assert after["private_lock"] is False
    assert after["effective"]["locks"] == []


def test_private_text_output_says_nothing_leaves(cli_home: Path) -> None:
    out = invoke("private", "on").stdout
    assert "Private lock: off -> on" in out
    assert "no cloud client is created" in out
    assert "already on" in invoke("private", "on").stdout
    assert "Private lock: on" in invoke("private").stdout


def test_private_rejects_other_words(cli_home: Path) -> None:
    assert invoke("private", "maybe").exit_code == 2


def test_lock_narrows_a_broad_default_scope_to_local(cli_home: Path) -> None:
    invoke("private", "on")
    out = invoke("scope", "auto").stdout
    assert "tighter setting always wins" in out
    _, status = invoke_json("status")
    assert status["effective"]["tiers"] == ["local"]
    assert status["default_scope"] == "auto"


def test_lock_is_never_loosened_by_a_cloud_default_scope(cli_home: Path) -> None:
    # Scopes that cannot overlap are an error with a fix, never a silent loosening of the lock.
    invoke("private", "on")
    result, data = invoke_json("scope", "cloud")
    assert result.exit_code == 0  # the setting itself was saved
    assert data["effective"]["scope"] is None
    assert data["effective"]["error"]["code"] == "private_lock_conflict"
    assert "headwaters private off" in invoke("scope").stdout
    _, status = invoke_json("status")
    assert status["effective"] is None
    assert status["private_lock"] is True
````

## tests/test_cli_status.py

````python
"""HDW-118: ``headwaters status``: scope, locks, project rule, walls with ETAs, warm model."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.state import open_database
from headwaters.core.state_limits import record_wall
from tests.cli_support import home, invoke, invoke_json, seed_models, write_rules

FAR_FUTURE = 4_102_444_800.0  # 2100-01-01


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


def test_project_rule_tightens_scope(cli_home: Path) -> None:
    (cli_home / ".headwaters.yaml").write_text("privacy: local\n", encoding="utf-8")
    _, status = invoke_json("status")
    assert status["project_rule"] == "local"
    assert status["effective"]["tiers"] == ["local"]
    assert "project_rule" in status["locks"]
    assert status["project_file"].endswith(".headwaters.yaml")


def test_invalid_project_file_is_a_config_error(cli_home: Path) -> None:
    (cli_home / ".headwaters.yaml").write_text("privacy: nobody\n", encoding="utf-8")
    result, data = invoke_json("status")
    assert result.exit_code == 2
    assert data["error"]["code"] == "invalid_project_config"


def test_status_text_lists_scope_locks_walls_and_warm_model(cli_home: Path) -> None:
    out = invoke("status").stdout
    for label in ("Default scope", "Effective scope", "Private lock", "Locks in force", "Catalog"):
        assert label in out
    assert "Walls: none" in out
    assert "Warm model: none" in out


def test_status_unsatisfiable_scope_shows_the_fix(cli_home: Path) -> None:
    write_rules(cli_home, "private_lock: true\ndefault_scope: cloud\n")
    out = invoke("status").stdout
    assert "Effective scope" in out
    assert "headwaters private off" in out
    _, data = invoke_json("status")
    assert data["scope_error"]["code"] == "private_lock_conflict"
    assert data["effective"] is None


def test_status_shows_walls_with_eta_and_kind(cli_home: Path) -> None:
    db = open_database()
    record_wall(db, "model:vendor/free:free", "rate_limit", until=FAR_FUTURE)
    record_wall(db, "key:openrouter", "exhausted", until=None)
    _, data = invoke_json("status")
    walls = {w["subject"]: w for w in data["walls"]}
    assert walls["model:vendor/free:free"]["eta_s"] > 0
    assert walls["key:openrouter"]["eta_s"] is None  # waits for a balance check, no reset time
    out = invoke("status").stdout
    assert "rate limited" in out
    assert "out of credit" in out
    assert "after a balance check" in out


def test_status_ignores_walls_that_have_expired(cli_home: Path) -> None:
    record_wall(open_database(), "model:old", "rate_limit", until=1.0)
    _, data = invoke_json("status")
    assert data["walls"] == []


def test_status_shows_the_warm_model(cli_home: Path) -> None:
    seed_models(cli_home)
    _, data = invoke_json("status")
    assert data["warm_models"] == [{"model": "qwen:14b", "vram_gb": 8.0}]
    assert "qwen:14b (8.0 GB on the GPU)" in invoke("status").stdout


def test_status_airplane_mode_is_a_lock(cli_home: Path) -> None:
    write_rules(cli_home, "airplane: true\n")
    _, data = invoke_json("status")
    assert data["airplane"] is True
    assert "airplane" in data["locks"]
````

## tests/test_discovery_merge.py

````python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.merge import CatalogLookup, ModelRecord, merge_models
from headwaters.core.catalog.schema import Catalog
from headwaters.core.catalog.sources import LiveModel
from headwaters.core.rules import Rules

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = 1_000.0


@pytest.fixture
def catalog() -> Catalog:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


def live(model_id: str, **kw: Any) -> LiveModel:
    base: dict[str, Any] = {"id": model_id, "tier": "local", "provider": "ollama"}
    base["source"] = "ollama"
    return LiveModel(**{**base, **kw})


def one(item: LiveModel, catalog: Catalog, rules: Rules) -> ModelRecord:
    """Merge a single live item and return its record (curated cloud entries are ignored)."""
    records = merge_models([item], catalog, rules, NOW)
    return next(r for r in records if r.row.id == item.id)


def rules_with(overrides: dict[str, Any]) -> Rules:
    return Rules.model_validate({"models": {"overrides": overrides}})


def test_alias_and_latest_matching(catalog: Catalog) -> None:
    lookup = CatalogLookup(catalog)
    local = lookup.find(["example-local-14b"])
    assert local is not None
    assert local.id == "example-local:14b"
    upper = lookup.find(["EXAMPLE-LOCAL:14B"])
    assert upper is not None
    assert upper.id == "example-local:14b"
    latest = lookup.find(["example-large:latest"])
    assert latest is not None
    assert latest.id == "example-paid/model-large"
    assert lookup.find(["nothing"]) is None
    assert lookup.get("nothing") is None


def test_known_model_takes_live_facts_and_catalog_scores(catalog: Catalog) -> None:
    item = live("example-local-14b", ctx=16384, tools_ok=False, size_gb=8.5, warm=True)
    record = one(item, catalog, Rules())
    row = record.row
    assert row.id == "example-local-14b"  # the id the live source reported
    assert record.catalog_id == "example-local:14b"
    assert (row.ctx, row.tools_ok, row.size_gb, row.warm) == (16384, False, 8.5, True)
    assert row.json_ok is True  # live said nothing, so the catalog fills the gap
    assert row.arch == {"n_layers": 48, "n_kv_heads": 8, "head_dim": 128}
    assert row.unscored is False
    assert record.seed_scores["code"] == 70.0  # 0.7 on the catalog scale -> 0..100
    assert row.last_seen == NOW


def test_unknown_model_gets_conservative_defaults(catalog: Catalog) -> None:
    record = one(live("mystery:7b", size_gb=4.0), catalog, Rules())
    row = record.row
    assert row.unscored is True
    assert row.tools_ok is False
    assert row.json_ok is False
    assert row.ctx == catalog.unknown_model_defaults.ctx
    assert record.catalog_id is None
    assert max(record.seed_scores.values()) <= 40.0
    assert record.seed_scores["agentic_tools"] == 20.0


def test_deprecation_is_carried(catalog: Catalog) -> None:
    data = json.loads(catalog.model_dump_json())
    data["models"][0]["deprecated_by"] = data["models"][2]["id"]
    deprecated = Catalog.model_validate(data)
    item = live("example-paid/model-large", tier="paid", provider="x", source="openrouter")
    record = one(item, deprecated, Rules())
    assert record.row.deprecated_by == "example-local:14b"


def test_retains_data_comes_from_catalog_or_source(catalog: Catalog) -> None:
    free = live("example-openrouter/model-free:free", tier="free_cloud", source="openrouter")
    unknown = live("opencode/x", tier="free_cloud", source="opencode", retains_data=True)
    records = {r.row.id: r.row for r in merge_models([free, unknown], catalog, Rules(), NOW)}
    assert records["example-openrouter/model-free:free"].retains_data is True  # catalog flag
    assert records["opencode/x"].retains_data is True  # source flag


def test_source_supplied_names_match_catalog(catalog: Catalog) -> None:
    item = live("vendor/odd-id", tier="paid", source="openrouter", match_names=["example-large"])
    record = one(item, catalog, Rules())
    assert record.catalog_id == "example-paid/model-large"
    assert record.row.unscored is False


def test_catalog_only_cloud_entries_are_added_but_local_need_install(catalog: Catalog) -> None:
    records = {r.row.id: r for r in merge_models([], catalog, Rules(), NOW)}
    assert set(records) == {"example-paid/model-large", "example-openrouter/model-free:free"}
    paid = records["example-paid/model-large"].row
    assert (paid.tier, paid.source) == ("paid", "catalog")
    assert records["example-openrouter/model-free:free"].row.tier == "free_cloud"


def test_user_overrides_win_last(catalog: Catalog) -> None:
    rules = rules_with(
        {
            "example-local-14b": {
                "scores": {"code": 95},
                "tools_ok": False,
                "ctx": 4096,
                "tier": "free_cloud",
            }
        }
    )
    item = live("example-local:14b", tools_ok=True, ctx=32768)
    record = one(item, catalog, rules)
    assert (record.row.tools_ok, record.row.ctx, record.row.tier) == (False, 4096, "free_cloud")
    scores = {s.task: s for s in record.score_rows()}
    assert scores["code"].override == 95.0
    assert scores["code"].seed == 70.0
    assert scores["chat"].override is None


def test_override_matches_unknown_model_by_id_case_insensitively(catalog: Catalog) -> None:
    rules = rules_with({"MYSTERY:7B": {"scores": {"chat": 80}}})
    record = one(live("mystery:7b"), catalog, rules)
    assert record.row.unscored is True  # an override does not make it curated
    assert record.override_scores == {"chat": 80.0}


@pytest.mark.parametrize(
    "overrides",
    [{"x": {"scores": {"nope": 5}}}, {"x": {"scores": {"code": 101}}}, {"x": {"ctx": 0}}],
)
def test_bad_override_rules_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        rules_with(overrides)
````

## tests/test_discovery_refresh.py

````python
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from headwaters.cli.main import app
from headwaters.core.catalog import discovery, updater
from headwaters.core.catalog.http import SourceError
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import Catalog
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_misc import kv_get
from headwaters.core.state_models import get_model, get_score, list_models
from tests.test_discovery_sources import OLLAMA, OR, OR_MODELS, SHOW_QWEN, FakeHttp, ollama_routes

REPO_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()
BASIC = {"capabilities": ["completion"]}


class Http(FakeHttp):
    """FakeHttp that answers /api/show by model name."""

    def request_json(self, method: str, url: str, **kw: Any) -> Any:
        if url.endswith("/api/show"):
            self.calls.append((method, url, kw.get("body"), kw.get("headers")))
            return SHOW_QWEN if kw["body"]["model"] == "qwen:14b" else BASIC
        return super().request_json(method, url, **kw)


def routes(with_openrouter: bool = True) -> dict[tuple[str, str], Any]:
    out = ollama_routes()
    out[("GET", f"{OLLAMA}/api/tags")] = {"models": [{"name": "qwen:14b", "size": 9 * 1024**3}]}
    if with_openrouter:
        out[("GET", f"{OR}/models")] = OR_MODELS
    return out


@pytest.fixture
def catalog() -> Catalog:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "s" / "t.db")


def refresh(
    db: Database,
    catalog: Catalog,
    http: Http,
    rules: Rules | None = None,
    now: float = 100.0,
) -> discovery.RefreshResult:
    return discovery.refresh_models(
        db, rules or Rules(), catalog, http=http, env={}, home=Path("/nohome"), now=now
    )


def test_refresh_persists_models_scores_and_flags(db: Database, catalog: Catalog) -> None:
    result = refresh(db, catalog, Http(routes()))
    assert result.sources_ok == ["ollama", "opencode", "openrouter"]
    ids = {m.id for m in list_models(db)}
    assert {"qwen:14b", "vendor/big", "vendor/small:free"} <= ids
    qwen = get_model(db, "qwen:14b")
    assert qwen is not None
    assert (qwen.tier, qwen.warm, qwen.unscored, qwen.size_gb) == ("local", True, True, 9.0)
    assert qwen.arch == {"n_layers": 48, "n_kv_heads": 8, "head_dim": 128}
    assert qwen.size_vram_gb == 8.0
    score = get_score(db, "qwen:14b", "code")
    assert score is not None
    assert score.seed == 30.0  # conservative unknown-model default
    free = get_model(db, "vendor/small:free")
    assert free is not None
    assert free.tier == "free_cloud"
    assert kv_get(db, discovery.KV_LAST_REFRESH) == 100.0
    assert result.unscored >= 3
    assert result.models == len(ids)


def test_refresh_is_idempotent(db: Database, catalog: Catalog) -> None:
    refresh(db, catalog, Http(routes()))
    before = [(m.id, m.tier) for m in list_models(db)]
    again = refresh(db, catalog, Http(routes()), now=200.0)
    assert [(m.id, m.tier) for m in list_models(db)] == before
    assert again.removed == 0


def test_unreachable_sources_degrade_gracefully(db: Database, catalog: Catalog) -> None:
    result = refresh(db, catalog, Http({}))  # nothing answers
    assert result.sources_ok == ["opencode"]
    assert any(w.startswith("ollama:") for w in result.warnings)
    assert any(w.startswith("openrouter:") for w in result.warnings)
    assert {m.id for m in list_models(db)} == {  # curated cloud entries still available
        "example-paid/model-large",
        "example-openrouter/model-free:free",
    }


def test_outage_keeps_known_models_but_empty_answer_removes(db: Database, catalog: Catalog) -> None:
    refresh(db, catalog, Http(routes()))
    refresh(db, catalog, Http(routes(with_openrouter=False)), now=200.0)  # OpenRouter down
    assert get_model(db, "vendor/big") is not None  # kept: its source did not answer
    gone = routes()
    gone[("GET", f"{OLLAMA}/api/tags")] = {"models": []}
    result = refresh(db, catalog, Http(gone), now=300.0)  # Ollama answered with no models
    assert get_model(db, "qwen:14b") is None
    assert result.removed >= 1


def test_source_errors_never_escape(db: Database, catalog: Catalog) -> None:
    broken = Http(
        {
            ("GET", f"{OLLAMA}/api/tags"): SourceError("refused"),
            ("GET", f"{OR}/models"): SourceError("HTTP 500"),
        }
    )
    assert len(refresh(db, catalog, broken).warnings) == 2


def test_airplane_and_local_lock_skip_cloud_but_keep_local(db: Database, catalog: Catalog) -> None:
    for overrides in ({"airplane": True}, {"private_lock": True}, {"default_scope": "local"}):
        http = Http(routes())
        result = refresh(db, catalog, http, Rules.model_validate(overrides))
        assert "openrouter" not in result.sources_ok
        assert any("cloud discovery skipped" in w for w in result.warnings)
        assert all(OR not in call[1] for call in http.calls)
        assert get_model(db, "qwen:14b") is not None


def test_non_loopback_ollama_host_is_refused(db: Database, catalog: Catalog) -> None:
    rules = Rules.model_validate({"tiers": {"local": {"host": "http://10.1.2.3:11434"}}})
    http = Http(routes())
    result = refresh(db, catalog, http, rules)
    assert any("not loopback" in w for w in result.warnings)
    assert all("10.1.2.3" not in call[1] for call in http.calls)


def test_opencode_user_list_and_config(db: Database, catalog: Catalog, tmp_path: Path) -> None:
    cfg = tmp_path / "oc.json"
    cfg.write_text('{"provider": {"opencode": {"models": {"cfg-free": {}}}}}', encoding="utf-8")
    rules = Rules.model_validate(
        {
            "models": {"opencode_config": str(cfg)},
            "tiers": {"free_cloud": {"opencode_free": ["listed-free"]}},
        }
    )
    refresh(db, catalog, Http(routes()), rules)
    for name in ("opencode/cfg-free", "opencode/listed-free"):
        row = get_model(db, name)
        assert row is not None
        assert (row.tier, row.retains_data, row.unscored) == ("free_cloud", True, True)


def test_openai_compatible_endpoint_and_its_failure(db: Database, catalog: Catalog) -> None:
    rules = Rules.model_validate(
        {
            "models": {
                "openai_compatible": [
                    {"name": "good", "base_url": "https://g.example/v1", "models": ["m"]},
                    {"name": "bad", "base_url": "https://b.example/v1"},
                ]
            }
        }
    )
    result = refresh(db, catalog, Http(routes()), rules)
    assert get_model(db, "good/m") is not None
    assert any(w.startswith("bad:") for w in result.warnings)
    assert "openai_compatible:good" in result.sources_ok


def test_ttl_and_force(db: Database, catalog: Catalog, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = Rules()
    ttl = rules.models.refresh_ttl_s
    assert discovery.needs_refresh(db, rules, now=0)
    refresh(db, catalog, Http(routes()), now=1000.0)
    assert not discovery.needs_refresh(db, rules, now=1000.0 + ttl - 1)
    assert discovery.needs_refresh(db, rules, now=1000.0 + ttl)
    calls: list[int] = []

    def fake_refresh(*a: Any, **k: Any) -> discovery.RefreshResult:
        calls.append(1)
        return discovery.RefreshResult()

    monkeypatch.setattr(discovery, "refresh_models", fake_refresh)
    monkeypatch.setattr(updater, "load_active", lambda *a, **k: (catalog, "seed"))
    assert discovery.refresh_if_stale(db, rules, now=1001.0).skipped is True
    discovery.refresh_if_stale(db, rules, force=True, now=1001.0)
    discovery.refresh_if_stale(db, rules, now=1000.0 + ttl)
    assert len(calls) == 2


def test_cli_refresh_and_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWATERS_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert "No models yet" in runner.invoke(app, ["models", "list"]).output
    monkeypatch.setattr(discovery, "UrllibHttp", lambda: Http(routes()))
    result = runner.invoke(app, ["models", "refresh"])
    assert result.exit_code == 0
    assert "seed catalog" in result.output
    assert "ollama" in result.output
    listing = runner.invoke(app, ["models", "list"]).output
    qwen = next(line for line in listing.splitlines() if "qwen:14b" in line)
    assert "local" in qwen
    assert "unscored, warm" in qwen
    big = next(line for line in listing.splitlines() if "vendor/big" in line)
    assert "paid" in big
````

## tests/test_discovery_sources.py

````python
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
from headwaters.core.rules import OpenAICompatEndpoint

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


OR = sources.OPENROUTER_BASE
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
````

## tests/test_executor_cloud.py

````python
"""HDW-115: litellm backend and mapping helpers, with litellm fully mocked."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.core.executor.cloud import LiteLLMBackend, build_kwargs
from headwaters.core.executor.cloud_map import (
    attribution_headers,
    delta_from_chunk,
    map_exception,
    reasoning_kwargs,
)
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import BackendRequest, Delta, ExecOptions, ExecTarget
from headwaters.core.rules import AttributionRules
from tests.executor_support import chunk

TARGET = ExecTarget("m", "openrouter", "openrouter/acme/m:free")
MESSAGES = [{"role": "user", "content": "hi"}]


def req(options: ExecOptions | None = None, **kw: Any) -> BackendRequest:
    return BackendRequest(TARGET, MESSAGES, None, options or ExecOptions(), "30m", **kw)


class FakeLiteLLM:
    def __init__(self, chunks: list[Any], fail: Exception | None = None) -> None:
        self.chunks = chunks
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail is not None:
            raise self.fail

        async def gen() -> Any:
            for c in self.chunks:
                yield c

        return gen()


def collect(
    module: FakeLiteLLM | LiteLLMBackend, request: BackendRequest | None = None
) -> list[Delta]:
    backend = module if isinstance(module, LiteLLMBackend) else LiteLLMBackend(module)

    async def go() -> list[Delta]:
        return [d async for d in backend.stream(request or req())]

    return asyncio.run(go())


def test_reasoning_parameter_per_provider() -> None:
    assert reasoning_kwargs("openrouter", None) == {}
    assert reasoning_kwargs("openrouter", True) == {
        "extra_body": {"reasoning": {"effort": "medium"}}
    }
    assert reasoning_kwargs("openrouter", "high") == {
        "extra_body": {"reasoning": {"effort": "high"}}
    }
    assert reasoning_kwargs("openrouter", False) == {
        "extra_body": {"reasoning": {"enabled": False}}
    }
    assert reasoning_kwargs("anthropic", "low") == {"reasoning_effort": "low"}
    assert reasoning_kwargs("anthropic", True) == {"reasoning_effort": "medium"}
    assert reasoning_kwargs("anthropic", False) == {}


def test_attribution_is_opt_in() -> None:
    assert attribution_headers(AttributionRules()) == {}
    on = AttributionRules(referer="https://hw.example", title="HW")
    assert attribution_headers(on) == {
        "HTTP-Referer": "https://hw.example",
        "X-OpenRouter-Title": "HW",
    }


def test_build_kwargs_streams_without_retries_and_passes_options() -> None:
    options = ExecOptions(reasoning="high", json_schema={"type": "object"}, max_tokens=99)
    kwargs = build_kwargs(req(options, headers={"HTTP-Referer": "x"}, api_key="k"))
    assert kwargs["model"] == "openrouter/acme/m:free"
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}
    assert kwargs["num_retries"] == 0
    assert kwargs["max_retries"] == 0
    assert kwargs["api_key"] == "k"
    assert kwargs["extra_headers"] == {"HTTP-Referer": "x"}
    assert kwargs["max_tokens"] == 99
    assert kwargs["extra_body"] == {"reasoning": {"effort": "high"}}
    assert kwargs["response_format"]["json_schema"]["schema"] == {"type": "object"}


def test_stream_captures_reasoning_separately_from_content_and_usage() -> None:
    usage = SimpleNamespace(prompt_tokens=9, completion_tokens=2)
    module = FakeLiteLLM(
        [
            chunk(reasoning="because"),
            chunk(content="ok"),
            chunk(finish="stop"),
            SimpleNamespace(choices=[], usage=usage),
        ]
    )
    deltas = collect(module)
    assert [d.reasoning for d in deltas][:2] == ["because", ""]
    assert [d.content for d in deltas][:2] == ["", "ok"]
    assert deltas[2].finish_reason == "stop"
    assert deltas[3].usage is not None
    assert deltas[3].usage.prompt_tokens == 9
    assert module.calls[0]["stream"] is True


def test_delta_reads_openrouter_reasoning_field_as_fallback() -> None:
    delta = SimpleNamespace(content=None, reasoning_content=None, reasoning="why", tool_calls=None)
    c = SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)
    assert delta_from_chunk(c).reasoning == "why"


def test_tool_call_fragments_are_mapped() -> None:
    function = SimpleNamespace(name="read_file", arguments='{"a":')
    call = SimpleNamespace(index=0, id="c1", function=function)
    delta = delta_from_chunk(chunk(tool_calls=[call]))
    part = delta.tool_calls[0]
    assert (part.index, part.id, part.name, part.arguments) == (0, "c1", "read_file", '{"a":')


def test_mid_stream_error_chunk_raises_provider_error() -> None:
    with pytest.raises(ProviderError) as info:
        delta_from_chunk(chunk(finish="error"))
    assert info.value.kind == "stream_error"


class FakeRateLimit(Exception):
    status_code = 429
    message = "rate limited"
    response = SimpleNamespace(headers={"Retry-After": "12"})


class Timeout(Exception):
    pass


class APIConnectionError(Exception):
    pass


def test_exception_mapping_keeps_status_headers_and_body() -> None:
    mapped = map_exception(FakeRateLimit())
    assert (mapped.status, mapped.body) == (429, "rate limited")
    assert mapped.headers["Retry-After"] == "12"
    assert map_exception(Timeout()).kind == "timeout"
    assert map_exception(APIConnectionError()).kind == "connection"
    assert map_exception(ValueError("x")).status == 500


def test_backend_maps_litellm_failures() -> None:
    with pytest.raises(ProviderError) as info:
        collect(FakeLiteLLM([], fail=FakeRateLimit()))
    assert info.value.status == 429


def test_backend_loads_litellm_lazily_with_local_cost_map_and_no_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeLiteLLM([chunk(content="x"), chunk(finish="stop")])
    imported: list[str] = []

    def fake_import(name: str) -> Any:
        imported.append(name)
        return fake

    monkeypatch.delenv("LITELLM_LOCAL_MODEL_COST_MAP", raising=False)
    monkeypatch.setattr("headwaters.core.executor.cloud.importlib.import_module", fake_import)
    backend = LiteLLMBackend()
    assert imported == []  # nothing imported until the first cloud call
    deltas = collect(backend)
    assert imported == ["litellm"]
    assert deltas[0].content == "x"
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert fake.telemetry is False  # type: ignore[attr-defined]
````

## tests/test_executor_ollama.py

````python
"""HDW-115: Ollama native /api/chat backend against a mocked transport (no real network)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from headwaters.core.executor.errors import ExecutorError, LocalScopeViolation, ProviderError
from headwaters.core.executor.ollama import OllamaBackend
from headwaters.core.executor.ollama_convert import build_payload
from headwaters.core.executor.types import BackendRequest, Delta, ExecOptions, ExecTarget

MESSAGES = [{"role": "user", "content": "hi"}]


def request(options: ExecOptions | None = None, num_ctx: int | None = 24576) -> BackendRequest:
    target = ExecTarget("qwen", "ollama", "qwen2.5-coder:14b", num_ctx=num_ctx)
    return BackendRequest(target, MESSAGES, None, options or ExecOptions(), "30m")


def ndjson(*objects: dict[str, Any]) -> bytes:
    return ("\n".join(json.dumps(o) for o in objects) + "\n").encode()


def collect(handler: Any) -> list[Delta]:
    backend = OllamaBackend("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))

    async def go() -> list[Delta]:
        return [d async for d in backend.stream(request())]

    return asyncio.run(go())


def test_payload_has_num_ctx_keep_alive_format_and_think() -> None:
    opts = ExecOptions(reasoning=True, json_schema={"type": "object"}, max_tokens=50, temperature=0)
    payload = build_payload(request(opts))
    assert payload["model"] == "qwen2.5-coder:14b"
    assert payload["stream"] is True
    assert payload["options"] == {"num_ctx": 24576, "temperature": 0, "num_predict": 50}
    assert payload["keep_alive"] == "30m"
    assert payload["format"] == {"type": "object"}
    assert payload["think"] is True


def test_payload_requires_computed_num_ctx() -> None:
    with pytest.raises(ExecutorError) as info:
        build_payload(request(num_ctx=None))
    assert info.value.code == "missing_num_ctx"


def test_streaming_separates_thinking_from_content_and_reports_usage() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/chat"
        seen.append(json.loads(req.content))
        done = {"message": {"role": "assistant", "content": ""}, "done": True}
        done |= {"done_reason": "stop", "prompt_eval_count": 11, "eval_count": 4}
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"role": "assistant", "thinking": "hmm"}, "done": False},
                {"message": {"role": "assistant", "content": "Hi"}, "done": False},
                done,
            ),
        )

    deltas = collect(handler)
    assert [d.reasoning for d in deltas] == ["hmm", "", ""]
    assert [d.content for d in deltas] == ["", "Hi", ""]
    assert deltas[-1].finish_reason == "stop"
    assert deltas[-1].usage is not None
    assert (deltas[-1].usage.prompt_tokens, deltas[-1].usage.completion_tokens) == (11, 4)
    assert seen[0]["options"]["num_ctx"] == 24576


def test_tool_calls_are_collected_with_json_arguments() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        call = {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}
        end = {"message": {"role": "assistant", "content": ""}, "done": True}
        return httpx.Response(
            200,
            content=ndjson({"message": {"role": "assistant", "tool_calls": [call]}}, end),
        )

    deltas = collect(handler)
    part = deltas[0].tool_calls[0]
    assert (part.name, json.loads(part.arguments)) == ("read_file", {"path": "a.py"})
    assert deltas[-1].finish_reason == "tool_calls"


def test_http_error_becomes_provider_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b'{"error":"model not found"}')

    with pytest.raises(ProviderError) as info:
        collect(handler)
    assert info.value.status == 404
    assert "model not found" in info.value.body


def test_error_object_in_stream_becomes_provider_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson({"error": "llama runner crashed"}))

    with pytest.raises(ProviderError) as info:
        collect(handler)
    assert info.value.kind == "stream_error"


def test_connection_failure_and_timeout_are_mapped() -> None:
    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def slow(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderError) as refused:
        collect(refuse)
    assert refused.value.kind == "connection"
    with pytest.raises(ProviderError) as timed_out:
        collect(slow)
    assert timed_out.value.kind == "timeout"


def test_backend_refuses_non_loopback_host_at_construction() -> None:
    with pytest.raises(LocalScopeViolation):
        OllamaBackend("http://10.1.2.3:11434")
````

## tests/test_executor_ollama_convert.py

````python
"""HDW-115: OpenAI-style messages -> Ollama native messages."""

from __future__ import annotations

from typing import Any

from headwaters.core.executor.ollama_convert import parse_line, to_ollama_messages


def test_images_are_inlined_and_remote_urls_dropped() -> None:
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                {"type": "image_url", "image_url": {"url": "https://remote.example/x.png"}},
            ],
        }
    ]
    assert to_ollama_messages(messages) == [
        {"role": "user", "content": "what is this", "images": ["QUJD"]}
    ]


def test_assistant_tool_calls_and_tool_results() -> None:
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
    }
    messages: list[dict[str, Any]] = [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "c1", "content": "file text"},
    ]
    out = to_ollama_messages(messages)
    assert out[0]["tool_calls"] == [
        {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}
    ]
    assert out[1] == {"role": "tool", "content": "file text", "tool_name": "read_file"}


def test_bad_tool_arguments_become_empty_object() -> None:
    call = {"id": "c", "function": {"name": "f", "arguments": "{not json"}}
    out = to_ollama_messages([{"role": "assistant", "content": "", "tool_calls": [call]}])
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {}


def test_parse_line_without_message_is_empty_delta() -> None:
    delta, seen = parse_line({"done": False}, 0)
    assert (delta.content, delta.reasoning, delta.tool_calls, seen) == ("", "", (), 0)
````

## tests/test_executor_timeouts.py

````python
"""HDW-115: first-token timeout is longer for a cold local model; idle timeout after streaming."""

from __future__ import annotations

import pytest

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.types import Delta, ExecutorConfig
from tests.executor_support import (
    ScriptedBackend,
    Step,
    decision,
    kinds,
    make_executor,
    run_once,
    stream_events,
)

FAST = ExecutorConfig(
    cloud_first_token_s=0.05,
    local_warm_first_token_s=0.05,
    local_cold_first_token_s=1.0,
    stream_idle_s=0.05,
)
SLOW_START: list[Step] = [("sleep", 0.2), Delta(content="late"), Delta(finish_reason="stop")]


def test_cloud_first_token_timeout() -> None:
    ex = make_executor(ScriptedBackend(SLOW_START), config=FAST)
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision())
    assert info.value.code == "timeout"
    assert info.value.before_first_token is True


def test_warm_local_model_uses_short_timeout() -> None:
    ex = make_executor(ScriptedBackend(SLOW_START), config=FAST, cold=False)
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision("local", "qwen"))
    assert info.value.code == "timeout"


@pytest.mark.parametrize("cold", [True, None])
def test_cold_or_unknown_local_model_waits_longer(cold: bool | None) -> None:
    ex = make_executor(ScriptedBackend(SLOW_START), config=FAST, cold=cold)
    assert run_once(ex, decision("local", "qwen")).content == "late"


def test_default_config_cold_limit_exceeds_warm_limit() -> None:
    cfg = ExecutorConfig()
    assert cfg.local_cold_first_token_s > cfg.local_warm_first_token_s


def test_idle_timeout_after_first_token_is_terminal_event() -> None:
    script: list[Step] = [Delta(content="a"), ("sleep", 0.3), Delta(content="b")]
    events = stream_events(make_executor(ScriptedBackend(script), config=FAST), decision())
    assert kinds(events) == ["first_token", "content", "error"]
    assert events[-1].error is not None
    assert events[-1].error.code == "timeout"
````

## tests/test_features.py

````python
"""Feature registry, plugin interfaces and discovery."""

from __future__ import annotations

import pytest

from headwaters.core.features import STAR_LEGEND, FeatureRegistry, FeatureSpec
from headwaters.core.plugins import INTERFACES, PluginContractError, check_plugin
from headwaters.features._example import ExampleVerifier
from tests.contracts.harness import BEHAVIOUR


class _BadVersion:
    api_version = 999

    def verify(self, candidate: str) -> list[str]:
        return []


class _StrVersion:
    api_version = "1"

    def verify(self, candidate: str) -> list[str]:
        return []


def test_legend_text() -> None:
    assert STAR_LEGEND == "★ = free during beta; may become part of Headwaters Pro at 1.0"


def test_every_interface_has_a_contract_behaviour() -> None:
    assert set(INTERFACES) == set(BEHAVIOUR)


def test_spec_validation() -> None:
    with pytest.raises(ValueError, match="empty"):
        FeatureSpec(id="", star=False, title="x")
    with pytest.raises(ValueError, match="unknown interfaces"):
        FeatureSpec(id="a", star=False, title="x", interfaces=("NopePlugin",))
    assert FeatureSpec(id="a", star=True, title="Alpha").label == "★ Alpha"
    assert FeatureSpec(id="b", star=False, title="Beta").label == "Beta"


def test_enabled_defaults_and_flags() -> None:
    reg = FeatureRegistry()
    reg.register(FeatureSpec(id="on", star=True, title="On"))
    reg.register(FeatureSpec(id="off", star=True, title="Off", default_enabled=False))
    assert reg.enabled("on") is True
    assert reg.enabled("off") is False
    assert reg.enabled("missing") is False
    reg.set_flags({"on": False, "off": True})
    assert reg.enabled("on") is False
    assert reg.enabled("off") is True


def test_duplicate_registration_rejected() -> None:
    reg = FeatureRegistry()
    spec = FeatureSpec(id="a", star=False, title="A")
    reg.register(spec)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(spec)


def test_undeclared_interface_rejected() -> None:
    reg = FeatureRegistry()
    spec = FeatureSpec(id="a", star=True, title="A", interfaces=("LedgerPlugin",))
    with pytest.raises(PluginContractError, match="does not declare"):
        reg.register(spec, {"VerifierPlugin": ExampleVerifier()})


def test_incompatible_plugins_rejected() -> None:
    with pytest.raises(PluginContractError, match="unknown plugin interface"):
        check_plugin("NopePlugin", object())
    with pytest.raises(PluginContractError, match="does not implement"):
        check_plugin("VerifierPlugin", object())
    with pytest.raises(PluginContractError, match="api_version 999"):
        check_plugin("VerifierPlugin", _BadVersion())
    with pytest.raises(PluginContractError, match="must be an int"):
        check_plugin("VerifierPlugin", _StrVersion())
````

## tests/test_features_discovery.py

````python
"""Discovery of ★ features and entry points; core must not import them."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest

from headwaters.core import features
from headwaters.core.features import (
    FeatureRegistry,
    FeatureSpec,
    discover_entry_points,
    discover_features,
)
from headwaters.core.plugins import PluginContractError
from headwaters.features._example import ExampleLedger
from tests.contracts.harness import run_contract


def test_plugins_for_respects_flags() -> None:
    reg = FeatureRegistry()
    discover_features(reg)
    assert len(reg.plugins_for("VerifierPlugin")) == 1
    reg.set_flags({"_example": False})
    assert reg.plugins_for("VerifierPlugin") == []
    with pytest.raises(ValueError, match="unknown plugin interface"):
        reg.plugins_for("NopePlugin")


def test_example_found_by_discovery_and_passes_contracts() -> None:
    reg = FeatureRegistry()
    assert discover_features(reg) == ["headwaters.features._example"]
    spec = reg.get("_example")
    assert spec is not None
    assert spec.star
    assert reg.enabled("_example")  # every star feature is on by default in the beta
    for interface in spec.interfaces:
        for plugin in reg.plugins_for(interface):
            run_contract(interface, plugin)
    reg.clear()
    assert reg.all() == []


def test_ledger_example_records() -> None:
    ledger = ExampleLedger()
    run_contract("LedgerPlugin", ledger)
    assert ledger.events == [{"route": "local", "wall": None}]


def test_module_level_registry() -> None:
    reg = features.get_registry()
    reg.clear()
    try:
        features.register(FeatureSpec(id="m", star=False, title="M"))
        assert features.enabled("m")
    finally:
        reg.clear()


def test_discover_skips_packages_without_register(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "fakefeatures"
    (pkg / "plain").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "plain" / "__init__.py").write_text("")
    (pkg / "module_only.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert discover_features(FeatureRegistry(), "fakefeatures") == []


def test_discover_non_package_returns_empty() -> None:
    assert discover_features(FeatureRegistry(), "headwaters.core.paths") == []


class _FakeEntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        return self._target


def _hook(reg: FeatureRegistry) -> None:
    reg.register(FeatureSpec(id="ep", star=True, title="EP"))


class _ModuleLike:
    register = staticmethod(_hook)


def test_entry_point_callable_and_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint("a", _hook)]
    )
    reg = FeatureRegistry()
    assert discover_entry_points(reg) == ["a"]
    assert reg.get("ep") is not None

    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint("m", _ModuleLike)]
    )
    assert discover_entry_points(FeatureRegistry()) == ["m"]


def test_entry_point_without_register_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint("b", object())]
    )
    with pytest.raises(PluginContractError, match="no register"):
        discover_entry_points(FeatureRegistry())


def test_core_does_not_import_features() -> None:
    """Importing the core registry must not pull in any headwaters.features module."""
    code = (
        "import sys, headwaters.core.features, headwaters.core.plugins;"
        "bad=[m for m in sys.modules if m.startswith('headwaters.features.')];"
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], check=False)
    assert result.returncode == 0
````

## tests/test_hardware_monitor.py

````python
"""HDW-108: HardwareMonitor detection order, graceful no-GPU degrade, caching."""

from __future__ import annotations

from headwaters.core.hardware.gpu import GpuInfo
from headwaters.core.hardware.state import BatteryState, HardwareMonitor, ManualGpu
from headwaters.core.rules import LocalTier

AC = BatteryState(False, False)


def gpu_info(source: str = "nvml", display: bool | None = False) -> GpuInfo:
    return GpuInfo("RTX", 12288, 11000, display, source, ())  # type: ignore[arg-type]


def _none() -> GpuInfo | None:
    return None


def test_no_nvidia_gpu_degrades_to_cloud_only() -> None:
    state = HardwareMonitor(LocalTier(), probes=[_none, _none]).get_state()
    assert state.gpu is None
    assert not state.has_gpu
    assert state.local_suggestion == "cloud_only_suggested"
    assert state.budget_gb == 0.0
    assert state.profile == "custom"


def test_probe_order_falls_back_and_survives_exceptions() -> None:
    def boom() -> GpuInfo | None:
        raise RuntimeError("driver crash")

    smi = gpu_info("nvidia-smi")
    state = HardwareMonitor(LocalTier(), probes=[boom, lambda: smi]).get_state()
    assert state.gpu is smi


def test_manual_config_fallback() -> None:
    manual = HardwareMonitor(LocalTier(), probes=[_none], manual=ManualGpu("My GPU", 12.0))
    state = manual.get_state()
    assert state.gpu is not None
    assert state.gpu.source == "manual"
    assert state.profile == "laptop_12gb"
    assert state.local_suggestion == "ok"
    by_profile = HardwareMonitor(LocalTier(profile="desktop_16gb"), probes=[_none]).get_state()
    assert by_profile.gpu is not None
    assert by_profile.gpu.total_mb == 16 * 1024


def test_state_is_cached_then_refreshed() -> None:
    now = [100.0]
    calls = [0]

    def probe() -> GpuInfo | None:
        calls[0] += 1
        return gpu_info()

    monitor = HardwareMonitor(
        LocalTier(),
        probes=[probe],
        battery_reader=lambda: AC,
        ttl_s=5.0,
        clock=lambda: now[0],
    )
    first = monitor.get_state()
    now[0] += 4.0
    assert monitor.get_state() is first
    assert calls[0] == 1
    now[0] += 2.0
    assert monitor.get_state() is not first
    assert calls[0] == 2
    monitor.get_state(force=True)
    assert calls[0] == 3
    monitor.invalidate()
    monitor.get_state()
    assert calls[0] == 4


def test_set_local_rules_invalidates_cache() -> None:
    monitor = HardwareMonitor(LocalTier(), probes=[lambda: gpu_info()], battery_reader=lambda: AC)
    assert monitor.get_state().reserve_gb == 1.0
    monitor.set_local_rules(LocalTier(reserve_gb=3.0))
    assert monitor.get_state().reserve_gb == 3.0
````

## tests/test_hardware_probes.py

````python
"""HDW-108: NVML and nvidia-smi probes with mocked driver output."""

from __future__ import annotations

import sys
import types
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.core.hardware import gpu as gpu_mod
from headwaters.core.hardware.gpu import probe_nvidia_smi, probe_nvml

MIB = 1024 * 1024
_NAME = b"NVIDIA GeForce RTX 4080 Laptop GPU"


def _fake_nvml(
    total_mib: int = 12288,
    free_mib: int = 11000,
    display: int | Exception = 0,
    procs: Sequence[Any] = (),
    fail_init: bool = False,
) -> types.ModuleType:
    mod: Any = types.ModuleType("fake_nvml")

    def init() -> None:
        if fail_init:
            raise RuntimeError("no driver")

    def display_active(_h: object) -> int:
        if isinstance(display, Exception):
            raise display
        return display

    mod.nvmlInit = init
    mod.nvmlShutdown = lambda: None
    mod.nvmlDeviceGetCount = lambda: 1
    mod.nvmlDeviceGetHandleByIndex = lambda i: i
    mod.nvmlDeviceGetMemoryInfo = lambda h: SimpleNamespace(
        total=total_mib * MIB, free=free_mib * MIB, used=(total_mib - free_mib) * MIB
    )
    mod.nvmlDeviceGetName = lambda h: _NAME
    mod.nvmlDeviceGetDisplayActive = display_active
    mod.nvmlDeviceGetComputeRunningProcesses = lambda h: list(procs)
    mod.nvmlDeviceGetGraphicsRunningProcesses = lambda h: []
    return mod


@pytest.fixture
def install_nvml(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(mod: types.ModuleType) -> str:
        monkeypatch.setitem(sys.modules, "fake_nvml", mod)
        return "fake_nvml"

    return install


def test_nvml_probe_reads_gpu(install_nvml: Any) -> None:
    proc = SimpleNamespace(pid=1234, usedGpuMemory=2048 * MIB)
    info = probe_nvml(install_nvml(_fake_nvml(display=1, procs=[proc])))
    assert info is not None
    assert info.name == "NVIDIA GeForce RTX 4080 Laptop GPU"
    assert info.total_mb == 12288
    assert info.free_mb == 11000
    assert info.used_mb == 1288
    assert info.display_active is True
    assert info.source == "nvml"
    assert info.processes[0].pid == 1234
    assert info.processes[0].mem_mb == 2048


def test_nvml_display_query_failure_is_unknown(install_nvml: Any) -> None:
    info = probe_nvml(install_nvml(_fake_nvml(display=RuntimeError("not supported"))))
    assert info is not None
    assert info.display_active is None


def test_nvml_hidden_process_memory_is_none(install_nvml: Any) -> None:
    proc = SimpleNamespace(pid=7, usedGpuMemory=(1 << 64) - 1)
    info = probe_nvml(install_nvml(_fake_nvml(procs=[proc])))
    assert info is not None
    assert info.processes[0].mem_mb is None


def test_nvml_unavailable_returns_none(install_nvml: Any) -> None:
    assert probe_nvml(install_nvml(_fake_nvml(fail_init=True))) is None
    assert probe_nvml("definitely_not_a_module_xyz") is None


def _smi(gpu_line: str | None, proc_lines: str = "") -> Any:
    def run(args: Sequence[str]) -> str | None:
        if any(a.startswith("--query-gpu") for a in args):
            return gpu_line
        return proc_lines

    return run


def test_smi_probe_parses_csv() -> None:
    runner = _smi(
        "NVIDIA GeForce RTX 5080, 16303, 15000, Disabled\n",
        "4321, C:\\Games\\game.exe, 9000\n99, ollama.exe, 5000\n",
    )
    info = probe_nvidia_smi(runner)
    assert info is not None
    assert info.source == "nvidia-smi"
    assert info.name == "NVIDIA GeForce RTX 5080"
    assert info.total_mb == 16303
    assert info.free_mb == 15000
    assert info.display_active is False
    assert [(p.pid, p.name, p.mem_mb) for p in info.processes] == [
        (4321, "game.exe", 9000.0),
        (99, "ollama.exe", 5000.0),
    ]


def test_smi_probe_garbage_and_missing() -> None:
    assert probe_nvidia_smi(_smi(None)) is None
    assert probe_nvidia_smi(_smi("")) is None
    assert probe_nvidia_smi(_smi("NVIDIA, [N/A], [N/A], Enabled")) is None
    assert probe_nvidia_smi(_smi("too, few")) is None
    info = probe_nvidia_smi(_smi("GPU X, 8192, 8000, [N/A]"))
    assert info is not None
    assert info.display_active is None


def test_smi_picks_largest_gpu() -> None:
    info = probe_nvidia_smi(_smi("Small, 4096, 4000, Enabled\nBig, 16303, 9000, Disabled\n"))
    assert info is not None
    assert info.name == "Big"


def test_run_nvidia_smi_without_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_mod, "find_nvidia_smi", lambda: None)
    assert gpu_mod.run_nvidia_smi(["-L"]) is None
````

## tests/test_hardware_state.py

````python
"""HDW-108: reserve, profile, battery, GPU-busy and the HardwareState snapshot."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.core.hardware.gpu import GpuInfo, GpuProcess
from headwaters.core.hardware.state import (
    BatteryState,
    build_state,
    compute_reserve,
    detect_gpu_busy,
    read_battery,
    select_profile,
)
from headwaters.core.rules import LocalTier

AC = BatteryState(False, False)


def gpu_info(
    total: float = 12288,
    free: float | None = 11000,
    display: bool | None = False,
    procs: tuple[GpuProcess, ...] = (),
    source: Any = "nvml",
) -> GpuInfo:
    return GpuInfo("RTX", total, free, display, source, procs)


@pytest.mark.parametrize(
    ("display", "override", "expected", "source"),
    [
        (True, None, 2.0, "display_active"),
        (False, None, 1.0, "no_display"),
        (None, None, 2.5, "unknown_display"),
        (True, 3.5, 3.5, "override"),
        (False, 0.0, 0.0, "override"),
    ],
)
def test_compute_reserve(
    display: bool | None, override: float | None, expected: float, source: str
) -> None:
    assert compute_reserve(display, override) == (expected, source)


@pytest.mark.parametrize(
    ("total_gb", "requested", "expected"),
    [
        (12.0, "auto", "laptop_12gb"),
        (16.0, "auto", "desktop_16gb"),
        (24.0, "auto", "custom"),
        (8.0, "auto", "custom"),
        (None, "auto", "custom"),
        (24.0, "laptop_12gb", "laptop_12gb"),
        (12.0, "custom", "custom"),
        (12.0, "desktop_16gb", "desktop_16gb"),
    ],
)
def test_select_profile(total_gb: float | None, requested: str, expected: str) -> None:
    assert select_profile(total_gb, requested) == expected


def test_gpu_busy_by_known_process() -> None:
    game = GpuProcess(10, "game.exe", 8000)
    busy = detect_gpu_busy(gpu_info(procs=(game,)), 50, own_pid=1)
    assert busy.busy
    assert busy.top_process == "game.exe"
    assert round(busy.other_used_pct) == 65


def test_gpu_busy_ignores_ollama_and_self() -> None:
    procs = (GpuProcess(1, "python.exe", 9000), GpuProcess(2, "ollama.exe", 9000))
    assert not detect_gpu_busy(gpu_info(procs=procs), 50, own_pid=1).busy


def test_gpu_busy_hidden_memory_uses_used_vram() -> None:
    hidden = GpuProcess(10, "render.exe", None)
    busy = detect_gpu_busy(gpu_info(free=2000, procs=(hidden,)), 50, own_pid=1)
    assert busy.busy
    assert busy.top_process == "render.exe"
    # Ollama holds memory but its size is hidden: cannot attribute, so not busy.
    ollama = GpuProcess(11, "ollama.exe", None)
    assert not detect_gpu_busy(gpu_info(free=2000, procs=(ollama,)), 50, own_pid=1).busy


def test_gpu_busy_no_gpu_and_threshold() -> None:
    assert not detect_gpu_busy(None).busy
    small = GpuProcess(10, "app.exe", 1000)
    assert not detect_gpu_busy(gpu_info(procs=(small,)), 50, own_pid=1).busy
    assert detect_gpu_busy(gpu_info(procs=(small,)), 5, own_pid=1).busy


def test_build_state_laptop_no_display() -> None:
    state = build_state(gpu_info(display=False), LocalTier(), BatteryState(True, False, 90.0))
    assert state.profile == "laptop_12gb"
    assert state.reserve_gb == 1.0
    assert state.budget_gb == pytest.approx(11.0)
    assert state.local_suggestion == "ok"
    assert state.battery_policy == "normal"


def test_build_state_desktop_display_and_override() -> None:
    gpu = gpu_info(total=16384, free=15000, display=True)
    state = build_state(gpu, LocalTier(), AC)
    assert (state.profile, state.reserve_gb, state.budget_gb) == ("desktop_16gb", 2.0, 14.0)
    over = build_state(gpu, LocalTier(reserve_gb=4.0), AC)
    assert (over.reserve_gb, over.reserve_source, over.budget_gb) == (4.0, "override", 12.0)


def test_build_state_unknown_display_notes() -> None:
    state = build_state(gpu_info(display=None), LocalTier(), AC)
    assert state.reserve_gb == 2.5
    assert any("Display status unknown" in n for n in state.notes)


@pytest.mark.parametrize("policy", ["prefer_cloud", "normal", "local_only"])
def test_battery_policy_applies_only_on_battery(policy: str) -> None:
    local = LocalTier(on_battery=policy)  # type: ignore[arg-type]
    on = build_state(gpu_info(), local, BatteryState(True, True, 40.0))
    ac = build_state(gpu_info(), local, BatteryState(True, False, 40.0))
    assert on.battery_policy == policy
    assert ac.battery_policy == "normal"


def test_busy_gpu_is_degraded() -> None:
    game = GpuProcess(10, "game.exe", 9000)
    state = build_state(gpu_info(procs=(game,)), LocalTier(), AC)
    assert state.local_suggestion == "degraded"
    assert state.gpu_busy.busy


def test_read_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    import psutil

    monkeypatch.setattr(psutil, "sensors_battery", lambda: None, raising=False)
    assert read_battery() == BatteryState(False, False)
    reading = SimpleNamespace(percent=55, power_plugged=False)
    monkeypatch.setattr(psutil, "sensors_battery", lambda: reading, raising=False)
    assert read_battery() == BatteryState(True, True, 55.0)
    reading.power_plugged = None
    assert not read_battery().on_battery

    def boom() -> None:
        raise OSError("no sensor")

    monkeypatch.setattr(psutil, "sensors_battery", boom, raising=False)
    assert read_battery() == BatteryState(False, False)
````

## tests/test_hardware_sysmem.py

````python
"""HDW-109: sysmem-fallback detection and residency (warm bonus / eviction) inputs."""

from __future__ import annotations

import pytest

from headwaters.core.catalog.schema import ModelEntry
from headwaters.core.hardware.residency import (
    LoadedModel,
    ResidencyConfig,
    default_max_loaded,
    residency_inputs,
)
from headwaters.core.hardware.sysmem import (
    SysmemTracker,
    detect_sysmem_fallback,
    expected_tps_for,
)


def test_sysmem_detection_spilled() -> None:
    v = detect_sysmem_fallback(8.0, 35.0, size_bytes=10_000, size_vram_bytes=6_000)
    assert v.detected
    assert v.evidence == "spilled"
    assert v.ratio == pytest.approx(8 / 35)
    assert v.hint is not None
    assert "Sysmem Fallback Policy" in v.hint


def test_sysmem_detection_slow_but_resident() -> None:
    v = detect_sysmem_fallback(10.0, 35.0, 10_000, 10_000)
    assert (v.detected, v.evidence) == (True, "resident_but_slow")


def test_sysmem_not_detected_cases() -> None:
    assert detect_sysmem_fallback(30.0, 35.0, 10_000, 5_000).evidence == "none"
    assert detect_sysmem_fallback(None, 35.0, 1, 1).evidence == "no_data"
    assert detect_sysmem_fallback(10.0, None, 1, 1).evidence == "no_data"
    busy = detect_sysmem_fallback(5.0, 35.0, 10_000, 5_000, gpu_busy=True)
    assert (busy.detected, busy.evidence) == (False, "gpu_busy")


def test_sysmem_tracker_and_expected_tps() -> None:
    tracker = SysmemTracker()
    tracker.record("m", detect_sysmem_fallback(5.0, 35.0, 10_000, 5_000))
    assert tracker.is_flagged("m")
    assert "Sysmem Fallback Policy" in tracker.hints()["m"]
    tracker.record("m", detect_sysmem_fallback(34.0, 35.0, 10_000, 10_000))
    assert not tracker.is_flagged("m")
    entry = ModelEntry(id="m", provider="ollama", expected_tps={"laptop_12gb": 35.0})
    assert expected_tps_for(entry, "laptop_12gb") == 35.0
    assert expected_tps_for(entry, "custom") is None


def test_residency_warm_and_eviction() -> None:
    loaded = [LoadedModel("a", 9.0)]
    warm = residency_inputs("a", 9.0, loaded, 10.5)
    assert warm.warm
    assert warm.warm_bonus > 0
    assert warm.evicts == ()
    cold = residency_inputs("b", 6.0, loaded, 10.5)
    assert not cold.warm
    assert cold.evicts == ("a",)
    assert cold.eviction_penalty == ResidencyConfig().eviction_penalty_per_model
    free = residency_inputs("b", 6.0, [], 10.5)
    assert free.evicts == ()
    assert free.eviction_penalty == 0.0


def test_residency_two_models_and_defaults() -> None:
    loaded = [LoadedModel("a", 9.0)]
    two = ResidencyConfig(max_loaded_models=2)
    assert residency_inputs("b", 3.0, loaded, 14.5, two).evicts == ()
    assert residency_inputs("b", 6.0, loaded, 14.5, two).evicts == ("a",)  # VRAM, not count
    assert default_max_loaded("laptop_12gb") == 1
    assert default_max_loaded("desktop_16gb") == 2
````

## tests/test_logging.py

````python
import json
import logging
from pathlib import Path

import pytest

from headwaters.core import logging as hw_logging
from headwaters.core.logging import REDACTED, configure_logging, redact_text, redact_value


@pytest.fixture(autouse=True)
def _cleanup() -> object:
    yield
    hw_logging.shutdown_logging()


SECRETS = [
    "sk-proj-abcdef1234567890ABCDEF",
    "sk-ant-api03-abcdefghijklmnop",
    "sk-or-v1-0123456789abcdef",
    "ghp_" + "a1B2c3D4e5" * 4,
    "github_pat_" + "A1b2C3d4E5" * 3,
    "AKIAIOSFODNN7EXAMPLE",
    "AIza" + "Sy" + "A" * 33,
    "xoxb-1234567890-abcdefghij",
    "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4",
    "0123456789abcdef0123456789abcdef0123456789abcdef",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_redacts_token_shapes(secret: str) -> None:
    out = redact_text(f"calling with {secret} now")
    assert secret not in out
    assert REDACTED in out


def test_redacts_bearer_and_assignments() -> None:
    assert "abc12345678" not in redact_text("Authorization: Bearer abc12345678")
    out = redact_text("api_key=hunter22secret and password: 'pw123456'")
    assert "hunter22secret" not in out
    assert "pw123456" not in out
    assert "api_key=" in out


def test_redacts_private_key_block() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert "MIIabc" not in redact_text(pem)


def test_plain_text_untouched() -> None:
    msg = "routed to model tier=free reason=quota"
    assert redact_text(msg) == msg


def test_redact_value_drops_content_fields() -> None:
    data = {
        "model": "x",
        "prompt": "secret prompt",
        "Messages": [{"role": "user"}],
        "nested": {"content": "hi", "api_key": "whatever", "ok": 1},
        "list": ["sk-abcdefgh12345"],
    }
    out = redact_value(data)
    assert out == {
        "model": "x",
        "nested": {"api_key": REDACTED, "ok": 1},
        "list": [REDACTED],
    }


def _read_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_logger_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log_file = tmp_path / "logs dir" / "hw.log"
    logger = configure_logging(debug=False, log_file=log_file)
    logger.info("key %s used", "sk-abcdefgh12345678")
    logger.info(
        "decision",
        extra={
            "prompt": "TOP SECRET",
            "content": "TOP SECRET",
            "messages": ["TOP SECRET"],
            "tier": "free",
            "token": "abcdef123456",
            "meta": {"api_key": "zzzzzzzz"},
        },
    )
    try:
        raise ValueError("boom sk-abcdefgh12345678")
    except ValueError:
        logger.exception("failed")
    for h in logger.handlers:
        h.flush()
    text = log_file.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert captured.out == ""
    for blob in (text, captured.err):
        assert "sk-abcdefgh12345678" not in blob
        assert "TOP SECRET" not in blob
        assert "abcdef123456" not in blob
        assert "zzzzzzzz" not in blob
    rows = _read_lines(log_file)
    decision = rows[1]
    assert decision["tier"] == "free"
    assert "prompt" not in decision
    assert decision["token"] == REDACTED
    assert "exc" in rows[2]


def test_debug_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logger = configure_logging(debug=True, log_file=tmp_path / "a.log")
    assert logger.level == logging.DEBUG
    logger = configure_logging(debug=False, log_file=tmp_path / "a.log")
    assert logger.level == logging.INFO
    monkeypatch.setenv(hw_logging.DEBUG_ENV_VAR, "1")
    assert configure_logging(to_file=False).level == logging.DEBUG


def test_configure_is_idempotent_and_uses_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADWATERS_HOME", str(tmp_path))
    configure_logging()
    logger = configure_logging()
    assert len([h for h in logger.handlers if h.get_name() == "headwaters-managed"]) == 2
    logger.info("hello")
    assert (tmp_path.resolve() / "logs" / hw_logging.LOG_FILE_NAME).exists()
    assert hw_logging.get_logger("router").name == "headwaters.router"
    assert hw_logging.get_logger().name == "headwaters"
````

## tests/test_paths.py

````python
import sys
from pathlib import Path

import pytest

from headwaters.core import paths


def test_home_override_redirects_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    p = paths.get_paths()
    root = tmp_path.resolve()
    assert p.config == root / "config"
    assert p.state == root / "state"
    assert p.cache == root / "cache"
    assert p.log == root / "logs"


def test_override_with_spaces_and_ensure_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "dir with spaces"
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(home))
    p = paths.get_paths().ensure_dirs()
    assert all(d.is_dir() for d in (p.config, p.state, p.cache, p.log))
    assert paths.config_dir() == p.config
    assert paths.state_dir() == p.state
    assert paths.cache_dir() == p.cache
    assert paths.log_dir() == p.log


def test_default_dirs_use_app_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    p = paths.get_paths()
    for d in (p.config, p.state, p.cache, p.log):
        assert d.is_absolute()
        assert "Headwaters" in d.parts


@pytest.mark.windows
def test_windows_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    p = paths.get_paths()
    # Config roams (%APPDATA%\Headwaters); state/cache/logs are local (%LOCALAPPDATA%\Headwaters).
    assert "AppData" in p.config.parts
    assert "Roaming" in p.config.parts
    assert "Local" in p.state.parts
    assert "Local" in p.cache.parts
    assert "Local" in p.log.parts
    assert p.config.name == "Headwaters"


@pytest.mark.windows
def test_long_path_gets_extended_prefix(tmp_path: Path) -> None:
    long_dir = tmp_path.joinpath(*["a" * 40] * 8)
    safe = paths.long_safe(long_dir)
    assert str(safe).startswith("\\\\?\\")
    safe.mkdir(parents=True)
    assert safe.is_dir()


def test_short_path_unchanged(tmp_path: Path) -> None:
    assert paths.long_safe(tmp_path) == tmp_path


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows passthrough")
def test_long_path_passthrough_off_windows() -> None:
    long_path = Path("/" + "/".join(["a" * 40] * 8))
    assert paths.long_safe(long_path) == long_path
````

## tests/test_project_config.py

````python
from pathlib import Path

import pytest

from headwaters.core.project_config import (
    PROJECT_FILE_NAME,
    ProjectConfig,
    effective_scope,
    find_project_file,
    load_project_config,
)
from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError


def test_walks_up_from_nested_dir(tmp_path: Path) -> None:
    (tmp_path / PROJECT_FILE_NAME).write_text(
        'privacy: local\nchecks: ["ruff check {files}", "pytest -q -x tests/unit"]\n',
        encoding="utf-8",
    )
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    cfg, found = load_project_config(nested)
    assert found == (tmp_path / PROJECT_FILE_NAME).resolve()
    assert cfg.privacy == "local"
    assert cfg.checks == ["ruff check {files}", "pytest -q -x tests/unit"]


def test_nearest_file_wins(tmp_path: Path) -> None:
    (tmp_path / PROJECT_FILE_NAME).write_text("privacy: local\n", encoding="utf-8")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / PROJECT_FILE_NAME).write_text("privacy: free\n", encoding="utf-8")
    assert load_project_config(inner)[0].privacy == "free"


def test_start_may_be_a_file(tmp_path: Path) -> None:
    (tmp_path / PROJECT_FILE_NAME).write_text("privacy: free\n", encoding="utf-8")
    source = tmp_path / "x.py"
    source.write_text("", encoding="utf-8")
    assert find_project_file(source) is not None


def test_absent_gives_defaults(tmp_path: Path) -> None:
    # tmp_path ancestors might hold a stray file on a dev box; pin to an isolated start.
    cfg, found = load_project_config(tmp_path)
    if found is None:
        assert cfg == ProjectConfig()


@pytest.mark.parametrize("text", ["privacy: public\n", "checks: pytest\n", "extra: 1\n"])
def test_invalid_project_config(tmp_path: Path, text: str) -> None:
    (tmp_path / PROJECT_FILE_NAME).write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_project_config(tmp_path)


@pytest.mark.parametrize(
    ("scope", "privacy", "expected"),
    [
        ("auto", "any", "auto"),
        ("cloud", "any", "cloud"),
        ("auto", "free", "free"),
        ("cloud", "free", "free"),
        ("auto", "local", "local"),
        ("free", "local", "local"),
        ("local", "any", "local"),  # never loosened
        ("local", "free", "local"),
        ("free", "free", "free"),
    ],
)
def test_privacy_only_tightens(scope: str, privacy: str, expected: str) -> None:
    rules = Rules.model_validate({"default_scope": scope})
    project = ProjectConfig.model_validate({"privacy": privacy})
    assert effective_scope(rules, project) == expected
````

## tests/test_rules.py

````python
import json
from pathlib import Path

import pytest
import yaml

from headwaters.core.rules import Rules
from headwaters.core.rules_io import (
    SCHEMA_RELATIVE_PATH,
    ConfigError,
    ensure_rules_file,
    load_rules,
    main,
    schema_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_defaults_match_design() -> None:
    r = Rules()
    assert (r.default_scope, r.strategy, r.verify, r.advice) == (
        "auto",
        "cost_saver",
        "agentic",
        "off",
    )
    assert r.gateway.port == 4000
    assert r.dashboard.port == 4001
    assert r.mcp.async_threshold_s == 20
    assert r.walls.cascade_if_wait_over_s == 8
    assert r.walls.soft_exhaust_pct == 90
    assert r.floors.agentic_bonus == 15
    assert r.floors.high_risk_bonus == 10
    assert r.floors.optimistic_margin == 8
    assert r.tiers.local.reserve_gb is None
    assert r.tiers.local.on_battery == "prefer_cloud"
    assert r.tiers.free_cloud.ranking == "catalog"
    assert r.telemetry is False
    assert r.secrets.action == "local"


def test_first_run_writes_defaults_and_roundtrips(tmp_path: Path) -> None:
    target = tmp_path / "cfg" / "rules.yaml"
    ensure_rules_file(target)
    assert target.is_file()
    assert load_rules(target) == Rules()


def test_ensure_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("strategy: quality_first\n", encoding="utf-8")
    ensure_rules_file(target)
    assert load_rules(target).strategy == "quality_first"


def test_empty_file_is_defaults(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("", encoding="utf-8")
    assert load_rules(target) == Rules()


def test_partial_file_overrides_only_named_fields(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("tiers:\n  local:\n    reserve_gb: 2.0\n", encoding="utf-8")
    r = load_rules(target)
    assert r.tiers.local.reserve_gb == 2.0
    assert r.tiers.local.keep_alive == "30m"


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("default_scope: everywhere\n", "default_scope"),
        ("gateway:\n  port: 99999\n", "gateway.port"),
        ("telemetry: true\n", "telemetry"),
        ("strategyy: cost_saver\n", "strategyy"),
        ("secrets:\n  action: shout\n", "secrets.action"),
        ("budget:\n  paid_daily_usd: -1\n", "budget.paid_daily_usd"),
        ("version: 2\n", "version"),
    ],
)
def test_invalid_values_name_the_field(tmp_path: Path, text: str, needle: str) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_rules(target)
    assert any(needle in p for p in info.value.problems)
    assert str(target) in str(info.value)


def test_yaml_syntax_error_has_line(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("strategy: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML syntax error at line"):
        load_rules(target)


def test_non_mapping_and_missing_file(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_rules(target)
    with pytest.raises(ConfigError, match="cannot read"):
        load_rules(tmp_path / "missing.yaml")


def test_schema_is_valid_json_and_mentions_fields() -> None:
    schema = json.loads(schema_json())
    assert schema["$schema"].startswith("https://json-schema.org")
    for key in ("default_scope", "tiers", "floors", "hosts", "advice", "features"):
        assert key in schema["properties"]


def test_committed_schema_is_current() -> None:
    committed = (REPO_ROOT / SCHEMA_RELATIVE_PATH).read_bytes().decode("utf-8")
    assert committed.replace("\r\n", "\n") == schema_json()


def test_schema_cli_write_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "s.json"
    assert main(["--check", str(out)]) == 1
    assert main(["--write", str(out)]) == 0
    assert main(["--check", str(out)]) == 0
    out.write_text("{}\n", encoding="utf-8")
    assert main(["--check", str(out)]) == 1
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Headwaters rules.yaml"


def test_default_file_content_is_plain_yaml(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    ensure_rules_file(target)
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["version"] == 1
````

## tests/test_rules_edit.py

````python
"""HDW-118: targeted, validated, atomic edits of rules.yaml."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from headwaters.core.rules_edit import set_setting
from headwaters.core.rules_io import ConfigError, load_rules


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_creates_the_default_file_on_first_use(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    previous, new = set_setting("default_scope", "local", target)
    assert (previous, new) == ("auto", "local")
    assert load_rules(target).default_scope == "local"


def test_replaces_the_value_and_keeps_comment_and_neighbours(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "# top\ndefault_scope: auto  # why\nairplane: true\n")
    set_setting("default_scope", "free", target)
    assert target.read_text(encoding="utf-8") == (
        "# top\ndefault_scope: free  # why\nairplane: true\n"
    )


def test_quoted_values_are_replaced(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", 'default_scope: "auto"\n')
    set_setting("default_scope", "cloud", target)
    assert load_rules(target).default_scope == "cloud"


def test_bool_settings_render_lowercase(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "private_lock: false\n")
    assert set_setting("private_lock", True, target) == (False, True)
    assert target.read_text(encoding="utf-8") == "private_lock: true\n"


def test_appends_when_the_key_is_missing_even_without_trailing_newline(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "version: 1")
    set_setting("private_lock", True, target)
    assert target.read_text(encoding="utf-8") == "version: 1\nprivate_lock: true\n"


def test_no_write_when_nothing_changes(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\n")
    before = target.stat().st_mtime_ns
    set_setting("default_scope", "auto", target)
    assert target.stat().st_mtime_ns == before


def test_unknown_key_and_unsafe_value_are_refused(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "version: 1\n")
    with pytest.raises(ValueError, match="cannot be edited"):
        set_setting("telemetry", True, target)
    with pytest.raises(ValueError, match="unsupported value"):
        set_setting("default_scope", "auto\nairplane: true", target)
    assert target.read_text(encoding="utf-8") == "version: 1\n"


def test_invalid_scope_value_is_rejected_and_file_untouched(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\n")
    with pytest.raises(ConfigError, match="default_scope"):
        set_setting("default_scope", "nowhere", target)
    assert target.read_text(encoding="utf-8") == "default_scope: auto\n"


def test_unparseable_existing_value_is_not_guessed(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: [auto]\n")
    with pytest.raises(ConfigError):
        set_setting("default_scope", "free", target)
    assert target.read_text(encoding="utf-8") == "default_scope: [auto]\n"


def test_duplicate_key_is_refused(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\ndefault_scope: free\n")
    with pytest.raises(ConfigError):
        set_setting("default_scope", "local", target)


def test_an_invalid_file_is_never_rewritten(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\nairplane: maybe\n")
    with pytest.raises(ConfigError, match="airplane"):
        set_setting("default_scope", "local", target)
    assert "default_scope: auto" in target.read_text(encoding="utf-8")


def test_a_failed_write_leaves_no_temp_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\n")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        set_setting("default_scope", "local", target)
    assert [p.name for p in tmp_path.iterdir()] == ["rules.yaml"]
    assert target.read_text(encoding="utf-8") == "default_scope: auto\n"
````

## tests/test_rules_watch.py

````python
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError
from headwaters.core.rules_watch import RulesManager


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[RulesManager]:
    reloads: list[Rules] = []
    errors: list[ConfigError] = []
    m = RulesManager(
        tmp_path / "rules.yaml",
        debounce_s=0.1,
        on_reload=reloads.append,
        on_error=errors.append,
    )
    m.reloads = reloads  # type: ignore[attr-defined]
    m.errors = errors  # type: ignore[attr-defined]
    m.start()
    yield m
    m.stop()


def test_writes_defaults_on_first_run(manager: RulesManager) -> None:
    assert manager.path.is_file()
    assert manager.current == Rules()
    assert manager.last_error is None


def test_reload_applies_valid_edit(manager: RulesManager) -> None:
    manager.path.write_text("strategy: quality_first\n", encoding="utf-8")
    assert wait_for(lambda: manager.current.strategy == "quality_first")
    assert manager.last_error is None


def test_invalid_edit_keeps_last_good_then_recovers(manager: RulesManager) -> None:
    manager.path.write_text("strategy: quality_first\n", encoding="utf-8")
    assert wait_for(lambda: manager.current.strategy == "quality_first")

    manager.path.write_text("strategy: nonsense\n", encoding="utf-8")
    assert wait_for(lambda: manager.last_error is not None)
    assert manager.current.strategy == "quality_first"  # fallback to last good
    assert "strategy" in str(manager.last_error)

    manager.path.write_text("strategy: primary_first\n", encoding="utf-8")
    assert wait_for(lambda: manager.current.strategy == "primary_first")
    assert manager.last_error is None


def test_rapid_writes_are_debounced(manager: RulesManager) -> None:
    for i in range(5):
        manager.path.write_text(f"walls:\n  cascade_if_wait_over_s: {i + 1}\n", encoding="utf-8")
        time.sleep(0.01)
    assert wait_for(lambda: manager.current.walls.cascade_if_wait_over_s == 5)
    time.sleep(0.4)
    assert len(manager.reloads) <= 2  # type: ignore[attr-defined]


def test_invalid_at_startup_uses_defaults(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("gateway:\n  port: 0\n", encoding="utf-8")
    m = RulesManager(target)
    assert m.current == Rules()
    assert m.last_error is not None
    assert "gateway.port" in str(m.last_error)


def test_manual_reload_and_idempotent_start_stop(tmp_path: Path) -> None:
    m = RulesManager(tmp_path / "rules.yaml")
    m.start()
    m.start()
    m.path.write_text("advice: inline\n", encoding="utf-8")
    assert m.reload() is True
    assert m.current.advice == "inline"
    m.stop()
    m.stop()
````

## tests/test_sign_catalog_script.py

````python
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from headwaters.core.catalog import trust

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sign_catalog.py"


def load_script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("sign_catalog", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_keygen_sign_verify_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = load_script()
    keyfile = tmp_path / "key.txt"
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(b'{"a": 1}')
    assert script.main(["keygen", str(keyfile)]) == 0
    public = capsys.readouterr().out.split(": ")[1].strip()
    assert script.main(["keygen", str(keyfile)]) == 1  # never overwrites
    assert script.main(["sign", str(keyfile), str(catalog)]) == 0
    assert script.main(["verify", public, str(catalog)]) == 0
    trust.verify(catalog.read_bytes(), (tmp_path / "catalog.json.sig").read_bytes(), public)
    catalog.write_bytes(b'{"a": 2}')
    assert script.main(["verify", public, str(catalog)]) == 1
````

## tests/test_state.py

````python
from pathlib import Path

import pytest

from headwaters.core.state import LATEST_VERSION, Database, open_database, scrub
from headwaters.core.state_limits import (
    active_walls,
    add_quota_usage,
    clear_wall,
    get_quota,
    record_wall,
    reset_quota_window,
    set_quota_limit,
)
from headwaters.core.state_misc import kv_get
from headwaters.core.state_models import (
    ModelRow,
    ScoreRow,
    get_model,
    get_score,
    list_models,
    set_score,
    upsert_model,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state" / "test.db")


EXPECTED_TABLES = {
    "models",
    "scores",
    "walls",
    "quotas",
    "sessions",
    "decisions",
    "outcomes",
    "jobs",
    "handoffs",
    "kv",
    "schema_migrations",
}


def test_schema_and_pragmas(db: Database) -> None:
    with db.read() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables >= EXPECTED_TABLES
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
        cols = {r["name"] for r in c.execute("PRAGMA table_info(decisions)")}
        assert {"scope", "strategy", "locks_applied"} <= cols
        ocols = {r["name"] for r in c.execute("PRAGMA table_info(outcomes)")}
        assert {"tokens_in", "cost_usd", "counterfactual_usd", "verify_result"} <= ocols


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    d = Database(tmp_path / "m.db")
    assert d.schema_version() == 0
    assert d.migrate() == list(range(1, LATEST_VERSION + 1))
    assert d.migrate() == []
    assert d.schema_version() == LATEST_VERSION
    upsert_model(d, ModelRow("m", "local", "ollama", ["text"]))
    assert d.migrate() == []  # existing data untouched
    assert get_model(d, "m") is not None
    with d.read() as c:
        assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == LATEST_VERSION


def test_failed_write_rolls_back(db: Database) -> None:
    def failing_write() -> None:
        with db.write() as c:
            c.execute("INSERT INTO kv (key, value) VALUES ('a', '1')")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        failing_write()
    assert kv_get(db, "a") is None


def test_models_and_scores(db: Database) -> None:
    upsert_model(
        db, ModelRow("qwen", "local", "ollama", ["text"], tools_ok=True, ctx=8192, arch={"l": 1})
    )
    upsert_model(db, ModelRow("qwen", "local", "ollama", ["text", "vision"], json_ok=False))
    m = get_model(db, "qwen")
    assert m is not None
    assert (m.modalities, m.tools_ok, m.json_ok, m.ctx) == (["text", "vision"], None, False, None)
    upsert_model(db, ModelRow("gpt", "paid", "openai", ["text"]))
    assert [x.id for x in list_models(db)] == ["gpt", "qwen"]
    assert [x.id for x in list_models(db, "local")] == ["qwen"]
    assert get_model(db, "none") is None

    set_score(db, ScoreRow("qwen", "code", seed=70.0))
    set_score(db, ScoreRow("qwen", "code", seed=70.0, learned_ema=72.5, n_samples=3))
    assert get_score(db, "qwen", "code") == ScoreRow("qwen", "code", 70.0, None, 72.5, 3)
    assert get_score(db, "qwen", "chat") is None


def test_walls(db: Database) -> None:
    w = record_wall(db, "prov", "rate_limit", until=100.0, error="429")
    assert w.consecutive_count == 1
    w = record_wall(db, "prov", "rate_limit", until=500.0, error="429 again")
    assert (w.consecutive_count, w.until, w.last_error) == (2, 500.0, "429 again")
    record_wall(db, "prov", "auth", until=None)
    assert {x.kind for x in active_walls(db, now=200.0)} == {"rate_limit", "auth"}
    assert {x.kind for x in active_walls(db, now=600.0)} == {"auth"}  # hard wall stays
    assert clear_wall(db, "prov", "auth") == 1
    assert clear_wall(db, "prov") == 1
    assert active_walls(db) == []


def test_quotas(db: Database) -> None:
    assert get_quota(db, "m", "day") is None
    assert add_quota_usage(db, "m", "day").count == 1
    assert add_quota_usage(db, "m", "day", 4).count == 5
    set_quota_limit(db, "m", "day", limit=50, soft_exhausted=True)
    q = get_quota(db, "m", "day")
    assert q is not None
    assert (q.count, q.limit, q.soft_exhausted) == (5, 50, True)
    assert reset_quota_window(db, "day") == 1
    q = get_quota(db, "m", "day")
    assert q is not None
    assert (q.count, q.soft_exhausted, q.limit) == (0, False, 50)


def test_scrub_removes_text_keys_recursively() -> None:
    data = {"a": 1, "Prompt": "x", "n": {"content": "y", "ok": [{"text": "z", "k": 2}]}}
    assert scrub(data) == {"a": 1, "n": {"ok": [{"k": 2}]}}


def test_no_text_columns_for_prompts(db: Database) -> None:
    with db.read() as c:
        for table in EXPECTED_TABLES:
            for col in c.execute(f"PRAGMA table_info({table})"):
                assert col["name"] not in {"prompt", "output", "content", "messages", "text"}
````

## tests/test_state_records.py

````python
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from headwaters.core.state import LATEST_VERSION, Database, open_database
from headwaters.core.state_decisions import (
    DecisionRow,
    OutcomeRow,
    get_decision,
    get_outcome,
    insert_decision,
    recent_decisions,
    save_outcome,
    set_user_feedback,
)
from headwaters.core.state_limits import get_quota
from headwaters.core.state_misc import (
    add_handoff,
    create_job,
    get_handoff,
    get_job,
    get_session,
    kv_delete,
    kv_get,
    kv_set,
    latest_handoff_id,
    pin_session,
    update_job,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state" / "test.db")


def test_decisions_and_outcomes(db: Database) -> None:
    did = insert_decision(
        db,
        DecisionRow(
            surface="gateway",
            scope="free",
            strategy="cost_saver",
            locks_applied=["project_rule"],
            requirements={"task": "code", "prompt": "SECRET USER TEXT"},
            chosen="qwen",
            alternatives=["a", "b"],
            floor=75.0,
            score=80.0,
            reasons=["cheapest"],
        ),
    )
    d = get_decision(db, did)
    assert d is not None
    assert (d.scope, d.strategy, d.locks_applied) == ("free", "cost_saver", ["project_rule"])
    assert d.requirements == {"task": "code"}  # prompt text never stored
    assert get_decision(db, 9999) is None
    assert get_outcome(db, did) is None

    save_outcome(db, OutcomeRow(did, "ok", latency_ms=120, tokens_in=10, tokens_out=20))
    save_outcome(db, OutcomeRow(did, "ok", cost_usd=0.0, verify_result="pass"))
    o = get_outcome(db, did)
    assert o is not None
    assert (o.counterfactual_usd, o.tokens_in, o.verify_result) == (None, None, "pass")
    assert set_user_feedback(db, did, "good")
    assert not set_user_feedback(db, 9999, "good")
    insert_decision(db, DecisionRow("mcp", "auto", "quality_first"))
    assert [x.surface for x in recent_decisions(db, 1)] == ["mcp"]


def test_sessions_jobs_handoffs_kv(db: Database) -> None:
    assert get_session(db, "s") is None
    s = pin_session(db, "s", "m1", "local")
    assert (s.pinned_model, s.last_switch) == ("m1", None)
    s = pin_session(db, "s", "m2", "cloud", handoff_packet_id=3)
    assert (s.pinned_model, s.handoff_packet_id, s.last_switch is not None) == ("m2", 3, True)

    create_job(db, "j1")
    assert update_job(db, "j1", status="running", progress=0.5)
    assert update_job(db, "j1", result_ref="r")
    j = get_job(db, "j1")
    assert j is not None
    assert (j.status, j.progress, j.result_ref) == ("running", 0.5, "r")
    assert not update_job(db, "nope", status="x")
    assert get_job(db, "nope") is None

    assert latest_handoff_id(db, "s") is None
    first = add_handoff(db, "s", {"goal": "g", "messages": ["private"]})
    second = add_handoff(db, "s", {"goal": "h"})
    assert latest_handoff_id(db, "s") == second
    assert get_handoff(db, first) == {"goal": "g"}
    assert get_handoff(db, 999) is None

    assert kv_get(db, "k", "dflt") == "dflt"
    kv_set(db, "k", {"a": 1})
    kv_set(db, "k", [1, 2])
    assert kv_get(db, "k") == [1, 2]
    assert kv_delete(db, "k")
    assert not kv_delete(db, "k")


WORKER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from headwaters.core.state import Database, open_database
    from headwaters.core.state_decisions import DecisionRow, insert_decision
    from headwaters.core.state_limits import add_quota_usage
    from headwaters.core.state_misc import kv_set

    path, tag, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    open_database(path)  # concurrent first-run migrate must be safe
    db = Database(path)
    for i in range(n):
        insert_decision(db, DecisionRow("gateway", "auto", "cost_saver", session=tag))
        add_quota_usage(db, "shared", "day")
        kv_set(db, f"{tag}-{i}", i)
    """
)


def test_two_processes_write_concurrently(tmp_path: Path) -> None:
    path = tmp_path / "shared.db"
    n = 60
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(path), tag, str(n)],
            stderr=subprocess.PIPE,
            text=True,
        )
        for tag in ("a", "b")
    ]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err

    d = Database(path)
    assert d.schema_version() == LATEST_VERSION
    with d.read() as c:
        assert c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2 * n
        for tag in ("a", "b"):
            q = "SELECT COUNT(*) FROM decisions WHERE session = ?"
            assert c.execute(q, (tag,)).fetchone()[0] == n
        assert c.execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 2 * n
        assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == LATEST_VERSION
    quota = get_quota(d, "shared", "day")
    assert quota is not None
    assert quota.count == 2 * n  # atomic increments: none lost
````

## tests/test_stdio.py

````python
import io
import sys

import pytest

from headwaters.core.stdio import StdoutViolationError, configure_utf8, guard_stdout


def test_guard_catches_stray_print() -> None:
    with guard_stdout() as emit:
        emit('{"jsonrpc":"2.0"}\n')
        with pytest.raises(StdoutViolationError):
            print("debug junk")


def test_guard_catches_sys_stdout_write_and_buffer() -> None:
    with guard_stdout():
        with pytest.raises(StdoutViolationError):
            sys.stdout.write("oops")
        with pytest.raises(StdoutViolationError):
            sys.stdout.buffer.write(b"oops")  # type: ignore[arg-type]


def test_protocol_writer_passes_through_and_restores() -> None:
    real = io.StringIO()
    original = sys.stdout
    sys.stdout = real
    try:
        with guard_stdout() as emit:
            emit('{"id":1}\n')
            print("", end="")  # empty writes are harmless
            assert sys.stdout is not real
            assert sys.stdout.encoding == "utf-8"
            assert not sys.stdout.isatty()
            assert sys.stdout.writable()
            sys.stdout.flush()
        assert sys.stdout is real
    finally:
        sys.stdout = original
    assert real.getvalue() == '{"id":1}\n'


def test_non_strict_diverts_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    with guard_stdout(strict=False) as emit:
        print("stray")
        emit("proto\n")
    out = capsys.readouterr()
    assert out.out == "proto\n"
    assert "stray" in out.err


def test_configure_utf8_sets_env_and_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    fake_out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    fake_err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    fake_in = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", fake_err)
    monkeypatch.setattr(sys, "stdin", fake_in)
    configure_utf8()
    import os

    assert os.environ["PYTHONUTF8"] == "1"
    assert fake_out.encoding == "utf-8"
    assert fake_err.encoding == "utf-8"
    assert fake_in.encoding == "utf-8"
    fake_out.write("héllo ✓")
````

## tests/test_verify_basic.py

````python
from __future__ import annotations

import pytest

from headwaters.core.catalog.schema import NamedPattern
from headwaters.core.plugins import check_plugin
from headwaters.core.verify_basic import BasicVerifier

ALIASES = {"py": "python", "ts": "typescript", "js": "javascript", "rs": "rust", "c++": "cpp"}


@pytest.fixture
def verifier() -> BasicVerifier:
    # Test data only: the real patterns live in catalog/seed_catalog.json.
    patterns = [NamedPattern(name="todo-marker", regex=r"\bTODO\b")]
    return BasicVerifier(patterns, fence_aliases=ALIASES)


def fenced(lang: str, body: str) -> str:
    return f"Here you go:\n```{lang}\n{body}\n```\n"


def test_is_a_verifier_plugin(verifier: BasicVerifier) -> None:
    check_plugin("VerifierPlugin", verifier)
    assert verifier.verify("plain prose") == []
    assert verifier.verify(fenced("python", "def f(:")) != []


GOOD = {
    "python": "def f(x):\n    return x + 1",
    "javascript": "const f = (x) => x + 1;",
    "typescript": "const f = (x: number): number => x + 1;",
    "go": "package main\n\nfunc f(x int) int { return x + 1 }",
    "rust": "fn f(x: i32) -> i32 { x + 1 }",
    "java": "class A { int f(int x) { return x + 1; } }",
    "csharp": "class A { int F(int x) { return x + 1; } }",
    "c": "int f(int x) { return x + 1; }",
    "cpp": "int f(int x) { return x + 1; }",
    "json": '{"a": [1, 2]}',
    "yaml": "a:\n  - 1\n  - 2",
}
BAD = {
    "python": "def f(:\n    return",
    "javascript": "const f = (x => {;",
    "typescript": "const f = (x: number: number => ;",
    "go": "package main\n\nfunc f( { ",
    "rust": "fn f( -> { let = ;",
    "java": "class A { int f( { }",
    "csharp": "class A { int F( { }",
    "c": "int f(int x { return ;",
    "cpp": "int f(int x { return ;",
    "json": '{"a": [1, 2,]}',
    "yaml": "a: [1, 2\nb: {",
}


@pytest.mark.parametrize("lang", sorted(GOOD))
def test_parse_passes_valid_code(verifier: BasicVerifier, lang: str) -> None:
    report = verifier.verify_report(fenced(lang, GOOD[lang]))
    assert report.passed, report.reasons


@pytest.mark.parametrize("lang", sorted(BAD))
def test_parse_fails_broken_code(verifier: BasicVerifier, lang: str) -> None:
    report = verifier.verify_report(fenced(lang, BAD[lang]))
    assert not report.passed
    assert report.reasons[0].code in {"syntax_error", "json_invalid"}
    assert report.reasons[0].line is not None


def test_fence_alias_and_line_number(verifier: BasicVerifier) -> None:
    report = verifier.verify_report(fenced("py", "x = 1\ny = (\n"))
    assert [r.code for r in report.reasons] == ["syntax_error"]
    assert report.reasons[0].line is not None


def test_python_uses_ast_for_semantic_syntax_errors(verifier: BasicVerifier) -> None:
    # tree-sitter accepts this call; ast.parse rejects it.
    report = verifier.verify_report(fenced("python", "f(a=1, 2)"))
    assert [r.code for r in report.reasons] == ["syntax_error"]


def test_unknown_language_is_not_parsed(verifier: BasicVerifier) -> None:
    assert verifier.verify_report(fenced("brainfuck", "+++[->+<")).passed


def test_unfenced_code_with_language_hint(verifier: BasicVerifier) -> None:
    broken = verifier.verify_report("def f(:\n", language="python")
    assert [r.code for r in broken.reasons] == ["syntax_error"]
    assert verifier.verify_report("def f(): pass", language="python").passed


def test_indented_python_snippet(verifier: BasicVerifier) -> None:
    assert verifier.verify_report(fenced("python", "    x = 1\n    y = 2")).passed
````

## tests/test_verify_diff.py

````python
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
````

## tests/test_verify_scan.py

````python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from headwaters.core.catalog.schema import NamedPattern
from headwaters.core.verify_basic import BasicVerifier, normalize_eol

# Test data only: the real patterns live in catalog/seed_catalog.json.
PATTERNS = [
    NamedPattern(name="todo-marker", regex=r"\bTODO\b"),
    NamedPattern(name="rest-of-code", regex=r"(?i)\.\.\.\s*rest of (the )?code"),
    NamedPattern(name="existing-code", regex=r"(?i)(//|#)\s*existing code"),
]


@pytest.fixture
def verifier() -> BasicVerifier:
    return BasicVerifier(PATTERNS)


def fenced(lang: str, body: str) -> str:
    return f"Here you go:\n```{lang}\n{body}\n```\n"


def codes(verifier: BasicVerifier, text: str) -> set[str]:
    return {r.code for r in verifier.verify_report(text).reasons}


@pytest.mark.parametrize(
    "body",
    ["def f():\n    # TODO implement", "def f():\n    ... rest of code", "x = 1\n# existing code"],
)
def test_placeholder_scan_fails(verifier: BasicVerifier, body: str) -> None:
    report = verifier.verify_report(fenced("python", body))
    assert "placeholder" in {r.code for r in report.reasons}
    assert "'" in report.reasons[0].message  # names the pattern, not the text


def test_placeholder_scan_ignores_prose_and_clean_code(verifier: BasicVerifier) -> None:
    assert verifier.verify_report("TODO: later, I will explain.").passed
    assert verifier.verify_report(fenced("python", "x = 1")).passed


def test_no_patterns_skips_the_scan() -> None:
    report = BasicVerifier().verify_report(fenced("python", "# TODO"))
    assert report.passed
    assert next(c for c in report.checks if c.name == "placeholders").status == "skipped"


def test_truncated_fence(verifier: BasicVerifier) -> None:
    report = verifier.verify_report("```python\ndef f():\n    return 1\n")
    assert [r.code for r in report.reasons] == ["truncated_fence"]
    assert report.reasons[0].line == 1


def test_longer_fence_and_tilde_fences(verifier: BasicVerifier) -> None:
    assert verifier.verify_report("````md\n```python\nx = 1\n```\n````\n").passed
    assert verifier.verify_report("~~~python\nx = 1\n~~~\n").passed
    assert codes(verifier, "~~~python\nx = 1\n```\n") == {"truncated_fence"}


def test_report_never_contains_candidate_text(verifier: BasicVerifier) -> None:
    secret = "sk-SUPERSECRETVALUE123"
    report = verifier.verify_report(fenced("python", f"x = '{secret}'\n# TODO"))
    assert not report.passed
    assert secret not in json.dumps(report.to_dict())


def test_crlf_candidate_is_normalized(verifier: BasicVerifier) -> None:
    text = fenced("python", "def f():\n    return 1").replace("\n", "\r\n")
    assert verifier.verify_report(text).passed
    assert normalize_eol("a\r\nb\rc\n") == "a\nb\nc\n"


def test_from_catalog_uses_seed_patterns() -> None:
    from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog

    root = Path(__file__).resolve().parents[1]
    seeded = BasicVerifier.from_catalog(load_catalog(root / SEED_RELATIVE_PATH))
    assert "placeholder" in codes(seeded, fenced("python", "key = 'YOUR_API_KEY'"))
    assert seeded.verify_report(fenced("py", "x = 1")).passed
````

## tests/test_verify_schema.py

````python
from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.verify_basic import BasicVerifier

SCHEMA = {
    "type": "object",
    "required": ["name", "n"],
    "properties": {"name": {"type": "string"}, "n": {"type": "integer"}},
}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
    {"name": "noop", "input_schema": {"type": "object"}},
]


def codes(**kw: Any) -> list[str]:
    text = kw.pop("text", "")
    return [r.code for r in BasicVerifier().verify_report(text, **kw).reasons]


def test_json_schema_pass() -> None:
    assert codes(text='{"name": "a", "n": 1}', json_schema=SCHEMA) == []
    assert codes(text='```json\n{"name": "a", "n": 1}\n```', json_schema=SCHEMA) == []


def test_json_schema_violation_reports_path_not_value() -> None:
    report = BasicVerifier().verify_report('{"name": "a", "n": "secret-x"}', json_schema=SCHEMA)
    assert [r.code for r in report.reasons] == ["json_schema_violation"]
    assert "n:" in report.reasons[0].message
    assert "secret-x" not in report.reasons[0].message


def test_json_schema_missing_invalid_and_bad_schema() -> None:
    assert codes(text="just words", json_schema=SCHEMA) == ["json_not_found"]
    assert codes(text='{"name": ', json_schema=SCHEMA) == ["json_invalid"]
    assert codes(text="{}", json_schema={"type": 12}) == ["json_schema_invalid"]


def test_fenced_json_syntax_error_reported_once() -> None:
    assert codes(text='```json\n{"name": }\n```', json_schema=SCHEMA) == ["json_invalid"]


def test_tool_calls_valid_openai_and_anthropic() -> None:
    calls = [
        {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
        {"name": "noop", "input": {}},
    ]
    assert codes(tool_calls=calls, tools=TOOLS) == []


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ({"function": {"name": "delete_all", "arguments": "{}"}}, "tool_unknown"),
        ({"function": {"name": "read_file", "arguments": "{oops"}}, "tool_args_invalid_json"),
        ({"function": {"name": "read_file", "arguments": "[1]"}}, "tool_args_not_object"),
        ({"function": {"name": "read_file", "arguments": "{}"}}, "tool_args_schema"),
        ({"function": {"name": "read_file", "arguments": '{"path": 3}'}}, "tool_args_schema"),
        (
            {"function": {"name": "read_file", "arguments": '{"path": "a", "x": 1}'}},
            "tool_args_schema",
        ),
        ({"function": {"arguments": "{}"}}, "tool_name_missing"),
    ],
)
def test_tool_calls_invalid(call: dict[str, Any], expected: str) -> None:
    assert codes(tool_calls=[call], tools=TOOLS) == [expected]


def test_tool_calls_without_tool_list_only_check_arguments() -> None:
    assert codes(tool_calls=[{"function": {"name": "any", "arguments": '{"a": 1}'}}]) == []
    assert codes(tool_calls=[{"function": {"name": "any", "arguments": "{"}}]) == [
        "tool_args_invalid_json"
    ]


def test_invalid_tool_schema_is_skipped_not_failed() -> None:
    tools = [{"name": "t", "input_schema": {"type": 12}}]
    report = BasicVerifier().verify_report("", tool_calls=[{"name": "t", "input": {}}], tools=tools)
    assert report.passed
    assert next(c for c in report.checks if c.name == "tool_calls").detail
````
