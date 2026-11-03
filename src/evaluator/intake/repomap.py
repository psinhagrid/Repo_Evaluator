"""Cleaned repo map: file tree, signatures and imports, with prose removed.

Signatures are rebuilt from tree-sitter nodes with every comment dropped
and every string literal replaced by "…", so docstrings, comments and
default-argument strings cannot carry persuasive text into the outline.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from .snapshot import Snapshot

PARSEABLE = {"python", "javascript", "typescript", "tsx"}
MAX_SIG_LEN = 300
SAFE_MODULE_RE = re.compile(r"[^\w@/.\-]")

STRING_TYPES = {"string", "template_string", "concatenated_string"}
COMMENT_TYPES = {"comment", "line_comment", "block_comment"}

PY_DEFS = {"function_definition": "function", "class_definition": "class"}
JS_DEFS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "method_definition": "method",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}


@dataclass
class Symbol:
    kind: str
    name: str
    signature: str
    line: int
    end_line: int
    parent: str | None = None


@dataclass
class FileMap:
    path: str
    language: str
    imports: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    has_main_guard: bool = False
    parse_errors: bool = False


def _render(node: Node, src: bytes, stop: int | None = None) -> str:
    """Source text of `node` up to byte `stop`, without comments, strings collapsed."""
    end = node.end_byte if stop is None else stop
    pieces: list[bytes] = []
    pos = node.start_byte

    def walk(n: Node) -> None:
        nonlocal pos
        if n.start_byte >= end:
            return
        is_string = n.is_named and n.type in STRING_TYPES  # the TS `string` keyword is an unnamed token
        if n.type in COMMENT_TYPES or is_string:
            pieces.append(src[pos:n.start_byte])
            if is_string:
                pieces.append('"…"'.encode())
            pos = min(n.end_byte, end)
            return
        for c in n.children:
            walk(c)

    walk(node)
    pieces.append(src[pos:end])
    text = b"".join(pieces).decode("utf-8", errors="replace")
    text = " ".join(text.split())
    return text[:MAX_SIG_LEN]


def _name(node: Node, src: bytes) -> str:
    n = node.child_by_field_name("name")
    return src[n.start_byte:n.end_byte].decode("utf-8", errors="replace") if n else "<anonymous>"


def _module_text(node: Node, src: bytes) -> str:
    raw = src[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip("'\"`")
    return SAFE_MODULE_RE.sub("", raw)[:100]


def _python(root: Node, src: bytes, fm: FileMap) -> None:
    def visit(n: Node, parent: str | None) -> None:
        for c in n.children:
            target = c
            if c.type == "decorated_definition":
                target = c.child_by_field_name("definition") or c
            if target.type in PY_DEFS:
                body = target.child_by_field_name("body")
                name = _name(target, src)
                kind = PY_DEFS[target.type]
                if kind == "function" and parent:
                    kind = "method"
                sig = _render(target, src, stop=body.start_byte if body else None).rstrip(":").strip()
                fm.symbols.append(Symbol(kind, name, sig, target.start_point[0] + 1, target.end_point[0] + 1, parent))
                if body:
                    visit(body, name if PY_DEFS[target.type] == "class" else parent)
            elif c.type == "import_statement":
                for d in c.children:
                    if d.type in ("dotted_name", "aliased_import"):
                        mod = d.child_by_field_name("name") if d.type == "aliased_import" else d
                        fm.imports.append(_module_text(mod, src))
            elif c.type == "import_from_statement":
                mod = c.child_by_field_name("module_name")
                if mod:
                    fm.imports.append(_module_text(mod, src))
            elif c.type == "if_statement":
                cond = c.child_by_field_name("condition")
                if cond and b"__name__" in src[cond.start_byte:cond.end_byte]:
                    fm.has_main_guard = True
            elif c.type in ("block", "expression_statement", "try_statement", "with_statement"):
                visit(c, parent)

    visit(root, None)


def _javascript(root: Node, src: bytes, fm: FileMap) -> None:
    def visit(n: Node, parent: str | None) -> None:
        for c in n.children:
            t = c.type
            if t == "export_statement":
                visit(c, parent)
                continue
            if t in JS_DEFS:
                body = c.child_by_field_name("body")
                name = _name(c, src)
                stop = body.start_byte if body and JS_DEFS[t] in ("function", "class", "method") else None
                sig = _render(c, src, stop=stop)
                fm.symbols.append(Symbol(JS_DEFS[t], name, sig, c.start_point[0] + 1, c.end_point[0] + 1, parent))
                if body and JS_DEFS[t] == "class":
                    visit(body, name)
            elif t in ("lexical_declaration", "variable_declaration"):
                for d in c.children:
                    if d.type != "variable_declarator":
                        continue
                    value = d.child_by_field_name("value")
                    if value is not None and value.type in ("arrow_function", "function_expression", "function"):
                        body = value.child_by_field_name("body")
                        sig = _render(c, src, stop=body.start_byte if body else None).rstrip("=> ").strip()
                        fm.symbols.append(Symbol("function", _name(d, src), sig, c.start_point[0] + 1,
                                                 c.end_point[0] + 1, parent))
                    elif value is not None and value.type == "call_expression":
                        fn = value.child_by_field_name("function")
                        args = value.child_by_field_name("arguments")
                        if fn and src[fn.start_byte:fn.end_byte] == b"require" and args and args.named_child_count:
                            fm.imports.append(_module_text(args.named_children[0], src))
            elif t == "import_statement":
                source = c.child_by_field_name("source")
                if source:
                    fm.imports.append(_module_text(source, src))

    visit(root, None)


def map_file(path: str, language: str, text: str) -> FileMap:
    fm = FileMap(path=path, language=language)
    src = text.encode("utf-8")
    tree = get_parser(language).parse(src)
    fm.parse_errors = tree.root_node.has_error
    if language == "python":
        _python(tree.root_node, src, fm)
    else:
        _javascript(tree.root_node, src, fm)
    fm.imports = sorted(set(i for i in fm.imports if i))
    return fm


def build_repo_map(snap: Snapshot, languages: dict[str, str | None]) -> dict:
    """`languages` maps snapshot path -> inventory language."""
    files = {}
    for path in sorted(snap.files):
        lang = languages.get(path)
        if snap.files[path].converted_from == "ipynb":
            lang = "python"
        if lang not in PARSEABLE:
            continue
        fm = map_file(path, lang, "\n".join(snap.lines(path)) + "\n")
        files[path] = asdict(fm)
    return {"tree": sorted(languages), "files": files}
