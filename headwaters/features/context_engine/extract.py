"""Read tree-sitter definitions, call references, imports and a summary line (HDW-601).

One extraction pass per file produces every row the index stores, so a file is parsed once no
matter which of them a query later asks for. Nothing here reads the file system: the caller
decodes bytes and owns the size and language limits.

The tables are data, not code: a language is described by the node types its grammar gives a
name to (``DEFINITIONS``), the node type of a call (``CALLS``) and the node type that starts an
import (``IMPORTS``). A language missing from those maps is simply not indexed, and a node type a
grammar renames produces no row rather than an error, because a stale grammar name must never
make the index fail to build.

The file "summary" is a placeholder: the first docstring or comment line of the file, cut to
``SUMMARY_MAX_CHARS``. A real summary belongs to the context engine that replaces ``pack_files``
(HDW-602); storing this line costs one pass and gives that ticket a column and a shape to fill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter_language_pack import get_parser

#: Suffix to grammar name. A suffix that is ambiguous in the wild (``.h``) takes the cheaper
#: guess; both C and C++ grammars accept a header either way.
LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
}

_JS_DEFINITIONS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "method_definition": "method",
    "variable_declarator": "variable",
}

_TS_DEFINITIONS = {
    **_JS_DEFINITIONS,
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "abstract_class_declaration": "class",
    "function_signature": "function",
    "method_signature": "method",
    "property_signature": "property",
}

#: Node type -> the ``kind`` value stored for it.
DEFINITIONS: dict[str, dict[str, str]] = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "javascript": _JS_DEFINITIONS,
    "typescript": _TS_DEFINITIONS,
    "tsx": _TS_DEFINITIONS,
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
        "var_spec": "variable",
        "const_spec": "const",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "mod_item": "module",
        "type_item": "type",
        "const_item": "const",
        "static_item": "static",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "record",
        "annotation_type_declaration": "annotation",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "csharp": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "struct_declaration": "struct",
        "enum_declaration": "enum",
        "record_declaration": "record",
        "namespace_declaration": "namespace",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
        "property_declaration": "property",
    },
    "c": {
        "function_definition": "function",
        "struct_specifier": "struct",
        "enum_specifier": "enum",
        "union_specifier": "union",
        "type_definition": "type",
    },
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
        "enum_specifier": "enum",
        "union_specifier": "union",
        "namespace_definition": "namespace",
        "type_definition": "type",
    },
}

#: Node type of a call, and the field that holds the callee.
CALLS: dict[str, tuple[str, str]] = {
    "python": ("call", "function"),
    "javascript": ("call_expression", "function"),
    "typescript": ("call_expression", "function"),
    "tsx": ("call_expression", "function"),
    "go": ("call_expression", "function"),
    "rust": ("call_expression", "function"),
    "java": ("method_invocation", "name"),
    "csharp": ("invocation_expression", "function"),
    "c": ("call_expression", "function"),
    "cpp": ("call_expression", "function"),
}

IMPORTS: dict[str, frozenset[str]] = {
    "python": frozenset({"import_statement", "import_from_statement"}),
    "javascript": frozenset({"import_statement", "export_statement"}),
    "typescript": frozenset({"import_statement", "export_statement"}),
    "tsx": frozenset({"import_statement", "export_statement"}),
    "go": frozenset({"import_spec"}),
    "rust": frozenset({"use_declaration"}),
    "java": frozenset({"import_declaration"}),
    "csharp": frozenset({"using_directive", "global_using_directive"}),
    "c": frozenset({"preproc_include"}),
    "cpp": frozenset({"preproc_include"}),
}

NAME_NODE_TYPES = frozenset(
    {
        "identifier",
        "field_identifier",
        "type_identifier",
        "property_identifier",
        "namespace_identifier",
        "constant",
        "type_spec",
    }
)

_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_COMMENT_PREFIXES = ("#", "//", "/*", "*", "--", "<!--")
SUMMARY_MAX_CHARS = 200
SIGNATURE_MAX_CHARS = 200
_MAX_DEPTH = 400


@dataclass(frozen=True)
class Definition:
    name: str
    kind: str
    line: int
    end_line: int
    signature: str = ""


@dataclass(frozen=True)
class Reference:
    name: str
    line: int


@dataclass(frozen=True)
class Import:
    module: str
    line: int


@dataclass
class ExtractResult:
    definitions: list[Definition] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    summary: str = ""
    error: str = ""


def language_for(rel: str) -> str | None:
    """The grammar for a workspace-relative path, or ``None`` when it is not source we index."""
    dot = rel.rfind(".")
    if dot < 0:
        return None
    return LANGUAGE_BY_SUFFIX.get(rel[dot:].lower())


def summarise(text: str) -> str:
    """The first docstring or comment line, markers stripped - the placeholder summary."""
    for raw in text.splitlines()[:400]:
        line = raw.strip()
        if not line:
            continue
        for quote in ('"""', "'''"):
            if line.startswith(quote):
                rest = line[3:].strip().rstrip("\"'").strip()
                return _clean_summary(rest)
        if line.startswith(_COMMENT_PREFIXES):
            return _clean_summary(line.lstrip("/*#-!<").strip())
        break
    return ""


def _clean_summary(body: str) -> str:
    body = body.strip().rstrip("*/").strip()
    return " ".join(body.split())[:SUMMARY_MAX_CHARS]


def _node_text(node: object, source: bytes) -> str:
    start = getattr(node, "start_byte", 0)
    end = getattr(node, "end_byte", 0)
    return source[start:end].decode("utf-8", errors="replace")


def _signature(node: object, source: bytes) -> str:
    first = _node_text(node, source).split("\n", 1)[0].strip()
    for stop in ("{", "=>"):
        first = first.split(stop, 1)[0].rstrip()
    if first.endswith(":"):
        first = first[:-1].rstrip()
    return " ".join(first.split())[:SIGNATURE_MAX_CHARS]


def _name_of(node: object, source: bytes) -> str | None:
    """The node's own name: a ``name`` field, else its first identifier-ish descendant."""
    try:
        named = node.child_by_field_name("name")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        named = None
    if named is not None and getattr(named, "type", "") in NAME_NODE_TYPES:
        return _node_text(named, source).strip()
    stack: list[tuple[object, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > 8:
            continue
        for child in reversed(getattr(current, "named_children", [])):
            if getattr(child, "type", "") in NAME_NODE_TYPES:
                return _node_text(child, source).strip()
            stack.append((child, depth + 1))
    return None


def _callee_name(node: object, field_name: str, source: bytes) -> str | None:
    try:
        target = node.child_by_field_name(field_name)  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        target = None
    text = _node_text(target if target is not None else node, source)
    found = _IDENTIFIER.findall(text)
    return found[-1] if found else None


def _strip_quotes(text: str) -> str:
    return text.strip().strip("\"'`").strip("<>").strip("\"'").strip()


def _imports_of(node: object, source: bytes, language: str) -> list[str]:
    kind = getattr(node, "type", "")
    text = _node_text(node, source)
    if language == "python" and kind == "import_statement":  # python: import a.b, c as d
        found: list[str] = []
        for child in getattr(node, "named_children", []):
            if getattr(child, "type", "") == "dotted_name":
                found.append(_node_text(child, source).strip())
            elif getattr(child, "type", "") == "aliased_import":
                name = child.child_by_field_name("name")
                found.append(_node_text(name, source).strip() if name is not None else "")
        return [item for item in found if item]
    if language == "python" and kind == "import_from_statement":  # python: from a.b import c
        module = node.child_by_field_name("module_name")
        if module is not None:
            return [_node_text(module, source).strip()]
        return [text.partition(" import ")[0].removeprefix("from ").strip() or "."]
    for field_name in ("source", "path", "argument"):
        target = node.child_by_field_name(field_name)
        if target is not None:
            found_text = _strip_quotes(_node_text(target, source))
            return [found_text] if found_text else []
    if kind in {"import_declaration", "using_directive", "global_using_directive"}:
        body = text.strip().rstrip(";")
        for prefix in ("global ", "import static ", "import ", "using static ", "using "):
            if body.startswith(prefix):
                body = body[len(prefix) :].strip()
        return [body] if body else []
    return []


def _in_class_body(node: object) -> bool:
    """True when a Python function sits directly in a ``class`` body, so it is a method."""
    try:
        return (
            node.parent is not None
            and node.parent.type == "block"
            and node.parent.parent is not None
            and node.parent.parent.type == "class_definition"
        )
    except AttributeError:
        return False


def extract(language: str, source: bytes) -> ExtractResult:
    """Parse ``source`` as ``language`` and collect every row for one file."""
    if language not in DEFINITIONS:  # never let an unknown name reach the grammar downloader
        return ExtractResult(error=f"unsupported language: {language}")
    try:
        parser = get_parser(language)
        tree = parser.parse(source)
    except (LookupError, ValueError, OSError) as exc:  # a missing grammar indexes no rows
        return ExtractResult(error=f"{type(exc).__name__}: {exc}")
    definitions = DEFINITIONS.get(language, {})
    call_type, call_field = CALLS.get(language, ("", ""))
    import_types = IMPORTS.get(language, frozenset())
    result = ExtractResult()
    stack: list[tuple[object, int]] = [(tree.root_node, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_DEPTH:
            continue
        kind = getattr(node, "type", "")
        line = getattr(node, "start_point", (0, 0))[0] + 1
        if kind in definitions:
            name = _name_of(node, source)
            if name:
                node_kind = definitions[kind]
                if node_kind == "function" and language == "python" and _in_class_body(node):
                    node_kind = "method"
                result.definitions.append(
                    Definition(
                        name=name,
                        kind=node_kind,
                        line=line,
                        end_line=getattr(node, "end_point", (0, 0))[0] + 1,
                        signature=_signature(node, source),
                    )
                )
        elif kind == call_type:
            callee = _callee_name(node, call_field, source)
            if callee:
                result.references.append(Reference(name=callee, line=line))
        elif kind in import_types:
            for module in _imports_of(node, source, language):
                result.imports.append(Import(module=module, line=line))
        for child in reversed(getattr(node, "named_children", [])):
            stack.append((child, depth + 1))
    result.summary = summarise(source.decode("utf-8", errors="replace"))
    return result
