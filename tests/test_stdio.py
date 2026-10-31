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
