from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
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
        "claude-sonnet-4-5",
        "google/gemma-4-31b-it:free",
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
    result = refresh(db, catalog, Http(routes()), rules)
    for name in ("opencode/cfg-free", "opencode/listed-free"):
        assert get_model(db, name) is None  # HDW-150: free-tier models work only inside OpenCode
    assert any("2 free model(s) not offered" in w for w in result.warnings)


def test_opencode_rows_from_an_earlier_version_are_removed(db: Database, catalog: Catalog) -> None:
    from headwaters.core.state_models import upsert_model

    refresh(db, catalog, Http(routes()))
    old = get_model(db, "qwen:14b")
    assert old is not None
    upsert_model(
        db,
        replace(old, id="opencode/old", provider="opencode", source="opencode"),
    )
    result = refresh(db, catalog, Http(routes()), now=200.0)
    assert get_model(db, "opencode/old") is None
    assert result.removed >= 1


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


def _slow_ollama(events: list[str] | None = None) -> Any:
    """A source that takes a moment, so the progress line must arrive before it finishes."""

    def source(http: Any, host: str, patterns: Any = (), *, deadline: float | None = None) -> Any:
        if events is not None:
            events.append("ollama started")
        time.sleep(0.2)
        if events is not None:
            events.append("ollama finished")
        return [], []

    return source


def test_refresh_reports_progress_before_a_slow_source_answers(
    db: Database, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    monkeypatch.setattr(discovery, "ollama_models", _slow_ollama(events))
    discovery.refresh_models(
        db,
        Rules(),
        catalog,
        http=Http(routes()),
        env={},
        home=Path("/nohome"),
        progress=events.append,
    )
    assert events[:3] == [
        f"checking ollama at {OLLAMA}",  # printed before the source even starts
        "ollama started",
        "ollama finished",
    ]
    assert "checking opencode config" in events
    assert "checking openrouter" in events


def test_a_source_past_the_budget_warns_and_keeps_the_partial_result(
    db: Database, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    refresh(db, catalog, Http(routes()))  # rows from an earlier successful refresh
    release = threading.Event()

    def stalled(*args: Any, **kwargs: Any) -> Any:
        release.wait(30)  # a blackholed endpoint: no answer, no timeout of its own
        return []

    monkeypatch.setattr(discovery, "openrouter_models", stalled)
    started = time.monotonic()
    try:
        result = discovery.refresh_models(
            db,
            Rules(),
            catalog,
            http=Http(routes()),
            env={},
            home=Path("/nohome"),
            budget_s=0.25,
        )
    finally:
        release.set()
    elapsed = time.monotonic() - started
    assert elapsed < 5  # the budget, not the stalled source, decided when this returned
    assert any(w.startswith("openrouter:") and "budget" in w for w in result.warnings)
    assert "openrouter" not in result.sources_ok
    assert get_model(db, "qwen:14b") is not None  # local still refreshed
    assert get_model(db, "vendor/big") is not None  # a silent source never deletes rows


def test_progress_never_prints_credentials_from_an_endpoint_url(
    db: Database, catalog: Catalog
) -> None:
    rules = Rules.model_validate(
        {
            "models": {
                "openai_compatible": [
                    {
                        "name": "keyed",
                        "base_url": "https://user:secret@b.example/v1",
                        "models": ["m"],
                    }
                ]
            }
        }
    )
    events: list[str] = []
    discovery.refresh_models(
        db,
        rules,
        catalog,
        http=Http(routes()),
        env={},
        home=Path("/nohome"),
        progress=events.append,
    )
    assert "checking keyed at https://b.example/v1" in events
    assert "secret" not in "\n".join(events)


def test_cli_refresh_prints_progress_on_stderr_and_keeps_json_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADWATERS_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(discovery, "ollama_models", _slow_ollama())
    monkeypatch.setattr(discovery, "UrllibHttp", lambda: Http(routes()))

    result = runner.invoke(app, ["models", "refresh"])
    assert result.exit_code == 0
    assert "checking ollama" in result.stderr
    assert "checking ollama" not in result.stdout
    assert "Refreshed" in result.stdout

    as_json = runner.invoke(app, ["models", "refresh", "--json"])
    assert as_json.exit_code == 0
    assert "checking ollama" in as_json.stderr  # progress never pollutes the JSON document
    assert json.loads(as_json.stdout)["ok"] is True
