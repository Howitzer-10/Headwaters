"""HDW-505: the one native ``/api/pull`` path the wizard's step 2 drives.

The point of these tests is the refusal, not the happy path: a cloud-proxied tag is refused
*before* a socket is opened (D13), a non-loopback host is refused, and a machine with no Ollama
running produces an error the person can read rather than a pull that silently does nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError

import httpx
import pytest

from headwaters.core.executor.errors import LocalScopeViolation, ProviderError
from headwaters.core.executor.ollama import PullLine, cloud_tag, list_installed, pull_stream
from headwaters.core.ollama_remote import remote_patterns_from
from tests.wizard_support import DeadTransport, seed_catalog

HOST = "http://127.0.0.1:11434"


class SpyTransport(httpx.BaseTransport):
    """Records every request, and answers with whatever the test scripted."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler or (lambda request: httpx.Response(200, json={}))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def stream_response(*lines: dict[str, object], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content="\n".join(json.dumps(line) for line in lines).encode() + b"\n",
        headers={"content-type": "application/x-ndjson"},
    )


# --------------------------------------------------------------------------- D13: cloud tags


@pytest.mark.parametrize("tag", ["gpt-oss:120b-cloud", "qwen3:14b-cloud", "kimi-k2.7:cloud"])
def test_a_cloud_tag_is_refused_before_a_socket_is_opened(tag: str) -> None:
    spy = SpyTransport()
    with pytest.raises(LocalScopeViolation):
        list(pull_stream(HOST, tag, transport=spy))
    assert spy.requests == [], "a cloud tag must not reach the network at all"


def test_the_catalogs_own_remote_pattern_is_what_refuses_the_tag() -> None:
    catalog = seed_catalog()
    patterns = remote_patterns_from(catalog)
    assert cloud_tag("gpt-oss:120b-cloud", patterns) is not None
    assert cloud_tag("qwen3:14b", patterns) is None, "a local tag is pullable"
    with pytest.raises(LocalScopeViolation):
        list(
            pull_stream(
                HOST, "gpt-oss:120b-cloud", transport=SpyTransport(), remote_patterns=patterns
            )
        )


def test_no_local_tag_is_ever_refused_as_remote() -> None:
    for tag in ("qwen3:14b", "qwen3:14b-instruct-q4_K_M", "llama3.1:8b", "cloud-thing:1.0"):
        assert cloud_tag(tag) is None, f"{tag} is a local tag and must remain pullable"


def test_a_non_loopback_host_is_refused_before_any_request() -> None:
    spy = SpyTransport()
    with pytest.raises(LocalScopeViolation):
        list(pull_stream("http://example.com:11434", "qwen3:14b", transport=spy))
    assert spy.requests == []


# --------------------------------------------------------------------------- the stream itself


def test_a_pull_streams_ollama_lines_as_pull_lines() -> None:
    spy = SpyTransport(
        lambda request: stream_response(
            {"status": "pulling manifest"},
            {"status": "pulling sha256:abc", "digest": "sha256:abc", "total": 100, "completed": 40},
            {"status": "success"},
        )
    )
    lines = list(pull_stream(HOST, "qwen3:14b", transport=spy))
    assert [line.status for line in lines] == ["pulling manifest", "pulling sha256:abc", "success"]
    assert lines[1].total == 100
    assert lines[1].completed == 40
    assert spy.requests[0].url.path == "/api/pull"
    sent = json.loads(spy.requests[0].content)
    assert sent["model"] == "qwen3:14b"
    assert sent["stream"] is True


def test_a_refused_connection_is_an_error_the_person_is_told() -> None:
    with pytest.raises(ProviderError) as raised:
        list(pull_stream(HOST, "qwen3:14b", transport=DeadTransport()))
    assert raised.value.kind == "connection"


def test_an_error_status_carries_ollamas_own_words() -> None:
    spy = SpyTransport(lambda request: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(ProviderError) as raised:
        list(pull_stream(HOST, "qwen3:14b", transport=spy))
    assert raised.value.status == 404
    assert "model not found" in str(raised.value.body)


def test_an_error_line_mid_stream_stops_the_pull() -> None:
    spy = SpyTransport(
        lambda request: stream_response(
            {"status": "pulling manifest"}, {"error": "no space left on device"}
        )
    )
    with pytest.raises(ProviderError) as raised:
        list(pull_stream(HOST, "qwen3:14b", transport=spy))
    assert raised.value.kind == "stream_error"
    assert "no space left" in str(raised.value.body)


def test_installed_tags_are_the_names_ollama_reports() -> None:
    spy = SpyTransport(
        lambda request: httpx.Response(
            200, json={"models": [{"name": "qwen3:14b"}, {"model": "x"}]}
        )
    )
    assert list_installed(HOST, transport=spy) == ("qwen3:14b", "x")
    assert spy.requests[0].url.path == "/api/tags"


def test_a_pull_line_is_frozen_so_a_progress_snapshot_cannot_be_edited() -> None:
    line = PullLine("pulling", "sha256:abc", 10, 5)
    with pytest.raises(FrozenInstanceError):
        line.completed = 10  # type: ignore[misc]
