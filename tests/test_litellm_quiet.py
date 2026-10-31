"""HDW-127 (F8): litellm never writes to stdout, logs through redaction, calls only the provider."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.core.executor.cloud import LITELLM_LOGGERS, LiteLLMBackend, _load_litellm
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import BackendRequest, ExecOptions, ExecTarget
from headwaters.core.stdio import guard_stdout

REPO_ROOT = Path(__file__).resolve().parents[1]
KEY = "sk-abcdefghijklmnop1234"


class _FakeModule:
    """Stands in for litellm; like the real one it prints when an exception is mapped."""

    def __init__(self) -> None:
        self.suppress_debug_info = False
        self.set_verbose = True

    async def acompletion(self, **_: Any) -> Any:
        if not self.suppress_debug_info:
            print("Give Feedback / Get Help: https://github.com/BerriAI/litellm")
        err = RuntimeError("rate limited")
        err.status_code = 429  # type: ignore[attr-defined]
        raise err


def _request() -> BackendRequest:
    target = ExecTarget("m", "openrouter", "openrouter/acme/m:free")
    return BackendRequest(target, [{"role": "user", "content": "hi"}], None, ExecOptions(), "30m")


@pytest.fixture
def litellm_loggers() -> Any:
    saved = {
        n: (list(logging.getLogger(n).handlers), logging.getLogger(n).level)
        for n in LITELLM_LOGGERS
    }
    yield
    for name, (handlers, level) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers = handlers
        logger.filters.clear()
        logger.setLevel(level)
        logger.propagate = True


def test_mapped_exception_writes_nothing_to_stdout(
    monkeypatch: pytest.MonkeyPatch, litellm_loggers: Any
) -> None:
    fake = _FakeModule()
    monkeypatch.setattr("headwaters.core.executor.cloud.importlib.import_module", lambda _: fake)
    backend = LiteLLMBackend()

    async def go() -> None:
        async for _ in backend.stream(_request()):
            pass

    with guard_stdout(strict=True), pytest.raises(ProviderError) as info:
        asyncio.run(go())
    assert info.value.status == 429
    assert fake.suppress_debug_info is True
    assert fake.set_verbose is False


def test_litellm_logger_records_are_redacted_and_leveled(
    monkeypatch: pytest.MonkeyPatch,
    litellm_loggers: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "headwaters.core.executor.cloud.importlib.import_module", lambda _: SimpleNamespace()
    )
    _load_litellm()
    _load_litellm()  # idempotent: no duplicate handlers
    for name in LITELLM_LOGGERS:
        logger = logging.getLogger(name)
        assert len(logger.handlers) == 1
        assert logger.level == logging.WARNING
        assert logger.propagate is False
        logger.info("debug detail %s", KEY)  # below the cap: dropped
        logger.warning("auth failed with key %s", KEY)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert KEY not in captured.err
    assert "[REDACTED]" in captured.err
    assert "debug detail" not in captured.err


def test_real_litellm_failing_stream_is_silent_and_only_reaches_the_provider() -> None:
    """Real litellm, sockets and DNS mocked: nothing on stdout, no host but the provider's."""
    code = textwrap.dedent(
        f"""
        import asyncio, socket, sys
        hosts = []

        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex
        LOOPBACK = ('127.0.0.1', '::1', 'localhost')  # the event loop's own socketpair

        def blocked(host):
            hosts.append(str(host))
            raise OSError('network blocked by test')

        def connect(self, address):
            if address[0] in LOOPBACK:
                return real_connect(self, address)
            blocked(address[0])

        def connect_ex(self, address):
            if address[0] in LOOPBACK:
                return real_connect_ex(self, address)
            blocked(address[0])

        def blocked_addr(host, *args, **kwargs):
            blocked(host)

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.getaddrinfo = blocked_addr

        from headwaters.core.executor.cloud import LiteLLMBackend
        from headwaters.core.executor.errors import ProviderError
        from headwaters.core.executor.types import BackendRequest, ExecOptions, ExecTarget
        from headwaters.core.stdio import guard_stdout

        target = ExecTarget('m', 'openai', 'openai/gpt-x', api_base='https://api.provider.test/v1')
        request = BackendRequest(
            target, [{{'role': 'user', 'content': 'hi'}}], None, ExecOptions(), '30m',
            api_key={KEY!r},
        )
        backend = LiteLLMBackend()

        async def go():
            async for _ in backend.stream(request):
                pass

        with guard_stdout(strict=True):
            try:
                asyncio.run(go())
            except ProviderError:
                pass
            else:
                raise SystemExit('expected a ProviderError')
        foreign = sorted({{h for h in hosts if h != 'api.provider.test'}})
        assert hosts, 'the provider host was never tried'
        assert not foreign, foreign
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert KEY not in result.stderr
