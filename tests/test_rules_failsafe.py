from pathlib import Path

from headwaters.core.hardware.fit import FitConfig
from headwaters.core.rules import Rules
from headwaters.core.rules_watch import RulesManager


def _start(tmp_path: Path, text: str) -> RulesManager:
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return RulesManager(path)


def test_invalid_file_keeps_private_lock(tmp_path: Path) -> None:
    m = _start(tmp_path, "private_lock: true\nairplane: true\nnot_a_key: 1\n")
    assert m.last_error is not None
    assert m.current.private_lock is True
    assert m.current.airplane is True


def test_invalid_file_keeps_default_scope(tmp_path: Path) -> None:
    m = _start(tmp_path, "default_scope: free\nstrategy: nonsense\n")
    assert m.current.default_scope == "free"
    assert m.current.private_lock is False


def test_unreadable_file_starts_local(tmp_path: Path) -> None:
    m = _start(tmp_path, "key: [unclosed\n")
    assert m.last_error is not None
    assert m.current.default_scope == "local"


def test_nothing_readable_in_valid_yaml_starts_local(tmp_path: Path) -> None:
    m = _start(tmp_path, "bogus: 1\n")
    assert m.current.default_scope == "local"


def test_fit_config_from_rules() -> None:
    rules = Rules.model_validate(
        {
            "scoring": {"ctx_headroom": 1.5},
            "tiers": {"local": {"ctx_steps": [4096, 2048], "overhead_gb": 0.5}},
        }
    )
    cfg = FitConfig.from_rules(rules)
    assert cfg.ctx_headroom == 1.5
    assert cfg.ctx_steps == (2048, 4096)
    assert cfg.overhead_gb == 0.5
    assert FitConfig.from_rules(Rules()) == FitConfig()


def test_verifiable_tasks_is_a_rule() -> None:
    assert Rules().floors.verifiable_tasks == ["code"]
