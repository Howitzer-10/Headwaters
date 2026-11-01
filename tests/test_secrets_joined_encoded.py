"""HDW-142: secrets split across fields or encoded (base64, percent) are still seen."""

from __future__ import annotations

import base64
import hashlib
import random
import string
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.router_prep import scan_groups
from headwaters.core.router_types import RouteRequest
from headwaters.core.rules import Rules
from headwaters.core.secrets_detect import ScanLimits, SecretDetector
from tests.executor_support import REPO_ROOT
from tests.net_spy import forbid_network
from tests.router_rig import Rig
from tests.router_support import OK
from tests.test_router import LOCAL, pool
from tests.test_secrets_detect import _real_secrets

ALNUM = string.ascii_letters + string.digits
LIMITS = ScanLimits.from_rules(Rules().secrets)
SEEDS = _real_secrets(random.Random(142))
IDS = [f"{name}-{i}" for i, (name, _) in enumerate(SEEDS)]


def detector() -> SecretDetector:
    return SecretDetector.from_catalog(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH))


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def names(groups: list[list[str]]) -> set[str]:
    return {m.pattern for m in detector().scan_groups(groups, LIMITS)}


@pytest.mark.parametrize(("name", "secret"), SEEDS, ids=IDS)
def test_plain_secret_alone_still_found(name: str, secret: str) -> None:
    assert name in names([[secret]])


@pytest.mark.parametrize(("name", "secret"), SEEDS, ids=IDS)
def test_split_at_every_position_is_found(name: str, secret: str) -> None:
    det = detector()
    for cut in range(1, len(secret)):
        groups = [["intro", secret[:cut], secret[cut:], "outro"]]
        got = {m.pattern for m in det.scan_groups(groups, LIMITS)}
        assert name in got, f"split at {cut}"


@pytest.mark.parametrize(("name", "secret"), SEEDS, ids=IDS)
def test_base64_and_percent_forms_are_found(name: str, secret: str) -> None:
    assert name in names([[f"data: {b64(secret)}"]])
    assert name in names([[f"data: {b64(secret).rstrip('=')}"]])
    assert name in names([[f"q={quote(secret, safe='')}"]])
    assert name in names([["".join(f"%{ord(c):02X}" for c in secret)]])


def test_results_hold_no_secret_text() -> None:
    secret = SEEDS[0][1]
    hits = detector().scan_groups([[secret[:10], secret[10:], b64(secret)]], LIMITS)
    assert hits
    assert secret not in repr(hits)


def test_a_secret_inside_one_leaf_reports_one_pattern() -> None:
    secret = SEEDS[0][1]
    hits = detector().scan_groups([["a", f"key {secret}", "b"]], LIMITS)
    assert {m.pattern for m in hits} == {SEEDS[0][0]}


def test_join_and_decode_can_be_switched_off() -> None:
    secret = SEEDS[0][1]
    off = ScanLimits(join=False, decode=False)
    assert not detector().scan_groups([[secret[:8], secret[8:]]], off)
    assert not detector().scan_groups([[b64(secret)]], off)


def test_groups_are_not_joined_across_messages() -> None:
    secret = SEEDS[0][1]
    assert not detector().scan_groups([[secret[:8]], [secret[8:]]], LIMITS)


def test_bad_base64_is_ignored() -> None:
    assert not names([["QUJD" * 10 + "!", "====", "A" * 25, "%zz%%%", "%C3%28"]])


BENIGN = [
    hashlib.sha256(b"x").hexdigest(),
    hashlib.sha512(b"x").hexdigest(),
    b64(hashlib.sha256(b"x").hexdigest()),
    "3f2b8c1e-9d4a-4b7e-8a61-5c0d2e9f7a13",
    "".join(random.Random(7).choice(ALNUM) for _ in range(64)),
    b64("the quick brown fox jumps over the lazy dog"),
    b64("def add(a, b):\n    return a + b\n"),
    "https://example.com/path?q=hello%20world&x=%7Bok%7D",
    "ci-run-20261101-build-12345-linux-x64",
]


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_gives_no_hit(text: str) -> None:
    assert not names([[text]])
    assert not names([["id", text, "name", text]])


def test_one_megabyte_tool_result_is_inside_the_rule_budget() -> None:
    rng = random.Random(1)
    words = ["".join(rng.choice(ALNUM) for _ in range(rng.randint(3, 12))) for _ in range(2000)]
    body = " ".join(rng.choice(words) for _ in range(150_000))[:1_000_000]
    blob = b64("".join(rng.choice(ALNUM) for _ in range(700_000)))
    groups = [[body[:500_000], body[500_000:], blob[:400_000], "x%41" * 50_000]]
    start = time.monotonic()
    hits = detector().scan_groups(groups, LIMITS)
    elapsed_ms = (time.monotonic() - start) * 1000
    assert not hits
    assert elapsed_ms < Rules().secrets.scan_budget_ms


def test_a_secret_inside_a_one_megabyte_result_is_found() -> None:
    secret = SEEDS[0][1]
    pad = ("lorem ipsum dolor sit amet " * 40_000)[:500_000]
    groups = [[pad, f"token={b64(secret)}", pad]]
    assert SEEDS[0][0] in names(groups)


USER_SPLIT: dict[str, Any] = {
    "role": "user",
    "content": [
        {"type": "text", "text": "key part one: " + SEEDS[0][1][:12]},
        {"type": "text", "text": SEEDS[0][1][12:]},
    ],
}
USER_B64: dict[str, Any] = {"role": "user", "content": "blob " + b64(SEEDS[0][1])}
INNOCENT: dict[str, Any] = {"role": "user", "content": "add two numbers"}


@pytest.mark.parametrize("message", [USER_SPLIT, USER_B64], ids=["split", "base64"])
def test_request_routes_local_without_network(
    message: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), secrets=detector())
    opened = forbid_network(monkeypatch, allow_loopback=True)
    result = rig.run(messages=[INNOCENT, message])
    assert result.decision.model == LOCAL
    assert rig.cloud_clients == 0
    assert not opened
    secret = SEEDS[0][1]
    assert secret not in repr(result.decision)
    assert b64(secret) not in repr(result.decision)


def test_scan_groups_follow_request_leaves() -> None:
    groups = scan_groups(RouteRequest(messages=[INNOCENT, USER_SPLIT]))
    first, second = (p["text"] for p in USER_SPLIT["content"])
    assert groups[-1].index(first) < groups[-1].index(second)
