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
