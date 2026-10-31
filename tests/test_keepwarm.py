"""HDW-201: keep-warm pings Ollama's native API only, and only when it is safe to."""

from __future__ import annotations

import re
from typing import Any

import pytest

from headwaters.core.catalog.http import SourceError
from headwaters.core.hardware.state import BatteryState, GpuBusy
from headwaters.core.keepwarm import KeepWarm, parse_duration_s
from headwaters.core.rules import Rules


class FakeHttp:
    def __init__(self, loaded: list[str] | None = None, *, ps_down: bool = False) -> None:
        self.loaded = loaded or []
        self.ps_down = ps_down
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.fail_post = False

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, url, body))
        if url.endswith("/api/ps"):
            if self.ps_down:
                raise SourceError("down")
            return {"models": [{"name": n} for n in self.loaded]}
        if self.fail_post:
            raise SourceError("boom")
        return {"done": True}

    @property
    def posts(self) -> list[tuple[str, str, dict[str, Any] | None]]:
        return [c for c in self.calls if c[0] == "POST"]


class State:
    def __init__(self, *, busy: bool = False, battery: bool = False) -> None:
        self.gpu_busy = GpuBusy(busy, 0.0, 0.0)
        self.battery = BatteryState(True, battery, 50.0)


def _rules(**local: Any) -> Rules:
    return Rules.model_validate({"tiers": {"local": local}})


def _warm(rules: Rules, http: FakeHttp, state: State | None = None, patterns: Any = ()) -> KeepWarm:
    return KeepWarm(
        rules,
        http,
        remote_patterns=patterns,
        hardware=lambda: state,  # type: ignore[arg-type,return-value]
    )


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30m", 1800.0), ("1h30m", 5400.0), ("90", 90.0), ("5s", 5.0), ("500ms", 0.5), ("2h", 7200.0)],
)
def test_parse_duration(text: str, seconds: float) -> None:
    assert parse_duration_s(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "-1", "-1m", "abc", "5x", "m5", "5m junk"])
def test_parse_duration_rejects_the_rest(text: str) -> None:
    assert parse_duration_s(text) is None


def test_interval_is_half_the_keep_alive_window() -> None:
    assert _warm(_rules(keep_alive="30m"), FakeHttp()).interval_s() == 900.0
    assert _warm(_rules(keep_alive="10s"), FakeHttp()).interval_s() == 30.0  # floor
    assert _warm(_rules(keep_alive="-1"), FakeHttp()).interval_s() == 900.0  # default


def test_auto_pings_what_is_already_loaded_with_an_empty_chat() -> None:
    http = FakeHttp(["qwen:14b"])
    result = _warm(_rules(), http).tick()
    assert result.action == "pinged"
    assert result.models == ("qwen:14b",)
    ((method, url, body),) = http.posts
    assert (method, url) == ("POST", "http://localhost:11434/api/chat")
    assert body == {"model": "qwen:14b", "messages": [], "stream": False, "keep_alive": "30m"}


def test_auto_never_loads_a_model_on_its_own() -> None:
    http = FakeHttp([])
    result = _warm(_rules(), http).tick()
    assert (result.action, result.reason) == ("skipped", "nothing_loaded")
    assert http.posts == []


def test_explicit_model_is_loaded_when_nothing_else_is() -> None:
    http = FakeHttp([])
    result = _warm(_rules(keep_warm_model="qwen:14b"), http).tick()
    assert result.models == ("qwen:14b",)


def test_explicit_model_never_evicts_the_one_in_use() -> None:
    http = FakeHttp(["other:7b"])
    result = _warm(_rules(keep_warm_model="qwen:14b"), http).tick()
    assert (result.action, result.reason) == ("skipped", "other_model_loaded")
    assert http.posts == []


@pytest.mark.parametrize("setting", ["off", "none", "false", ""])
def test_can_be_switched_off(setting: str) -> None:
    http = FakeHttp(["qwen:14b"])
    result = _warm(_rules(keep_warm_model=setting), http).tick()
    assert result.reason == "disabled"
    assert http.calls == []


def test_paused_while_the_gpu_is_busy_or_on_battery() -> None:
    http = FakeHttp(["qwen:14b"])
    assert _warm(_rules(), http, State(busy=True)).tick().reason == "gpu_busy"
    assert _warm(_rules(), http, State(battery=True)).tick().reason == "on_battery"
    assert http.calls == []


def test_no_ping_when_keep_alive_does_not_expire() -> None:
    http = FakeHttp(["qwen:14b"])
    assert _warm(_rules(keep_alive="-1"), http).tick().reason == "keep_alive_not_timed"
    assert _warm(_rules(keep_alive="0"), http).tick().reason == "keep_alive_not_timed"
    assert http.calls == []


def test_never_talks_to_a_non_loopback_host() -> None:
    http = FakeHttp(["qwen:14b"])
    result = _warm(_rules(host="http://10.0.0.5:11434"), http).tick()
    assert result.reason == "host_not_loopback"
    assert http.calls == []


def test_cloud_proxied_models_are_never_pinged() -> None:
    patterns = (re.compile(r"(?:^|[:/-])cloud$"),)
    http = FakeHttp(["gpt-oss:120b-cloud", "qwen:14b"])
    result = _warm(_rules(), http, patterns=patterns).tick()
    assert result.models == ("qwen:14b",)
    explicit = _warm(_rules(keep_warm_model="gpt-oss:120b-cloud"), FakeHttp(), patterns=patterns)
    assert explicit.tick().reason == "remote_model"


def test_unreachable_ollama_is_a_skip_not_a_crash() -> None:
    result = _warm(_rules(), FakeHttp(ps_down=True)).tick()
    assert (result.action, result.reason) == ("skipped", "ollama_unreachable")


def test_a_failed_ping_is_reported() -> None:
    http = FakeHttp(["qwen:14b"])
    http.fail_post = True
    result = _warm(_rules(), http).tick()
    assert (result.action, result.reason) == ("error", "ping_failed")
