import random
import string
from pathlib import Path

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.secrets_detect import SecretDetector

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def detector() -> SecretDetector:
    return SecretDetector.from_catalog(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH))


def _rand(rng: random.Random, alphabet: str, n: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


ALNUM = string.ascii_letters + string.digits


def _real_secrets(rng: random.Random) -> list[tuple[str, str]]:
    """(expected pattern name, secret text) built from random material."""
    return [
        ("sk-style-api-key", "sk-" + _rand(rng, ALNUM, 32)),
        ("sk-style-api-key", "sk-proj-" + _rand(rng, ALNUM, 40)),
        ("aws-access-key-id", "AKIA" + _rand(rng, string.ascii_uppercase + string.digits, 16)),
        ("github-token", "ghp_" + _rand(rng, ALNUM, 36)),
        ("github-token", "github_pat_" + _rand(rng, ALNUM + "_", 60)),
        ("slack-token", "xoxb-" + _rand(rng, string.digits, 12) + "-" + _rand(rng, ALNUM, 24)),
        ("google-api-key", "AIza" + _rand(rng, ALNUM + "_-", 35)),
        (
            "json-web-token",
            ".".join(
                [
                    "eyJ" + _rand(rng, ALNUM, 20),
                    "eyJ" + _rand(rng, ALNUM, 20),
                    _rand(rng, ALNUM, 20),
                ]
            ),
        ),
        ("bearer-token", "Bearer " + _rand(rng, ALNUM, 40)),
        ("private-key-block", "-----BEGIN RSA PRIVATE KEY-----\n" + _rand(rng, ALNUM, 60)),
        ("private-key-block", "-----BEGIN PRIVATE KEY-----"),
    ]


def test_detects_each_family(detector: SecretDetector) -> None:
    rng = random.Random(1)
    for name, secret in _real_secrets(rng):
        matches = detector.scan(f"here you go: {secret} thanks")
        assert name in {m.pattern for m in matches}, name


@pytest.mark.parametrize(
    "text",
    [
        "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
        "sk-...",
        "sk-your-key-goes-here-1234567890",
        "export OPENAI_API_KEY=YOUR_API_KEY",
        "token = 'your_token'",
        "AKIAEXAMPLEEXAMPLE12",
        "Bearer xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "sk-ant-api03-EXAMPLEKEYEXAMPLEKEYEXAMPLE",
        "ghp_" + "0" * 36,
    ],
)
def test_placeholders_are_ignored(detector: SecretDetector, text: str) -> None:
    assert detector.scan(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "def add(a, b):\n    return a + b\n",
        "Please refactor the skill tree and the sketch module.",
        "The task-list is sk-short.",
        "see https://example.com/docs#section",
        "AKIA is a prefix",
    ],
)
def test_clean_text_has_no_matches(detector: SecretDetector, text: str) -> None:
    assert not detector.contains_secret(text)


def test_matches_carry_no_secret_text(detector: SecretDetector) -> None:
    secret = "sk-" + "Qz8" * 12
    (match,) = detector.scan(f"key {secret}")
    assert secret not in repr(match)
    assert not hasattr(match, "text")
    assert (match.start, match.end) == (4, 4 + len(secret))


def test_scan_all_covers_every_text(detector: SecretDetector) -> None:
    found = detector.scan_all(["clean", "sk-" + "Ab1" * 10, "also clean"])
    assert [m.pattern for m in found] == ["sk-style-api-key"]


def test_fuzz_real_secrets_always_found_in_noise(detector: SecretDetector) -> None:
    rng = random.Random(20261030)
    noise = string.ascii_letters + string.digits + " \n\t.,;:(){}[]=<>/\\\"'`#"
    for _ in range(300):
        for name, secret in _real_secrets(rng):
            left = _rand(rng, noise, rng.randint(0, 80))
            right = _rand(rng, noise, rng.randint(0, 80))
            # Keep the secret delimited the way real prompts do, so word boundaries hold.
            text = f"{left}\n{secret}\n{right}"
            if any(w in (left + right).lower() for w in ("example", "dummy", "placeholder")):
                continue
            assert name in {m.pattern for m in detector.scan(text)}, (name, text)


def test_fuzz_random_noise_has_no_false_positives(detector: SecretDetector) -> None:
    rng = random.Random(7)
    alphabet = string.ascii_lowercase + " \n.,;(){}=_-"
    for _ in range(500):
        text = _rand(rng, alphabet, rng.randint(0, 400))
        assert not detector.contains_secret(text), text


def test_fuzz_never_raises_on_arbitrary_text(detector: SecretDetector) -> None:
    rng = random.Random(99)
    for _ in range(300):
        text = "".join(chr(rng.randint(0, 0x2FFF)) for _ in range(rng.randint(0, 300)))
        detector.scan(text)


def test_fuzz_placeholder_wrapped_keys_are_ignored(detector: SecretDetector) -> None:
    rng = random.Random(3)
    for _ in range(200):
        filler = rng.choice(["xxxxxxxxxxxxxxxx", "example", "dummy", "0" * 20])
        text = f"sk-{filler}{_rand(rng, ALNUM, 10)}"
        assert detector.scan(text) == [], text
