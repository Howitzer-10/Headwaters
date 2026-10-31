"""HDW-201: excluded-port parsing, free-port selection and the loopback-only bind rule."""

from __future__ import annotations

import socket
import sys
from collections.abc import Sequence

import pytest

from headwaters.core import netports

# Real output of `netsh int ipv4 show excludedportrange protocol=tcp` (Windows 11, English).
NETSH_SAMPLE = "\n".join(
    [
        "",
        "Protocol tcp Port Exclusion Ranges",
        "",
        "Start Port    End Port      ",  # netsh pads these lines with trailing spaces
        "----------    --------      ",
        "      2869        2869      ",
        "      5357        5357      ",
        "      8000        8099      ",
        "     27339       27339      ",
        "     50000       50059     *",
        "",
        "* - Administered port exclusions.",
        "",
    ]
)
# The same table on a German install: headers differ, the numeric rows do not.
NETSH_GERMAN = """
Protokoll TCP Portausschlussbereiche

Startport    Endport
----------    --------
      4000        4010

* - Verwaltete Portausschlüsse.
"""


def test_parse_sample_output() -> None:
    assert netports.parse_excluded_ranges(NETSH_SAMPLE) == [
        (2869, 2869),
        (5357, 5357),
        (8000, 8099),
        (27339, 27339),
        (50000, 50059),
    ]


def test_parse_ignores_headers_in_other_languages() -> None:
    assert netports.parse_excluded_ranges(NETSH_GERMAN) == [(4000, 4010)]


@pytest.mark.parametrize(
    "text",
    ["", "garbage\nmore garbage", "   10    5   \n", "0 10\n70000 70001\n", "1.5 2.5\n"],
)
def test_parse_rejects_nonsense(text: str) -> None:
    assert netports.parse_excluded_ranges(text) == []


def test_is_excluded_checks_both_ends() -> None:
    ranges = [(4000, 4010)]
    assert netports.is_excluded(4000, ranges)
    assert netports.is_excluded(4010, ranges)
    assert not netports.is_excluded(3999, ranges)
    assert not netports.is_excluded(4011, ranges)


def test_read_ranges_runs_netsh_with_fixed_argv() -> None:
    seen: list[tuple[str, ...]] = []

    def run(argv: Sequence[str]) -> str:
        seen.append(tuple(argv))
        return NETSH_SAMPLE

    assert (8000, 8099) in netports.read_excluded_ranges(run, platform="win32")
    assert seen == [netports.EXCLUDED_RANGE_COMMAND]
    assert netports.EXCLUDED_RANGE_COMMAND[:3] == ("netsh", "int", "ipv4")


def test_read_ranges_is_empty_off_windows_and_never_runs_netsh() -> None:
    def boom(argv: Sequence[str]) -> str:
        raise AssertionError("netsh must not run off Windows")

    assert netports.read_excluded_ranges(boom, platform="linux") == []


def test_read_ranges_survives_a_failing_netsh() -> None:
    assert netports.read_excluded_ranges(lambda argv: "", platform="win32") == []


def test_bind_skips_excluded_and_taken_ports() -> None:
    tried: list[int] = []

    def binder(port: int, host: str) -> socket.socket:
        tried.append(port)
        return netports.bind_loopback(0, host)

    sock = netports.bind_first_free(4000, excluded=[(4000, 4001)], taken={4002}, binder=binder)
    sock.close()
    assert tried == [4003]


def test_bind_falls_back_when_a_port_is_busy() -> None:
    holder = netports.bind_loopback(0)
    busy = holder.getsockname()[1]
    holder.listen()
    try:
        sock = netports.bind_first_free(busy, span=50)
        try:
            assert sock.getsockname()[1] != busy
            assert busy < sock.getsockname()[1] <= busy + 50
        finally:
            sock.close()
    finally:
        holder.close()


def test_bind_reports_when_nothing_is_free() -> None:
    with pytest.raises(netports.NoFreePortError):
        netports.bind_first_free(5000, excluded=[(5000, 5010)], span=10)


def test_pick_free_port_releases_the_port() -> None:
    port = netports.pick_free_port(45000, span=100)
    again = netports.bind_loopback(port)
    again.close()


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "192.168.1.5", "::", "::1", "localhost", "example.com", "", "127.0.0"]
)
def test_non_loopback_hosts_are_refused(host: str) -> None:
    with pytest.raises(netports.NonLoopbackBindError):
        netports.assert_loopback_host(host)
    with pytest.raises(netports.NonLoopbackBindError):
        netports.bind_loopback(0, host)
    with pytest.raises(netports.NonLoopbackBindError):
        netports.bind_first_free(4000, host=host)


def test_loopback_hosts_are_accepted() -> None:
    assert netports.assert_loopback_host("127.0.0.1") == "127.0.0.1"
    assert netports.assert_loopback_host("127.5.5.5") == "127.5.5.5"


def test_bound_socket_is_loopback_only() -> None:
    sock = netports.bind_loopback(0)
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="netsh is Windows-only")
def test_real_netsh_output_parses() -> None:
    ranges = netports.read_excluded_ranges()
    assert all(1 <= start <= end <= 65535 for start, end in ranges)
