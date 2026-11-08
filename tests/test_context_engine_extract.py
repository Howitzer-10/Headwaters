"""HDW-601: tree-sitter extraction - definitions, references, imports, placeholder summary."""

from __future__ import annotations

import pytest

from headwaters.features.context_engine.extract import (
    extract,
    language_for,
    summarise,
)


@pytest.mark.parametrize(
    ("rel", "language"),
    [
        ("a.py", "python"),
        ("a.js", "javascript"),
        ("a.jsx", "javascript"),
        ("a.mjs", "javascript"),
        ("a.ts", "typescript"),
        ("a.tsx", "tsx"),
        ("a.go", "go"),
        ("a.rs", "rust"),
        ("a.java", "java"),
        ("a.cs", "csharp"),
        ("a.c", "c"),
        ("a.h", "c"),
        ("a.cpp", "cpp"),
        ("a.hpp", "cpp"),
        ("SRC/MODULE.PY", "python"),
    ],
)
def test_language_for_maps_each_suffix(rel: str, language: str) -> None:
    assert language_for(rel) == language


@pytest.mark.parametrize("rel", ["README.md", "notes.txt", "Makefile", "a.json", "x.unknownext"])
def test_language_for_ignores_what_it_cannot_parse(rel: str) -> None:
    assert language_for(rel) is None


def _extract(language: str, text: str):
    return extract(language, text.encode("utf-8"))


def test_python_definitions_references_and_imports() -> None:
    result = _extract(
        "python",
        '"""Tally module."""\n'
        "import os\n"
        "import json as js\n"
        "from pathlib import Path\n"
        "\n"
        "\n"
        "class Tally:\n"
        "    def add(self, value):\n"
        "        return stamp(value)\n"
        "\n"
        "\n"
        "def stamp(value):\n"
        "    return os.path.basename(str(Path(value)))\n",
    )
    assert result.error == ""
    assert [(d.name, d.kind) for d in result.definitions] == [
        ("Tally", "class"),
        ("add", "method"),
        ("stamp", "function"),
    ]
    stamp = result.definitions[-1]
    assert (stamp.line, stamp.end_line) == (12, 13)
    assert stamp.signature == "def stamp(value)"
    assert {r.name for r in result.references} == {"stamp", "basename", "str", "Path"}
    assert [i.module for i in result.imports] == ["os", "json", "pathlib"]
    assert result.summary == "Tally module."


def test_javascript_definitions_and_relative_import() -> None:
    result = _extract(
        "javascript",
        "// Widget module.\n"
        "import { stamp } from './py.js';\n"
        "import * as ns from './ns.js';\n"
        "export function draw(n) {\n"
        "  return stamp(n);\n"
        "}\n"
        "class Box {\n"
        "  area() { return 1; }\n"
        "}\n",
    )
    assert [(d.name, d.kind) for d in result.definitions] == [
        ("draw", "function"),
        ("Box", "class"),
        ("area", "method"),
    ]
    assert [i.module for i in result.imports] == ["./py.js", "./ns.js"]
    assert [r.name for r in result.references] == ["stamp"]
    assert result.summary == "Widget module."


def test_typescript_declarations_and_re_export() -> None:
    result = _extract(
        "typescript",
        "import type { Thing } from './types';\n"
        "export * from './more';\n"
        "export interface Shape { area(): number; }\n"
        "export type Id = string;\n"
        "export enum Kind { A, B }\n"
        "export function areaOf(s: Shape): number { return s.area(); }\n",
    )
    assert {(d.name, d.kind) for d in result.definitions} >= {
        ("Shape", "interface"),
        ("Id", "type"),
        ("Kind", "enum"),
        ("areaOf", "function"),
    }
    assert [i.module for i in result.imports] == ["./types", "./more"]
    assert [r.name for r in result.references] == ["area"]


def test_go_functions_methods_and_import_path() -> None:
    result = _extract(
        "go",
        "package main\n"
        "\n"
        'import "fmt"\n'
        "\n"
        "func main() {\n"
        "\tfmt.Println(stamp(1))\n"
        "}\n"
        "\n"
        "type Point struct{ X int }\n"
        "\n"
        "func (p Point) Move() int { return p.X }\n"
        "\n"
        "func stamp(n int) int { return n }\n",
    )
    names = {(d.name, d.kind) for d in result.definitions}
    assert ("main", "function") in names
    assert ("stamp", "function") in names
    assert ("Point", "type") in names
    assert ("Move", "method") in names
    assert [i.module for i in result.imports] == ["fmt"]
    assert {r.name for r in result.references} >= {"Println", "stamp"}


def test_rust_items_and_use_declaration() -> None:
    result = _extract(
        "rust",
        "//! Rust sample.\n"
        "use std::fmt;\n"
        "\n"
        "pub struct Point { x: i32 }\n"
        "\n"
        "pub fn compute(n: u32) -> u32 { helper(n) }\n"
        "\n"
        "fn helper(n: u32) -> u32 { n + 1 }\n",
    )
    assert {(d.name, d.kind) for d in result.definitions} >= {
        ("Point", "struct"),
        ("compute", "function"),
        ("helper", "function"),
    }
    assert [i.module for i in result.imports] == ["std::fmt"]
    assert [r.name for r in result.references] == ["helper"]
    assert result.summary == "Rust sample."


def test_java_and_csharp_imports_lose_their_keyword() -> None:
    java = _extract(
        "java",
        "// Java sample.\n"
        "import java.util.List;\n"
        "import static java.util.Arrays.asList;\n"
        "\n"
        "public class Thing {\n"
        "    public int size() { return helper(); }\n"
        "    private int helper() { return 1; }\n"
        "}\n",
    )
    assert [i.module for i in java.imports] == ["java.util.List", "java.util.Arrays.asList"]
    assert {(d.name, d.kind) for d in java.definitions} == {
        ("Thing", "class"),
        ("size", "method"),
        ("helper", "method"),
    }
    assert [r.name for r in java.references] == ["helper"]

    csharp = _extract(
        "csharp",
        "// C# sample.\n"
        "using System;\n"
        "global using System.Text;\n"
        "\n"
        "public class Widget {\n"
        "    public int Size => Helper();\n"
        "    private int Helper() { return 1; }\n"
        "}\n",
    )
    assert [i.module for i in csharp.imports] == ["System", "System.Text"]
    assert {"Widget", "Helper"} <= {d.name for d in csharp.definitions}


def test_c_and_cpp_includes_are_paths() -> None:
    c = _extract(
        "c",
        "/* C sample. */\n"
        "#include <stdio.h>\n"
        '#include "local.h"\n'
        "\n"
        "int helper(int n) { return n + 1; }\n"
        "\n"
        "int main(void) { return helper(1); }\n",
    )
    assert [i.module for i in c.imports] == ["stdio.h", "local.h"]
    assert [d.name for d in c.definitions] == ["helper", "main"]
    assert [r.name for r in c.references] == ["helper"]
    assert c.summary == "C sample."

    cpp = _extract(
        "cpp",
        "#include <vector>\n"
        "\n"
        "namespace demo {\n"
        "class Box { public: int area(); };\n"
        "int Box::area() { return 1; }\n"
        "}\n",
    )
    assert [i.module for i in cpp.imports] == ["vector"]
    assert {(d.name, d.kind) for d in cpp.definitions} >= {
        ("demo", "namespace"),
        ("Box", "class"),
        ("area", "function"),
    }


@pytest.mark.parametrize(
    ("text", "summary"),
    [
        ('"""One line docstring."""\nimport os\n', "One line docstring."),
        ("'''Single quoted docstring.'''\n", "Single quoted docstring."),
        ("// Line comment.\nfn main() {}\n", "Line comment."),
        ("/* Block comment. */\nint x;\n", "Block comment."),
        ("# Hash comment.\nx = 1\n", "Hash comment."),
        ("-- Lua style comment.\n", "Lua style comment."),
        ("<!-- Markup comment. -->\n", "Markup comment. -->"),
        ("", ""),
        ("import os\n", ""),
        ("\n\n\n# Only a comment on line four.\n", "Only a comment on line four."),
    ],
)
def test_summarise_reads_the_first_comment_line(text: str, summary: str) -> None:
    assert summarise(text) == summary


def test_summary_is_cut_at_the_limit() -> None:
    from headwaters.features.context_engine.extract import SUMMARY_MAX_CHARS

    assert len(summarise("# " + "x" * 500)) == SUMMARY_MAX_CHARS


def test_unknown_language_reports_an_error_instead_of_raising() -> None:
    result = extract("no-such-grammar", b"anything\n")
    assert result.error
    assert result.definitions == []
