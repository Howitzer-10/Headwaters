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
