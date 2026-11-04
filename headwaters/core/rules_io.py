"""Reading, validating, defaulting and schema export for config files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from headwaters.core import paths
from headwaters.core.rules import Rules

RULES_FILE_NAME = "rules.yaml"
SCHEMA_RELATIVE_PATH = Path("catalog") / "rules.schema.json"


class ConfigError(ValueError):
    """A config file could not be read or failed validation; the message is user-facing."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        super().__init__(f"Invalid config {path}:\n" + "\n".join(f"  - {p}" for p in problems))


def format_validation_error(error: ValidationError) -> list[str]:
    """One readable line per problem, e.g. ``gateway.port: Input should be ...``."""
    lines: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"{location}: {item['msg']}")
    return lines


def read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read ``path`` as a YAML mapping; an empty file is an empty mapping."""
    try:
        text = paths.long_safe(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(path, [f"cannot read file: {exc.strerror or exc}"]) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        problem = getattr(exc, "problem", None) or str(exc)
        raise ConfigError(path, [f"YAML syntax error at {where}{problem}"]) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(path, ["top level must be a mapping of settings"])
    return data


def rules_path() -> Path:
    return paths.config_dir() / RULES_FILE_NAME


def load_rules(path: Path | None = None) -> Rules:
    """Load and validate ``rules.yaml`` (default: the config dir). Raises ``ConfigError``."""
    target = path or rules_path()
    data = read_yaml_mapping(target)
    try:
        return Rules.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(target, format_validation_error(exc)) from exc


_HEADER = (
    "# Headwaters rules. Edit and save: changes apply without a restart.\n"
    "# An invalid edit is reported and the last good settings stay active.\n"
    "#\n"
    "# Only settings you choose are written below. A setting you leave out keeps the\n"
    "# installed version's default, so a later release can change that default instead of\n"
    "# this file pinning the old value. To choose one, copy its line from the reference,\n"
    "# remove the leading '# ' and keep the nesting. Quote a word YAML reads as a boolean\n"
    '# (off, on, yes, no): write "off", never bare off.\n'
)

_REFERENCE = (
    "#\n"
    "# Reference (example values; the live defaults are in catalog/rules.schema.json):\n"
    "#\n"
    "#   default_scope: auto                 # auto | free | local | cloud\n"
    "#   strategy: cost_saver                # cost_saver | quality_first | primary_first\n"
    "#   private_lock: false                 # no cloud egress while on\n"
    "#   airplane: false\n"
    "#   verify: agentic                     # always | agentic | off\n"
    '#   advice: "off"                       # "off" | inline | header_only\n'
    "#   secrets:\n"
    "#     action: local                     # local | refuse | off\n"
    "#   tiers:\n"
    "#     local:\n"
    "#       host: http://localhost:11434\n"
    "#       keep_alive: 30m                 # Go duration; Ollama unloads after this\n"
    '#       keep_warm_model: "off"          # "off" | auto | <model name>\n'
    "#       keep_warm_heartbeat: false\n"
    "#     paid:\n"
    "#       models: []\n"
    "#   budget:\n"
    "#     paid_daily_usd: 5.0\n"
    "#   gateway:\n"
    "#     port: 4000\n"
    "#   dashboard:\n"
    "#     port: 4001\n"
    "#   models:\n"
    "#     aliases: {}                       # gpt-4o: headwaters/free\n"
    "#   workspace:\n"
    "#     roots: []                         # folders the file tools may reach\n"
    "#   features: {}                        # <feature>: true\n"
)


def render_rules(rules: Rules) -> str:
    """File text for ``rules``: only the settings it actually chose.

    ``exclude_defaults`` is what makes a default change reach an install (D21): a field left at
    its default is not written, so the file does not pin it. The reference in the header keeps
    the file a usable starting point without persisting a single default.
    """
    chosen = rules.model_dump(mode="json", exclude_defaults=True)
    if not chosen:
        return _HEADER + _REFERENCE
    body = yaml.safe_dump(chosen, sort_keys=False, allow_unicode=True)
    return _HEADER + _REFERENCE + "\n" + body


def ensure_rules_file(path: Path | None = None) -> Path:
    """Write the default ``rules.yaml`` on first run. Never overwrites an existing file."""
    target = path or rules_path()
    safe = paths.long_safe(target)
    if not safe.exists():
        safe.parent.mkdir(parents=True, exist_ok=True)
        with safe.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(render_rules(Rules()))
    return target


def schema_json() -> str:
    """Deterministic JSON Schema text for ``Rules`` (LF endings, trailing newline)."""
    schema = Rules.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Headwaters rules.yaml"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """``python -m headwaters.core.rules_io [--write|--check] [PATH]``."""
    parser = argparse.ArgumentParser(prog="headwaters.core.rules_io")
    parser.add_argument("--check", action="store_true", help="fail if PATH differs from generated")
    parser.add_argument("--write", action="store_true", help="write the generated schema to PATH")
    parser.add_argument("path", nargs="?", type=Path, default=SCHEMA_RELATIVE_PATH)
    args = parser.parse_args(argv)
    generated = schema_json()
    if args.check:
        current = None
        if args.path.exists():
            current = args.path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        if current != generated:
            sys.stderr.write(f"{args.path} is out of date; run with --write and commit it.\n")
            return 1
        return 0
    if args.write:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        with args.path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(generated)
        return 0
    sys.stdout.write(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
