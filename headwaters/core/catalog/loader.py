"""Load and validate the curated catalog; resolve model ids including unknown models."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from headwaters.core import paths
from headwaters.core.catalog.schema import Catalog, ModelEntry
from headwaters.core.rules_io import ConfigError, format_validation_error

SEED_RELATIVE_PATH = Path("catalog") / "seed_catalog.json"
SCHEMA_RELATIVE_PATH = Path("catalog") / "catalog.schema.json"

__all__ = [
    "SCHEMA_RELATIVE_PATH",
    "SEED_RELATIVE_PATH",
    "Catalog",
    "CatalogIndex",
    "load_catalog",
    "parse_catalog",
    "schema_json",
]


def load_catalog(path: Path) -> Catalog:
    """Read and validate a catalog file. Raises ``ConfigError`` with one line per problem."""
    try:
        text = paths.long_safe(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(path, [f"cannot read file: {exc.strerror or exc}"]) from exc
    return parse_catalog(text, path)


def parse_catalog(text: str, source: Path) -> Catalog:
    """Validate catalog JSON text; ``source`` is only used in error messages."""
    path = source
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(path, [f"JSON syntax error at line {exc.lineno}: {exc.msg}"]) from exc
    if not isinstance(data, dict):
        raise ConfigError(path, ["top level must be an object"])
    try:
        return Catalog.model_validate(data)
    except ValidationError as exc:
        problems = []
        for line, item in zip(format_validation_error(exc), exc.errors(), strict=True):
            problems.append(line.removeprefix("(root): ") if item["loc"] == () else line)
        raise ConfigError(path, problems) from exc


class CatalogIndex:
    """Lookup by id or alias. Unknown models get conservative defaults and ``unscored``."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._by_name: dict[str, ModelEntry] = {}
        for entry in catalog.models:
            for name in (entry.id, *entry.aliases):
                self._by_name.setdefault(name.lower(), entry)

    def find(self, name: str) -> ModelEntry | None:
        return self._by_name.get(name.lower())

    def resolve(self, name: str, provider: str = "unknown") -> ModelEntry:
        known = self.find(name)
        if known is not None:
            return known
        defaults = self.catalog.unknown_model_defaults
        return ModelEntry(
            id=name,
            provider=provider,
            tools_ok=defaults.tools_ok,
            json_ok=defaults.json_ok,
            ctx=defaults.ctx,
            scores=defaults.scores.model_copy(),
            unscored=True,
        )


def schema_json() -> str:
    """Deterministic JSON Schema text for the catalog (LF endings, trailing newline)."""
    schema = Catalog.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Headwaters catalog"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """``python -m headwaters.core.catalog.loader [--write|--check] [PATH]`` (schema file)."""
    parser = argparse.ArgumentParser(prog="headwaters.core.catalog.loader")
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
