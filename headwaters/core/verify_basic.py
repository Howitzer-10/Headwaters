"""Basic output verification (HDW-116): the cheap checks that make downgrading safe.

``BasicVerifier`` implements ``VerifierPlugin`` (``verify`` returns rendered reasons) and adds
``verify_report`` for a structured ``VerifyReport`` with machine-readable reason codes.

Checks: truncated fences, placeholder/laziness scan (regexes come from the catalog's
``placeholder_patterns``, none live here), tree-sitter parse (``ast.parse`` too for Python), JSON
parse and JSON Schema, ``git apply --check`` for diffs (CRLF/LF aware) and tool-call arguments.
Line endings are normalized to LF before scanning and parsing. Reports never contain candidate text.
"""

from __future__ import annotations

import ast
import json
import re
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from headwaters.core.catalog.schema import Catalog, NamedPattern
from headwaters.core.plugins import CURRENT_API_VERSION
from headwaters.core.verify_diff import check_diff
from headwaters.core.verify_fences import Block, looks_like_diff, normalize_eol, split_fences
from headwaters.core.verify_schema import check_json_schema, check_tool_calls
from headwaters.core.verify_types import CheckResult, Collector, Reason, VerifyReport

__all__ = [
    "GRAMMARS",
    "BasicVerifier",
    "CheckResult",
    "Reason",
    "VerifyReport",
    "looks_like_diff",
    "normalize_eol",
]

# Canonical language -> tree-sitter grammar. Aliases ("py", "ts", "c++") come from the catalog's
# ``analyzer_hints.fence_aliases``. tsx is a grammar of its own in tree-sitter.
GRAMMARS: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "csharp": "csharp",
    "c": "c",
    "cpp": "cpp",
    "json": "json",
    "yaml": "yaml",
}
_DIFF_LANGS = frozenset({"diff", "patch"})


@dataclass(frozen=True)
class _Unit:
    language: str | None  # canonical language, "diff", or None when unknown
    body: str
    offset: int  # add to a 1-based line within ``body`` to get the candidate line


@cache
def _parser(grammar: str) -> Any:
    from tree_sitter_language_pack import get_parser

    return get_parser(grammar)


def _first_error(root: Any) -> tuple[int, str]:
    """1-based line and kind (``error`` or ``missing``) of the first broken node."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_error:
            return int(node.start_point[0]) + 1, "error"
        if node.is_missing:
            return int(node.start_point[0]) + 1, "missing"
        if node.has_error:
            stack.extend(reversed(node.children))
    return 1, "error"


class BasicVerifier:
    """The core verifier. Build it with ``from_catalog`` so patterns come from catalog data."""

    api_version = CURRENT_API_VERSION

    def __init__(
        self,
        placeholder_patterns: Sequence[NamedPattern] = (),
        *,
        fence_aliases: Mapping[str, str] | None = None,
        repo: Path | None = None,
        git_timeout: float = 15.0,
    ) -> None:
        self._patterns = [(p.name, re.compile(p.regex)) for p in placeholder_patterns]
        self._aliases = {k.lower(): v.lower() for k, v in (fence_aliases or {}).items()}
        self._repo = repo
        self._git_timeout = git_timeout

    @classmethod
    def from_catalog(
        cls, catalog: Catalog, *, repo: Path | None = None, git_timeout: float = 15.0
    ) -> BasicVerifier:
        return cls(
            catalog.placeholder_patterns,
            fence_aliases=catalog.analyzer_hints.fence_aliases,
            repo=repo,
            git_timeout=git_timeout,
        )

    def verify(self, candidate: str) -> list[str]:
        """``VerifierPlugin`` entry point: rendered reasons, empty when the candidate passed."""
        return [reason.render() for reason in self.verify_report(candidate).reasons]

    def verify_report(
        self,
        candidate: str,
        *,
        language: str | None = None,
        json_schema: Mapping[str, Any] | None = None,
        tool_calls: Sequence[Mapping[str, Any]] | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        repo: Path | None = None,
    ) -> VerifyReport:
        """Run every applicable check.

        ``language`` treats an unfenced candidate as that language; ``json_schema`` validates the
        JSON in the candidate; ``tool_calls`` (+ ``tools`` definitions) validates tool calls;
        ``repo`` overrides the repository used for ``git apply --check``.
        """
        out = Collector()
        text = normalize_eol(candidate)
        blocks = split_fences(text, self._resolve_language)

        if blocks:
            out.ran("fences")
        for block in blocks:
            if not block.closed:
                out.fail(
                    "fences",
                    "truncated_fence",
                    "code fence is never closed (output looks cut off)",
                    block.start_line,
                )

        units = self._units(text, blocks, language)
        self._scan_placeholders(out, units)
        git_repo = repo or self._repo
        for unit in units:
            if unit.language == "diff":
                check_diff(out, unit.body, git_repo, unit.offset or None, self._git_timeout)
            elif unit.language == "json":
                self._check_json_syntax(out, unit)
            elif unit.language in GRAMMARS:
                self._check_parse(out, unit)
        if json_schema is not None:
            check_json_schema(out, text, blocks, json_schema)
        if tool_calls is not None:
            check_tool_calls(out, tool_calls, tools)
        return out.report()

    def _resolve_language(self, info: str) -> str | None:
        words = info.split()
        token = words[0].strip("{}.").lower() if words else ""
        token = self._aliases.get(token, token)
        if token in _DIFF_LANGS:
            return "diff"
        return token if token in GRAMMARS else None

    def _units(self, text: str, blocks: list[Block], language: str | None) -> list[_Unit]:
        """Code to check. Unclosed fences are reported as truncated, not parsed (only noise)."""
        if blocks:
            return [_Unit(b.language, b.body, b.start_line) for b in blocks if b.closed]
        if language is not None:
            return [_Unit(self._resolve_language(language), text, 0)]
        if looks_like_diff(text):
            return [_Unit("diff", text, 0)]
        return []

    def _scan_placeholders(self, out: Collector, units: list[_Unit]) -> None:
        if not self._patterns:
            out.skip("placeholders", "no placeholder patterns configured")
            return
        if not units:
            out.skip("placeholders", "no code to scan")
            return
        out.ran("placeholders")
        for unit in units:
            for number, line in enumerate(unit.body.split("\n"), start=1):
                if unit.language == "diff" and not (line.startswith("+") and line[:3] != "+++"):
                    continue  # only lines the diff adds
                for name, regex in self._patterns:
                    if regex.search(line):
                        out.fail(
                            "placeholders",
                            "placeholder",
                            f"placeholder or lazy-output pattern {name!r}",
                            unit.offset + number,
                        )
                        break

    def _check_parse(self, out: Collector, unit: _Unit) -> None:
        language = unit.language or ""
        body = textwrap.dedent(unit.body) if language == "python" else unit.body
        if not body.strip():
            return
        if language == "python":
            out.ran("parse")
            try:
                ast.parse(body)
            except SyntaxError as exc:
                out.fail(
                    "parse", "syntax_error", f"python: {exc.msg}", unit.offset + (exc.lineno or 1)
                )
                return
            except ValueError as exc:  # e.g. null bytes
                out.fail("parse", "syntax_error", f"python: {exc}")
                return
        try:
            tree = _parser(GRAMMARS[language]).parse(body.encode("utf-8"))
        except Exception as exc:  # grammar missing or parser crash: never fail the candidate
            out.skip("parse", f"{language}: tree-sitter unavailable ({type(exc).__name__})")
            return
        out.ran("parse")
        if tree.root_node.has_error:
            line, kind = _first_error(tree.root_node)
            out.fail(
                "parse",
                "syntax_error",
                f"{language}: tree-sitter found a {kind} node",
                unit.offset + line,
            )

    @staticmethod
    def _check_json_syntax(out: Collector, unit: _Unit) -> None:
        out.ran("json")
        try:
            json.loads(unit.body)
        except json.JSONDecodeError as exc:
            out.fail("json", "json_invalid", exc.msg, unit.offset + exc.lineno)
