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
