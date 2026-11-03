"""Build steps for the Windows installer and the itch payload (HDW-405).

The script is deliberately boring: it stages the payload, compiles the Inno script and signs the
result *if* the owner has supplied a certificate. Nothing is downloaded, nothing is frozen, and
no secret is ever committed: the certificate path and password come from the environment, and a
missing certificate skips signing with a message instead of failing the build.

``butler push`` is not called here. ``packaging/butler/push_windows_beta.ps1`` does that, by hand,
because an upload is a release decision rather than a build step.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import headwaters
from headwaters.packaging import itch
from headwaters.packaging.layout import (
    CATALOG_DIR_NAME,
    LOCK_FILE_NAME,
    PROJECT_FILE_NAME,
    SOURCE_DIR_NAME,
    UV_EXE_NAME,
)
from headwaters.packaging.runtime import missing_entries

CHANNEL = "windows-beta"
ISCC_ENV = "HEADWATERS_ISCC"
UV_ENV = "HEADWATERS_UV"
CERT_ENV = "HEADWATERS_CERT_PATH"
CERT_PASSWORD_ENV = "HEADWATERS_CERT_PASSWORD"
SIGNTOOL_ENV = "HEADWATERS_SIGNTOOL"
TIMESTAMP_ENV = "HEADWATERS_TIMESTAMP_URL"
DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"
DEFAULT_SIGNTOOL = "signtool.exe"

SETUP_BASENAME = "HeadwatersSetup-{version}.exe"
STAGE_DIR = Path("build") / "windows"
DIST_DIR = Path("dist")


@dataclass(frozen=True)
class CopyStep:
    """One entry of the payload: a file, a tree, or generated text."""

    kind: str
    source: Path | None
    target: Path
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": None if self.source is None else str(self.source),
            "target": str(self.target),
        }


def find_uv(env: Mapping[str, str] | None = None, *, which: Any = None) -> Path | None:
    """The uv binary to ship: ``HEADWATERS_UV`` first, then ``uv`` on PATH."""
    source = os.environ if env is None else env
    override = (source.get(UV_ENV) or "").strip()
    if override:
        return Path(override)
    finder = shutil.which if which is None else which
    found = finder("uv")
    return Path(found) if found else None


def find_iscc(
    env: Mapping[str, str] | None = None, *, is_file: Callable[[str], bool] = os.path.isfile
) -> Path | None:
    """Inno Setup's compiler: ``HEADWATERS_ISCC`` first, then the usual install folders."""
    source = os.environ if env is None else env
    override = (source.get(ISCC_ENV) or "").strip()
    if override:
        return Path(override)
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Inno Setup 6"
        / "ISCC.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    ]
    for candidate in candidates:
        if is_file(str(candidate)):
            return candidate
    return None


def payload_steps(repo_root: Path, payload_dir: Path, *, uv: Path | None = None) -> list[CopyStep]:
    """What goes into ``runtime/``: the lockfile, pyproject, uv, the package and the catalog."""
    root = Path(repo_root)
    uv_path = uv if uv is not None else find_uv()
    if uv_path is None:
        raise FileNotFoundError(f"uv not found: set {UV_ENV} or put uv on PATH")
    return [
        CopyStep("file", root / PROJECT_FILE_NAME, payload_dir / PROJECT_FILE_NAME),
        CopyStep("file", root / LOCK_FILE_NAME, payload_dir / LOCK_FILE_NAME),
        CopyStep("file", Path(uv_path), payload_dir / UV_EXE_NAME),
        CopyStep("tree", root / SOURCE_DIR_NAME, payload_dir / SOURCE_DIR_NAME),
        CopyStep("tree", root / CATALOG_DIR_NAME, payload_dir / CATALOG_DIR_NAME),
    ]


def itch_steps(payload_dir: Path, stage_dir: Path) -> list[CopyStep]:
    """The itch payload: the same runtime layout plus the manifest marker that marks it managed."""
    return [
        CopyStep("tree", Path(payload_dir), Path(stage_dir) / "runtime"),
        CopyStep("text", None, Path(stage_dir) / itch.MARKER_NAME, itch.render_manifest()),
    ]


def _ignore(directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith(".pyc")}


def apply(steps: Sequence[CopyStep], *, copytree: Any = None, copy: bool = True) -> list[Path]:
    """Run the copy steps into a staging area; the only writer in this module."""
    tree_copy = shutil.copytree if copytree is None else copytree
    written: list[Path] = []
    for step in steps:
        step.target.parent.mkdir(parents=True, exist_ok=True)
        if step.kind == "text":
            step.target.write_text(step.text, encoding="utf-8")
        elif step.kind == "tree":
            assert step.source is not None
            if step.target.exists():
                shutil.rmtree(step.target)
            tree_copy(step.source, step.target, ignore=_ignore)
        else:
            assert step.source is not None
            if copy:
                shutil.copy2(step.source, step.target)
        written.append(step.target)
    return written


def iscc_command(
    iscc: Path, iss: Path, *, version: str, payload_dir: Path, output_dir: Path
) -> list[str]:
    """The Inno compiler call; version and paths are preprocessor defines, not file edits."""
    return [
        str(iscc),
        f"/DAppVersion={version}",
        f"/DSourceDir={Path(payload_dir).resolve()}",
        f"/DOutputDir={Path(output_dir).resolve()}",
        str(iss),
    ]


@dataclass(frozen=True)
class SigningPlan:
    """Whether the setup executable gets signed, and with which (redactable) command."""

    enabled: bool
    argv: tuple[str, ...]
    reason: str
    password_index: int | None = None

    def redacted(self) -> str:
        """The command line with the certificate password replaced, for logs and reports."""
        parts = list(self.argv)
        if self.password_index is not None:
            parts[self.password_index] = "***"
        return subprocess.list2cmdline(parts)

    def as_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "reason": self.reason, "command": self.redacted()}


def signing_plan(env: Mapping[str, str] | None, files: Sequence[Path | str]) -> SigningPlan:
    """Code signing is a hook: no certificate in the environment means no signing, no failure."""
    source = os.environ if env is None else env
    cert = (source.get(CERT_ENV) or "").strip()
    if not cert:
        return SigningPlan(
            enabled=False,
            argv=(),
            reason=(
                f"signing skipped: set {CERT_ENV} (and {CERT_PASSWORD_ENV} if the certificate has "
                "a password) to sign the setup executable"
            ),
        )
    tool = (source.get(SIGNTOOL_ENV) or "").strip() or DEFAULT_SIGNTOOL
    timestamp = (source.get(TIMESTAMP_ENV) or "").strip() or DEFAULT_TIMESTAMP
    argv = [tool, "sign", "/fd", "sha256", "/f", cert, "/tr", timestamp, "/td", "sha256"]
    password_index: int | None = None
    password = source.get(CERT_PASSWORD_ENV) or ""
    if password:
        argv.append("/p")
        password_index = len(argv)
        argv.append(password)
    argv.extend(str(Path(name)) for name in files)
    return SigningPlan(
        enabled=True,
        argv=tuple(argv),
        reason=f"signing with the certificate in {CERT_ENV}",
        password_index=password_index,
    )


def butler_command(user: str, game: str, stage_dir: Path, *, channel: str = CHANNEL) -> list[str]:
    return ["butler", "push", str(stage_dir), f"{user}/{game}:{channel}"]


def main(argv: Sequence[str] | None = None, *, runner: Any = None) -> int:
    """Stage, compile and (when a certificate exists) sign. Never uploads."""
    parser = argparse.ArgumentParser(prog="headwaters.packaging.build", description=__doc__)
    parser.add_argument("--version", default=headwaters.__version__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--skip-iscc", action="store_true", help="stage only, do not compile")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.repo_root).resolve()
    stage = root / STAGE_DIR
    payload_dir = stage / "payload"
    itch_dir = stage / "itch"
    dist = root / DIST_DIR
    run = runner if runner is not None else subprocess.run

    uv = find_uv()
    steps = payload_steps(root, payload_dir, uv=uv)
    itch_plan = itch_steps(payload_dir, itch_dir)
    setup = dist / SETUP_BASENAME.format(version=args.version)
    signing = signing_plan(os.environ, [setup])

    if args.dry_run:
        for step in [*steps, *itch_plan]:
            print(f"{step.kind:5} {step.source or '(generated)'} -> {step.target}")
        print(f"sign: {signing.reason}")
        return 0

    if payload_dir.exists():
        shutil.rmtree(payload_dir)
    written = apply(steps)
    print(f"staged {len(written)} entries in {payload_dir}")
    absent = missing_entries(payload_dir)
    if absent:
        print(f"error: payload is incomplete, missing {', '.join(absent)}", file=sys.stderr)
        return 1
    apply(itch_plan)
    print(f"staged the itch payload in {itch_dir}")

    if not args.skip_iscc:
        iscc = find_iscc()
        if iscc is None:
            print(f"error: ISCC.exe not found; set {ISCC_ENV}", file=sys.stderr)
            return 1
        command = iscc_command(
            iscc,
            root / "packaging" / "innosetup" / "headwaters.iss",
            version=args.version,
            payload_dir=payload_dir,
            output_dir=dist,
        )
        print(subprocess.list2cmdline(command))
        code = int(getattr(run(command, check=False), "returncode", 0))
        if code != 0:
            print(f"error: ISCC failed with {code}", file=sys.stderr)
            return code

    print(signing.reason)
    if signing.enabled and setup.exists():
        print(signing.redacted())
        code = int(getattr(run(list(signing.argv), check=False), "returncode", 0))
        if code != 0:
            print(f"error: signtool failed with {code}", file=sys.stderr)
            return code

    print("next, by hand: packaging/butler/push_windows_beta.ps1 (never from pytest or CI)")
    return 0


if __name__ == "__main__":  # pragma: no cover - release entry point
    sys.exit(main())
