"""The Beta 0.1 release gate (HDW-407).

A release is ready when two different things are true, and the gate keeps them apart:

* **machine checks** - things a program can prove from the repo: the docs exist, the per-host
  pages still match the installer templates, the troubleshooting covers the six known failure
  modes, the promises and the ★ legend are in the places the store page needs, the public files
  carry no secret-shaped string, the catalog rule from FU-121-3 holds;
* **owner steps** - things only a person can do once, recorded in ``docs/release/evidence.json``
  with the proof the step needs (minutes, machines, hosts, dates). No evidence file means every
  owner step is blocked, which is the state this repo is actually in.

``ready`` is true only when both halves pass, so a docs-only change can never turn the gate green.
Exit codes: 0 ready, 1 not ready, 2 the evidence file or the repo root is unusable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headwaters.control import release_docs
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH
from headwaters.core.catalog.schema import Catalog
from headwaters.core.features import CORE_FEATURES, STAR_LEGEND
from headwaters.core.logging import DROPPED_FIELDS
from headwaters.core.rules import Rules
from headwaters.installer import templates
from headwaters.installer.hosts import all_adapters, host_ids

__all__ = [
    "EVIDENCE_RELATIVE",
    "EXIT_BLOCKED",
    "EXIT_CONFIG",
    "EXIT_READY",
    "FEED_RELATIVE",
    "Check",
    "EvidenceError",
    "GateResult",
    "OwnerStep",
    "render",
    "run_gate",
]

EXIT_READY = 0
EXIT_BLOCKED = 1
EXIT_CONFIG = 2

EVIDENCE_RELATIVE = Path("docs") / "release" / "evidence.json"
FEED_RELATIVE = Path("docs") / "release" / "catalog_feed.json"

_REQUIRED_DOCS: dict[str, int] = {
    "README.md": 600,
    "docs/BETA.md": 600,
    "docs/TROUBLESHOOTING.md": 1500,
    "docs/PRIVACY.md": 800,
    "docs/ITCH_STORE.md": 600,
    "docs/RELEASE_EVIDENCE.md": 400,
    "docs/RELEASE_CHECKLIST.md": 600,
    "docs/RELEASE_CHECKLIST_LINUX.md": 600,
    "docs/hosts/index.md": 400,
}

_TROUBLESHOOTING: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Context truncation", ("context", "truncat")),
    ("Tool-call failures", ("tool",)),
    ("Cold loads", ("cold",)),
    ("Model-name mismatch", ("model",)),
    ("Port conflicts", ("port",)),
    ("Sysmem fallback", ("sysmem",)),
)

_PROMISE_MARKERS = (
    "core-free",
    "no-tracking",
    "breaking-changes",
)
_TESTER_MARKERS = ("testers-pro", "testers-discount")

_PRIVACY_PHRASES = (
    "no telemetry",
    "opt-in",
    "prompt",
    "code",
    "path",
    "output",
    "delete",
    "mask",
)

_LINK = re.compile(r"\]\(([^)\s]+)\)")
_SECRET_SHAPES = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
)
_SECRET_ALLOW = ("EXAMPLE", "REDACTED", "placeholder", "your-", "<")
_ABSOLUTE_HOME = re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+|/home/[A-Za-z0-9._-]+")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PUBLIC_SUFFIXES = (".md", ".ps1", ".sh", ".iss", ".toml", ".py")


@dataclass(frozen=True)
class Check:
    """One thing a program can prove about the repo."""

    id: str
    title: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class OwnerStep:
    """One thing a person has to do once, with the evidence that proves it."""

    id: str
    title: str
    proof: str
    where: str
    done: bool
    reason: str


@dataclass(frozen=True)
class GateResult:
    repo_root: Path
    evidence_path: Path
    checks: tuple[Check, ...]
    steps: tuple[OwnerStep, ...]
    warnings: tuple[str, ...]

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.ok)

    @property
    def blocked(self) -> tuple[OwnerStep, ...]:
        return tuple(step for step in self.steps if not step.done)

    @property
    def ready(self) -> bool:
        return not self.failed and not self.blocked


def _read(root: Path, relative: str) -> str:
    path = root / relative
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _check(identifier: str, title: str, problem: str | None, detail: str) -> Check:
    return Check(id=identifier, title=title, ok=problem is None, detail=problem or detail)


# -- machine checks ---------------------------------------------------------------------------


def check_required_docs(root: Path) -> Check:
    missing: list[str] = []
    thin: list[str] = []
    required = dict(_REQUIRED_DOCS)
    for adapter in all_adapters():
        required[f"docs/hosts/{adapter.id}.md"] = 300
    for relative, minimum in required.items():
        path = root / relative
        if not path.is_file():
            missing.append(relative)
        elif len(path.read_text(encoding="utf-8").strip()) < minimum:
            thin.append(f"{relative} (< {minimum} chars)")
    for checklist in ("docs/RELEASE_CHECKLIST.md", "docs/RELEASE_CHECKLIST_LINUX.md"):
        if "headwaters release-gate" not in _read(root, checklist):
            thin.append(f"{checklist} does not mention headwaters release-gate")
    problem = None
    if missing:
        problem = "missing: " + ", ".join(missing)
    elif thin:
        problem = "; ".join(thin)
    return _check(
        "docs-required",
        "every required beta document exists and is not a stub",
        problem,
        f"{len(required)} documents present",
    )


def check_host_pages(root: Path) -> Check:
    stale = release_docs.stale_pages(root)
    problem = (
        "stale or missing: " + ", ".join(stale) + " (run `headwaters release-docs --write`)"
        if stale
        else None
    )
    return _check(
        "docs-hosts",
        "per-host pages match the installer templates",
        problem,
        f"{len(release_docs.render_all())} pages match the installer",
    )


def check_templates(root: Path) -> Check:
    del root
    ctx = release_docs.display_context()
    problems: list[str] = []
    for adapter in all_adapters():
        if not adapter.template_names:
            problems.append(f"{adapter.id} names no templates")
            continue
        for name in adapter.template_names:
            try:
                loaded = templates.load(name, ctx)
            except (FileNotFoundError, ValueError) as exc:
                problems.append(f"{adapter.id}: {name}: {exc}")
                continue
            if _unresolved(loaded):
                problems.append(f"{adapter.id}: {name} still has an @marker")
    return _check(
        "docs-hosts-templates",
        "every host page is backed by templates that load and are fully filled",
        "; ".join(problems) if problems else None,
        f"{len(host_ids())} hosts, all templates resolved",
    )


def _unresolved(value: Any) -> bool:
    if isinstance(value, str):
        return bool(re.fullmatch(r"@[a-z_]+", value))
    if isinstance(value, dict):
        return any(_unresolved(item) for item in value.values())
    if isinstance(value, list):
        return any(_unresolved(item) for item in value)
    return False


def check_troubleshooting(root: Path) -> Check:
    text = _read(root, "docs/TROUBLESHOOTING.md")
    sections = _sections(text)
    problems: list[str] = []
    for heading, keywords in _TROUBLESHOOTING:
        body = sections.get(heading)
        if body is None:
            problems.append(f"no `## {heading}` section")
            continue
        lowered = body.lower()
        if len(body.strip()) < 250:
            problems.append(f"`{heading}` is a stub")
        found = [word for word in keywords if word not in lowered]
        if found:
            problems.append(f"`{heading}` never says {', '.join(found)}")
    if "headwaters doctor" not in text:
        problems.append("the page never tells the reader to run `headwaters doctor`")
    return _check(
        "docs-troubleshooting",
        "the six known failure modes have a real fix",
        "; ".join(problems) if problems else None,
        f"{len(_TROUBLESHOOTING)} topics covered",
    )


def _sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines)
            current = line[3:].strip()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines)
    return sections


def check_privacy(root: Path) -> Check:
    text = _read(root, "docs/PRIVACY.md").lower()
    problems = [f"the notice never says {word!r}" for word in _PRIVACY_PHRASES if word not in text]
    rules = Rules()
    if rules.telemetry is not False:
        problems.append("core.rules.Rules.telemetry is no longer Literal[False]")
    expected = {"prompt", "content", "messages"}
    if set(DROPPED_FIELDS) != expected:
        problems.append(f"core.logging.DROPPED_FIELDS is {sorted(DROPPED_FIELDS)}")
    if "telemetry: false" not in text:
        problems.append("the notice does not show the `telemetry: false` rule")
    return _check(
        "docs-privacy",
        "the privacy notice matches the code",
        "; ".join(problems) if problems else None,
        "notice and code agree: no telemetry, prompts/content/messages never logged",
    )


def check_promises(root: Path) -> Check:
    beta = _read(root, "docs/BETA.md")
    store = _read(root, "docs/ITCH_STORE.md")
    problems: list[str] = []
    bodies = _marker_bodies(beta)
    for marker in _PROMISE_MARKERS:
        if marker not in bodies:
            problems.append(f"BETA.md has no `promise: {marker}`")
        elif bodies[marker] < 80:
            problems.append(f"`promise: {marker}` is one line, not a promise")
    chosen = [marker for marker in _TESTER_MARKERS if marker in bodies]
    if len(chosen) != 1:
        problems.append(f"exactly one tester promise must be chosen, found {chosen or 'none'}")
    else:
        if bodies[chosen[0]] < 80:
            problems.append(f"`promise: {chosen[0]}` is one line, not a promise")
        if chosen[0] not in _marker_bodies(store):
            problems.append(f"the store page does not repeat `promise: {chosen[0]}`")
    if "[pro free" in store.lower() or "50% off]" in store.lower():
        problems.append("the store page still has the `[Pro free / 50% off]` placeholder")
    for key in ("faq: stay-free", "faq: tracking"):
        if f"<!-- {key} -->" not in store:
            problems.append(f"the store page has no `{key}` FAQ")
    return _check(
        "docs-promises",
        "the four beta promises are written once and the tester promise is chosen",
        "; ".join(problems) if problems else None,
        f"promises: {', '.join(_PROMISE_MARKERS)} + {chosen[0] if chosen else '?'}",
    )


def _marker_bodies(text: str) -> dict[str, int]:
    """Marker name -> characters of body that follow it before the next marker or heading."""
    bodies: dict[str, int] = {}
    current: str | None = None
    count = 0
    for line in text.splitlines():
        found = re.fullmatch(r"<!--\s*([a-z-]+:\s*[a-z-]+)\s*-->", line.strip())
        if found:
            if current is not None:
                bodies[current] = count
            current = found.group(1).split(":", 1)[1].strip()
            count = 0
            continue
        if line.startswith("#"):
            if current is not None:
                bodies[current] = count
            current, count = None, 0
            continue
        if current is not None:
            count += len(line.strip())
    if current is not None:
        bodies[current] = count
    return bodies


def check_star_legend(root: Path) -> Check:
    problems = [
        f"{relative} does not carry the ★ legend"
        for relative in ("README.md", "docs/BETA.md", "docs/ITCH_STORE.md")
        if STAR_LEGEND not in _read(root, relative)
    ]
    return _check(
        "docs-star-legend",
        "the ★ legend is identical wherever a feature is listed",
        "; ".join(problems) if problems else None,
        "legend present in README, BETA and the store page",
    )


def check_core_features(root: Path) -> Check:
    text = _read(root, "docs/BETA.md")
    missing = [title for _identifier, title in CORE_FEATURES if title not in text]
    return _check(
        "docs-core-features",
        "the free list on the beta page matches the code",
        ("BETA.md never lists: " + "; ".join(missing)) if missing else None,
        f"{len(CORE_FEATURES)} core features listed",
    )


def check_store_page(root: Path) -> Check:
    text = _read(root, "docs/ITCH_STORE.md")
    problems: list[str] = []
    for required in (
        "# Headwaters (Beta): Never run out of model",
        "## Pitch",
        "## FAQs",
        "## What you need",
    ):
        if required not in text:
            problems.append(f"missing {required!r}")
    if "<!-- store-copy: beta -->" not in text:
        problems.append("the page does not say it is a beta build")
    return _check(
        "docs-store",
        "the itch store page has the copy the ticket asks for",
        "; ".join(problems) if problems else None,
        "title, pitch, FAQs, requirements and the beta label are present",
    )


def check_readme_links(root: Path) -> Check:
    text = _read(root, "README.md")
    missing: list[str] = []
    for target in _LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        relative = target.split("#", 1)[0]
        if relative and not (root / relative).exists():
            missing.append(target)
    return _check(
        "docs-readme-links",
        "every relative link in the README resolves",
        ("unresolved: " + ", ".join(missing)) if missing else None,
        "all relative links resolve",
    )


def public_files(root: Path) -> list[Path]:
    """The files a stranger could read: prose, packaging and the store copy, never tests."""
    found: list[Path] = [root / "README.md"]
    for directory in ("docs", "packaging", "scripts", "integrations", "vscode-extension"):
        base = root / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _PUBLIC_SUFFIXES:
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith(("docs/reports/", "review_bundle_")) or "/." in relative:
                continue
            if relative in {"docs/FOLLOWUPS.md", "docs/ASSUMPTIONS.md"}:
                continue
            found.append(path)
    return [path for path in found if path.is_file()]


def check_open_source(root: Path) -> Check:
    problems: list[str] = []
    for relative in ("THIRD_PARTY_NOTICES.md", "docs/PROVENANCE.md"):
        if not (root / relative).is_file():
            problems.append(f"{relative} is missing")
    scanned = 0
    for path in public_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        scanned += 1
        for pattern in _SECRET_SHAPES:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n")
                fragment = text.splitlines()[line]
                if any(allowed in fragment for allowed in _SECRET_ALLOW):
                    continue
                problems.append(
                    f"{path.relative_to(root).as_posix()}:{line + 1} looks like a secret"
                )
        if _ABSOLUTE_HOME.search(text):
            problems.append(f"{path.relative_to(root).as_posix()} carries a machine path")
    return _check(
        "docs-open-source",
        "open-source spot check: no secret-shaped string, no machine path, notices present",
        "; ".join(problems) if problems else None,
        f"{scanned} public files scanned",
    )


def check_catalog_feed(root: Path, feed: Path | None = None) -> Check:
    problems: list[str] = []
    seed = root / SEED_RELATIVE_PATH
    try:
        seed_data: Any = json.loads(seed.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _check(
            "catalog-feed",
            "FU-121-3: a catalog key reaches clients only behind a schema bump",
            f"the seed catalog cannot be read: {exc}",
            "",
        )
    try:
        Catalog.model_validate(seed_data)
    except Exception as exc:  # pydantic's ValidationError, reported as text on purpose
        problems.append(f"the seed catalog no longer validates: {exc}")
    if "ollama_remote_patterns" not in seed_data:
        problems.append("the seed catalog has no `ollama_remote_patterns`")
    target = feed if feed is not None else root / FEED_RELATIVE
    detail = "seed catalog validates"
    if target.is_file():
        try:
            published: Any = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{target.name} is not JSON: {exc}")
            published = None
        if isinstance(published, dict):
            detail = f"seed catalog validates; {target.name} checked"
            try:
                Catalog.model_validate(published)
            except Exception as exc:
                message = str(exc)
                version = published.get("schema_version")
                extra = "extra_forbidden" in message or "Extra inputs are not permitted" in message
                if extra and isinstance(version, int) and version >= 2:
                    detail = (
                        "seed catalog validates; the published feed carries a newer key behind "
                        f"schema_version {version}, so an old client refuses it"
                    )
                else:
                    problems.append(
                        f"{target.name} carries a key the shipped client rejects while "
                        f"schema_version is {version!r}; bump it to 2 or ship the key first"
                    )
    return _check(
        "catalog-feed",
        "FU-121-3: a catalog key reaches clients only behind a schema bump",
        "; ".join(problems) if problems else None,
        detail,
    )


_PACKAGING = (
    "packaging/innosetup/headwaters.iss",
    "packaging/butler/push_windows_beta.ps1",
    "packaging/butler/push_linux_beta.ps1",
    "packaging/itch/.itch.toml",
    "packaging/linux/install.sh",
    "scripts/build_windows.py",
    "scripts/build_linux.py",
    "scripts/sign_catalog.py",
)


def check_packaging(root: Path) -> Check:
    missing = [
        relative
        for relative in _PACKAGING
        if not (root / relative).is_file() or (root / relative).stat().st_size == 0
    ]
    return _check(
        "packaging-files",
        "the packaging payload and the signed-catalog signer are in the repo",
        ("missing or empty: " + ", ".join(missing)) if missing else None,
        f"{len(_PACKAGING)} packaging files present",
    )


# -- owner steps ------------------------------------------------------------------------------


def _entry_date(entry: dict[str, Any], key: str = "date") -> str | None:
    value = entry.get(key)
    return None if isinstance(value, str) and _DATE.match(value) else f"`{key}` is not YYYY-MM-DD"


def _minutes(entry: dict[str, Any], maximum: float) -> str | None:
    value = entry.get("minutes")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "`minutes` is missing"
    return None if 0 < float(value) <= maximum else f"`minutes` is {value}, not <= {maximum}"


def _gpu(entry: dict[str, Any], minimum: float) -> str | None:
    value = entry.get("gpu_gb")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "`gpu_gb` is missing"
    return None if float(value) >= minimum else f"`gpu_gb` is {value}, not >= {minimum}"


def _filled(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    return None if isinstance(value, str) and value.strip() else f"`{key}` is missing"


def _install(maximum: float, gpu: float) -> Callable[[dict[str, Any]], str | None]:
    def validate(entry: dict[str, Any]) -> str | None:
        for problem in (_minutes(entry, maximum), _gpu(entry, gpu), _entry_date(entry)):
            if problem is not None:
                return problem
        return _filled(entry, "machine")

    return validate


def _two_results(entry: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            return f"`{key}` is missing"
    for problem in (_entry_date(entry),):
        if problem is not None:
            return problem
    if entry.get("result") != "pass":
        return "`result` is not `pass`"
    return None


def _hosts(entry: dict[str, Any]) -> str | None:
    covered = entry.get("hosts")
    if not isinstance(covered, dict):
        return "`hosts` is missing"
    missing = [host for host in host_ids() if not isinstance(covered.get(host), str)]
    if missing:
        return "not tested in " + ", ".join(missing)
    return _entry_date(entry)


def _p1(entry: dict[str, Any]) -> str | None:
    days = entry.get("days")
    if not isinstance(days, (int, float)) or isinstance(days, bool) or float(days) < 7:
        return "`days` is not >= 7"
    if entry.get("p1_open") != 0:
        return "`p1_open` is not 0"
    for key in ("from", "to"):
        value = entry.get(key)
        if not isinstance(value, str) or not _DATE.match(value):
            return f"`{key}` is not YYYY-MM-DD"
    return None


def _signing(entry: dict[str, Any]) -> str | None:
    for key in ("signer", "certificate"):
        problem = _filled(entry, key)
        if problem is not None:
            return problem
    if entry.get("verified") is not True:
        return "`verified` is not true"
    if entry.get("timestamped") is not True:
        return "`timestamped` is not true"
    return _entry_date(entry)


def _upload(channel: str) -> Callable[[dict[str, Any]], str | None]:
    def validate(entry: dict[str, Any]) -> str | None:
        if entry.get("channel") != channel:
            return f"`channel` is not `{channel}`"
        if entry.get("uploaded") is not True:
            return "`uploaded` is not true"
        for key in ("build_id", "date"):
            problem = _filled(entry, key)
            if problem is not None:
                return problem
        return None

    return validate


def _reviewer(entry: dict[str, Any]) -> str | None:
    problem = _filled(entry, "reviewer")
    if problem is not None:
        return problem
    return _entry_date(entry)


def _testers(entry: dict[str, Any]) -> str | None:
    count = entry.get("registered")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return "`registered` is not a positive count"
    return _filled(entry, "system")


_OWNER_STEPS: tuple[tuple[str, str, str, str, Callable[[dict[str, Any]], str | None]], ...] = (
    (
        "install-12gb",
        "10-minute install on a 12 GB machine",
        "minutes <= 10, gpu_gb >= 12, date, machine",
        "docs/BETA.md closed-alpha exit criteria",
        _install(10, 12),
    ),
    (
        "install-16gb",
        "10-minute install on a 16 GB machine",
        "minutes <= 10, gpu_gb >= 16, date, machine",
        "docs/BETA.md closed-alpha exit criteria",
        _install(10, 16),
    ),
    (
        "host-stability",
        "stable in VS Code Copilot BYOK, Cline, OpenCode and Claude Desktop",
        "hosts.vscode/cline/opencode/claude-desktop notes, date",
        "docs/BETA.md closed-alpha exit criteria",
        _hosts,
    ),
    (
        "p1-week",
        "no P1 bug for one week of closed alpha",
        "days >= 7, p1_open = 0, from, to",
        "docs/BETA.md closed-alpha exit criteria",
        _p1,
    ),
    (
        "windows-clean-vm",
        "the Windows payload installs on a clean VM",
        "date, machine, result = pass",
        "docs/RELEASE_CHECKLIST.md section 2",
        lambda entry: _two_results(entry, ("machine",)),
    ),
    (
        "linux-clean-run",
        "the Linux payload installs on a clean distribution",
        "date, distribution, result = pass",
        "docs/RELEASE_CHECKLIST_LINUX.md section 2",
        lambda entry: _two_results(entry, ("distribution",)),
    ),
    (
        "code-signing",
        "the Windows build is Authenticode signed and timestamped",
        "signer, certificate, verified = true, timestamped = true, date",
        "docs/RELEASE_CHECKLIST.md section 0",
        _signing,
    ),
    (
        "itch-windows",
        "the Windows build is uploaded to the windows-beta channel",
        "channel = windows-beta, uploaded = true, build_id, date",
        "docs/RELEASE_CHECKLIST.md section 3",
        _upload("windows-beta"),
    ),
    (
        "itch-linux",
        "the Linux build is uploaded to the linux-beta channel",
        "channel = linux-beta, uploaded = true, build_id, date",
        "docs/RELEASE_CHECKLIST_LINUX.md section 3",
        _upload("linux-beta"),
    ),
    (
        "open-source-review",
        "open-source readiness: license, provenance and notices reviewed by a person",
        "reviewer, date",
        "docs/DECISIONS.md D1",
        _reviewer,
    ),
    (
        "tester-registration",
        "the beta tester list the chosen promise relies on exists",
        "system, registered >= 1",
        "docs/BETA.md tester list",
        _testers,
    ),
)


class EvidenceError(Exception):
    """The evidence file exists but cannot be used at all."""


def _load_evidence(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise EvidenceError(f'{path} needs `"version": 1` at the top')
    steps = data.get("steps")
    if not isinstance(steps, dict):
        raise EvidenceError(f"{path} needs a `steps` object")
    return steps


def owner_steps(root: Path, evidence_path: Path) -> tuple[OwnerStep, ...]:
    steps = _load_evidence(evidence_path)
    found: list[OwnerStep] = []
    for identifier, title, proof, where, validate in _OWNER_STEPS:
        entry = steps.get(identifier)
        if entry is None:
            found.append(
                OwnerStep(
                    identifier, title, proof, where, False, "not recorded in the evidence file"
                )
            )
            continue
        if not isinstance(entry, dict):
            found.append(OwnerStep(identifier, title, proof, where, False, "not a JSON object"))
            continue
        problem = validate(entry)
        found.append(
            OwnerStep(identifier, title, proof, where, problem is None, problem or "recorded")
        )
    return tuple(found)


def run_gate(
    repo_root: Path,
    *,
    evidence_path: Path | None = None,
    feed_path: Path | None = None,
) -> GateResult:
    """Run every check and read the owner evidence. Raises ``EvidenceError`` for a broken file."""
    root = repo_root.resolve()
    evidence = evidence_path or root / EVIDENCE_RELATIVE
    checks = (
        check_required_docs(root),
        check_host_pages(root),
        check_templates(root),
        check_troubleshooting(root),
        check_privacy(root),
        check_promises(root),
        check_star_legend(root),
        check_core_features(root),
        check_store_page(root),
        check_readme_links(root),
        check_open_source(root),
        check_catalog_feed(root, feed_path),
        check_packaging(root),
    )
    warnings = (
        "D1: open-source publication is still undecided, so no LICENSE file is required yet"
        if not (root / "LICENSE").is_file()
        else "D1: LICENSE is still the placeholder, so no license is granted until one is chosen"
        if "placeholder" in (root / "LICENSE").read_text(encoding="utf-8", errors="replace").lower()
        else "D1: a LICENSE file is present",
    )
    return GateResult(
        repo_root=root,
        evidence_path=evidence,
        checks=checks,
        steps=owner_steps(root, evidence),
        warnings=warnings,
    )


def render(result: GateResult) -> str:
    """The report a person reads: what passed, what failed, and what nobody has run yet."""
    passed = len(result.checks) - len(result.failed)
    lines = [
        f"Headwaters beta release gate - {result.repo_root}",
        f"machine checks: {passed}/{len(result.checks)} passed",
        f"owner steps: {len(result.steps) - len(result.blocked)}/{len(result.steps)} recorded",
        f"evidence: {result.evidence_path}",
        "",
    ]
    for check in result.checks:
        mark = "ok  " if check.ok else "FAIL"
        lines.append(f"  {mark}  {check.id}: {check.detail}")
    lines.append("")
    for step in result.steps:
        mark = "ok  " if step.done else "BLOCKED"
        lines.append(f"  {mark}  {step.id}: {step.title}")
        if not step.done:
            lines.append(f"          needs {step.proof}")
            lines.append(f"          run {step.where}; record it in {EVIDENCE_RELATIVE.as_posix()}")
    for warning in result.warnings:
        lines.append(f"  warn  {warning}")
    lines.append("")
    if result.ready:
        lines.append("READY: every machine check passed and every owner step is recorded.")
    else:
        lines.append(
            f"NOT READY: {len(result.failed)} machine check(s) failed, "
            f"{len(result.blocked)} owner step(s) not recorded. Beta 0.1 must not be called "
            "shippable until both are zero."
        )
    return "\n".join(lines)


def as_dict(result: GateResult) -> dict[str, Any]:
    """The same report as JSON, for a release script."""
    return {
        "ok": result.ready,
        "ready": result.ready,
        "repo_root": str(result.repo_root),
        "evidence": str(result.evidence_path),
        "checks": [
            {"id": check.id, "title": check.title, "ok": check.ok, "detail": check.detail}
            for check in result.checks
        ],
        "steps": [
            {
                "id": step.id,
                "title": step.title,
                "done": step.done,
                "reason": step.reason,
                "proof": step.proof,
                "where": step.where,
            }
            for step in result.steps
        ],
        "warnings": list(result.warnings),
        "summary": {
            "checks": len(result.checks),
            "checks_failed": len(result.failed),
            "steps": len(result.steps),
            "steps_blocked": len(result.blocked),
        },
    }
