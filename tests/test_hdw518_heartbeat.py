"""HDW-518: the opt-in keep-warm heartbeat on top of HDW-201's tick.

Off is the pre-HDW-518 behaviour, tick for tick: same interval, same ping body, same skip reasons,
no extra ``/api/ps`` read. On adds a literal interval, an idle backoff and an ``expires_at`` read.
No new task/thread/timer: the tick rides ``Daemon._every(self.keep_warm_interval, ...)``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from headwaters.core.catalog.http import SourceError
from headwaters.core.hardware.state import BatteryState, GpuBusy
from headwaters.core.keepwarm import KeepWarm, KeepWarmResult, describe
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_decisions import latest_decision_ts
from tests.rebuild_support import DaemonRig
from tests.router_rig import Rig
from tests.router_support import OK, candidate

EXPIRES = "2026-11-03T20:04:24.8249404-05:00"


class FakeHttp:
    """Ollama's two endpoints, with the ``expires_at`` a real ``/api/ps`` carries."""

    def __init__(
        self,
        loaded: list[str] | None = None,
        *,
        ps_down: bool = False,
        expires_at: str | None = EXPIRES,
    ) -> None:
        self.loaded = loaded or []
        self.ps_down = ps_down
        self.expires_at = expires_at
        self.fail_post = False
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

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
                raise SourceError("connect failed")
            return {"models": [{"name": n, "expires_at": self.expires_at} for n in self.loaded]}
        if self.fail_post:
            raise SourceError("boom")
        if body is not None and body.get("model") not in self.loaded:
            self.loaded.append(body["model"])  # a real load: /api/ps lists it from then on
        return {"done": True, "done_reason": "load"}

    @property
    def posts(self) -> list[tuple[str, str, dict[str, Any] | None]]:
        return [c for c in self.calls if c[0] == "POST"]

    @property
    def ps_reads(self) -> int:
        return sum(1 for c in self.calls if c[1].endswith("/api/ps"))


class State:
    def __init__(self, *, busy: bool = False, battery: bool = False) -> None:
        self.gpu_busy = GpuBusy(busy, 0.0, 0.0)
        self.battery = BatteryState(True, battery, 50.0)


def _rules(**local: Any) -> Rules:
    return Rules.model_validate({"tiers": {"local": local}})


def _warm(
    rules: Rules,
    http: FakeHttp,
    state: State | None = None,
    *,
    patterns: Any = (),
    last_request: Any = None,
    clock: Any = None,
) -> KeepWarm:
    extra: dict[str, Any] = {"clock": clock} if clock is not None else {}
    return KeepWarm(
        rules,
        http,
        remote_patterns=patterns,
        hardware=lambda: state,  # type: ignore[arg-type,return-value]
        last_request=last_request,
        **extra,
    )


def test_heartbeat_default_matches_today() -> None:
    """Every new key set, flag off: same interval, body, skips and calls as before HDW-518."""
    heartbeat_keys = {
        "keep_warm_heartbeat": False,
        "heartbeat_interval_s": 300.0,
        "heartbeat_idle_backoff_s": 60.0,
    }
    today = _warm(_rules(), FakeHttp(["qwen:14b"]))
    http_off = FakeHttp(["qwen:14b"])
    off = _warm(
        _rules(**heartbeat_keys),
        http_off,
        last_request=lambda: 0.0,  # ancient traffic: an off heartbeat must ignore it
        clock=lambda: 1e9,
    )
    assert today.interval_s() == off.interval_s() == 900.0
    assert today.tick() == off.tick()
    assert http_off.posts[0][2] == {
        "model": "qwen:14b",
        "messages": [],
        "stream": False,
        "keep_alive": "30m",
    }
    # One `/api/ps` to find the loaded model, no second read for `expires_at`.
    assert http_off.ps_reads == 1
    for local in ({"keep_alive": "-1"}, {"keep_warm_model": "off"}, {"host": "http://10.0.0.5"}):
        assert (
            _warm(_rules(**local), FakeHttp(["qwen:14b"])).tick()
            == _warm(_rules(**local, **heartbeat_keys), FakeHttp(["qwen:14b"])).tick()
        )


def test_heartbeat_interval_override() -> None:
    on = _rules(keep_warm_heartbeat=True, heartbeat_interval_s=300.0)
    assert _warm(on, FakeHttp()).interval_s() == 300.0
    derived = _rules(keep_warm_heartbeat=True)  # 0 = derive from keep_alive, as today
    assert _warm(derived, FakeHttp()).interval_s() == 900.0
    ignored = _rules(heartbeat_interval_s=300.0)  # flag off: the interval is inert
    assert _warm(ignored, FakeHttp()).interval_s() == 900.0


def test_numeric_keep_alive_still_arms_the_heartbeat() -> None:
    """HDW-522: ``"120"`` is stored as ``"120s"``, so the window check and the ping body agree."""
    rules = _rules(keep_warm_heartbeat=True, keep_alive="120")
    assert rules.tiers.local.keep_alive == "120s"
    assert _warm(rules, FakeHttp()).interval_s() == 60.0
    http = FakeHttp(["qwen:14b"])
    assert _warm(rules, http).tick().action == "pinged"
    assert http.posts[0][2] is not None
    assert http.posts[0][2]["keep_alive"] == "120s"


def test_heartbeat_interval_floor_is_thirty_seconds() -> None:
    """The explicit interval honours the same 30 s floor the derived path applies (FU-518-8)."""
    tiny = _rules(keep_warm_heartbeat=True, keep_alive="30m", heartbeat_interval_s=5.0)
    assert _warm(tiny, FakeHttp()).interval_s() == 30.0
    exact = _rules(keep_warm_heartbeat=True, keep_alive="30m", heartbeat_interval_s=30.0)
    assert _warm(exact, FakeHttp()).interval_s() == 30.0


def test_heartbeat_interval_must_be_shorter_than_keep_alive() -> None:
    with pytest.raises(ValidationError) as caught:
        _rules(keep_warm_heartbeat=True, keep_alive="10m", heartbeat_interval_s=600.0)
    message = str(caught.value)
    assert "heartbeat_interval_s" in message
    assert "keep_alive" in message
    # one second shorter is fine, and so is the derived interval
    _rules(keep_warm_heartbeat=True, keep_alive="10m", heartbeat_interval_s=599.0)
    _rules(keep_warm_heartbeat=True, keep_alive="10m")


def test_heartbeat_rejects_short_keep_alive() -> None:
    """§D4: reject, never clamp — a window of a minute or less cannot be kept warm."""
    for window in ("45s", "60s", "1m"):
        with pytest.raises(ValidationError) as caught:
            _rules(keep_warm_heartbeat=True, keep_alive=window)
        assert "keep_alive" in str(caught.value)
        assert "keep_warm_heartbeat" in str(caught.value)
    # Off: the same short window is allowed and keeps pinging (at the 30 s floor), unchanged.
    short = _rules(keep_alive="45s")
    assert _warm(short, FakeHttp()).interval_s() == 30.0
    assert _warm(short, FakeHttp(["qwen:14b"])).tick().action == "pinged"
    # A window that never expires is not an error: the tick already skips it.
    for keep_alive in ("-1", "0"):
        rules = _rules(keep_warm_heartbeat=True, keep_alive=keep_alive)
        assert _warm(rules, FakeHttp(["qwen:14b"])).tick().reason == "keep_alive_not_timed"


def test_heartbeat_skips_on_idle_backoff() -> None:
    rules = _rules(keep_warm_heartbeat=True, heartbeat_idle_backoff_s=3600.0)
    http = FakeHttp(["qwen:14b"])
    result = _warm(rules, http, last_request=lambda: 1_000.0, clock=lambda: 1_000.0 + 3600).tick()
    assert (result.action, result.reason) == ("skipped", "idle_backoff")
    assert http.calls == []  # not even a `/api/ps` read
    # A device that never routed a request is idle too: that is the cold state to avoid warming.
    never = _warm(rules, FakeHttp(["qwen:14b"]), last_request=lambda: None, clock=lambda: 5_000.0)
    assert never.tick().reason == "idle_backoff"


def test_heartbeat_resumes_after_traffic() -> None:
    rules = _rules(keep_warm_heartbeat=True, heartbeat_idle_backoff_s=3600.0)
    http = FakeHttp(["qwen:14b"])
    warm = _warm(rules, http, last_request=lambda: 10_000.0, clock=lambda: 10_060.0)
    assert warm.tick().action == "pinged"


def test_heartbeat_idle_backoff_off_by_default() -> None:
    """No backoff unless it is configured, and no backoff at all while the flag is off."""
    on = _rules(keep_warm_heartbeat=True)
    warm = _warm(on, FakeHttp(["qwen:14b"]), last_request=lambda: 0.0, clock=lambda: 1e9)
    assert warm.tick().action == "pinged"
    off = _rules(heartbeat_idle_backoff_s=3600.0)
    idle = _warm(off, FakeHttp(["qwen:14b"]), last_request=lambda: 0.0, clock=lambda: 1e9)
    assert idle.tick().action == "pinged"


def test_heartbeat_never_evicts_other_model() -> None:
    """K7 holds with the flag on: a 12 GB device has no room for a second model."""
    rules = _rules(keep_warm_heartbeat=True, keep_warm_model="qwen:14b")
    http = FakeHttp(["other:7b"])
    result = _warm(rules, http).tick()
    assert (result.action, result.reason) == ("skipped", "other_model_loaded")
    assert http.posts == []


def test_heartbeat_loads_named_model_when_device_empty() -> None:
    """K6 made explicit: an empty device is the one case an explicit model is loaded."""
    rules = _rules(keep_warm_heartbeat=True, keep_warm_model="qwen:14b")
    http = FakeHttp([])
    result = _warm(rules, http).tick()
    assert (result.action, result.models) == ("pinged", ("qwen:14b",))
    assert result.expires_at == EXPIRES  # read after the ping, only when the flag is on
    assert http.ps_reads == 2  # the "what is loaded" read, then the `expires_at` read


def test_heartbeat_one_post_per_tick_on_failure() -> None:
    http = FakeHttp(["qwen:14b", "other:7b"])
    http.fail_post = True
    result = _warm(_rules(keep_warm_heartbeat=True), http).tick()
    assert (result.action, result.reason) == ("error", "ping_failed")
    assert len(http.posts) == 1  # no inner retry loop, and no second model


def test_heartbeat_absent_ollama_is_quiet(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    result = _warm(_rules(keep_warm_heartbeat=True), FakeHttp(ps_down=True)).tick()
    assert (result.action, result.reason) == ("skipped", "ollama_unreachable")
    assert caplog.records == []  # no warning; the daemon logs one line per state change
    assert describe(result, 900.0) == "keep-warm: paused (ollama_unreachable)"


def test_heartbeat_writes_nothing_to_ledger(tmp_path: Path) -> None:
    """A tick is invisible to the ledger: no decision row, no quota count, no outcome."""
    rig = Rig(tmp_path, {"qwen-local": OK}, [candidate("qwen-local", "local", 86.0)])
    rig.run()  # one real request: one decision, counted once against the allowances
    before = _table_counts(rig.db)
    http = FakeHttp(["qwen:14b"])
    assert _warm(_rules(keep_warm_heartbeat=True), http).tick().action == "pinged"
    assert _table_counts(rig.db) == before
    assert before["decisions"] == 1
    assert latest_decision_ts(rig.db) is not None


def test_heartbeat_does_not_change_routing(tmp_path: Path) -> None:
    """The heartbeat is a background ping: the same request routes the same way with it on."""
    now: dict[str, Rules] = {"rules": _rules(keep_warm_model="auto")}
    rig = Rig(
        tmp_path,
        {"qwen-local": OK},
        [candidate("qwen-local", "local", 86.0)],
        rules_source=lambda: now["rules"],
    )
    first = rig.run()
    _warm(now["rules"], FakeHttp(["qwen:14b"])).tick()
    second = rig.run()
    now["rules"] = _rules(
        keep_warm_model="auto", keep_warm_heartbeat=True, heartbeat_interval_s=300
    )
    _warm(now["rules"], FakeHttp(["qwen:14b"]), last_request=lambda: 1e12).tick()
    third = rig.run()
    models = {first.decision.model, second.decision.model, third.decision.model}
    assert models == {"qwen-local"}
    assert [a.model for a in first.attempts] == [a.model for a in third.attempts]


def test_heartbeat_stops_on_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No new task: the tick rides the daemon's existing loop, which stops with the daemon."""
    made = DaemonRig(tmp_path, monkeypatch)
    try:
        seen: list[float | None] = []

        def counting_tick(self: KeepWarm) -> KeepWarmResult:
            seen.append(self._last_request() if self._last_request is not None else None)
            if len(seen) == 2:
                made.daemon.request_stop()
            return KeepWarmResult("skipped", "nothing_loaded")

        monkeypatch.setattr(KeepWarm, "tick", counting_tick)
        made.daemon._stop = asyncio.Event()  # what `start` installs before creating the tasks
        # The same loop the daemon starts, with the wait shortened so the test does not idle 15 min.
        asyncio.run(made.daemon._every(lambda: 0.01, made.daemon.keep_warm_once))
        assert len(seen) == 2
        assert seen[0] is None  # wired to the LEDGER's own timestamp, not a new state path
        asyncio.run(made.daemon._every(lambda: 0.01, made.daemon.keep_warm_once))
        assert len(seen) == 2  # nothing survives the stop
    finally:
        made.close()


def test_heartbeat_does_not_resurrect() -> None:
    """The tick is rebuilt from the live rules: clearing the flag is today's behaviour at once."""
    http = FakeHttp(["qwen:14b"])
    now: dict[str, Rules] = {
        "rules": _rules(keep_warm_heartbeat=True, heartbeat_idle_backoff_s=60.0)
    }

    def tick() -> KeepWarmResult:
        # `Daemon.keep_warm_once` builds a fresh `KeepWarm` from `live_rules` on every tick.
        return KeepWarm(
            now["rules"],
            http,
            hardware=lambda: None,
            last_request=lambda: None,
            clock=lambda: 1e9,
        ).tick()

    assert tick().reason == "idle_backoff"
    now["rules"] = _rules()
    after = tick()
    assert after.action == "pinged"
    assert after.expires_at is None
    assert http.ps_reads == 1  # the idle tick read nothing, and no heartbeat state lingered


def test_heartbeat_loopback_only() -> None:
    rules = _rules(keep_warm_heartbeat=True)
    assert (
        _warm(
            _rules(**{"host": "http://10.0.0.5:11434", "keep_warm_heartbeat": True}),
            FakeHttp(["qwen:14b"]),
        )
        .tick()
        .reason
        == "host_not_loopback"
    )
    patterns = (re.compile(r"(?:^|[:/-])cloud$"),)
    remote = _warm(rules, FakeHttp(["gpt-oss:120b-cloud"]), patterns=patterns)
    assert remote.tick().reason == "nothing_loaded"
    explicit = _warm(
        _rules(keep_warm_heartbeat=True, keep_warm_model="gpt-oss:120b-cloud"),
        FakeHttp(),
        patterns=patterns,
    )
    assert explicit.tick().reason == "remote_model"


@pytest.mark.parametrize(
    ("result", "interval", "line"),
    [
        (
            KeepWarmResult("pinged", "ok", ("qwen:14b",), EXPIRES),
            900.0,
            f"keep-warm: resident model=qwen:14b interval=900s until={EXPIRES}",
        ),
        (
            KeepWarmResult("pinged", "ok", ("qwen:14b",)),
            300.0,
            "keep-warm: resident model=qwen:14b interval=300s",
        ),
        (
            KeepWarmResult("error", "ping_failed", ("qwen:14b",)),
            900.0,
            "keep-warm: ping failed for qwen:14b (ping_failed)",
        ),
        (KeepWarmResult("skipped", "idle_backoff"), 900.0, "keep-warm: idle backoff"),
        (
            KeepWarmResult("skipped", "ollama_unreachable"),
            900.0,
            "keep-warm: paused (ollama_unreachable)",
        ),
    ],
)
def test_describe_says_one_line_per_state(
    result: KeepWarmResult, interval: float, line: str
) -> None:
    assert describe(result, interval) == line


def test_latest_decision_ts_is_none_until_something_is_routed(tmp_path: Path) -> None:
    db = open_database(tmp_path / "state.db")
    assert latest_decision_ts(db) is None


def _table_counts(db: Database) -> dict[str, int]:
    counts: dict[str, int] = {}
    with db.read() as c:
        for table in ("decisions", "outcomes", "quotas"):
            counts[table] = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return counts
