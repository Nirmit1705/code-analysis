"""Java symbol extraction (Phase 3.5).

Walks the Tree-sitter AST (via `app.analysis.treesitter.parser_registry`)
for a single file and produces the shared `Symbol` representation
(`app.analysis.symbols.Symbol`) for:

- type declarations: `class`, `interface`, `enum`, and `record` — all four
  collapse to `SymbolKind.CLASS`. The shared `SymbolKind` enum (introduced
  in `js_ts.py`, reused as-is by `python.py`) has no separate
  "interface"/"enum"/"record" kind, and adding one is a cross-cutting
  change outside this chunk's scope. If that granularity is ever needed
  it's recoverable later by re-checking the source at the symbol's
  `file_path`/`line_start` — `Symbol` doesn't currently carry the
  underlying node type, which is a real (if minor) loss of information,
  noted below.
- methods (`method_declaration`) *with a body* — an abstract method or a
  plain interface method signature (no `default` implementation) has
  nothing to attribute a location/docstring to, so — matching
  `js_ts.py`'s treatment of interface/abstract method signatures and
  `python.py`'s "a def always has a body, nothing analogous to skip"
  situation — it is not extracted.
- constructors (`constructor_declaration`) — always extracted, since Java
  requires a constructor to have a body.

Java has no free-standing functions (every method belongs to some type
declaration), so this module never produces `SymbolKind.FUNCTION`, and
there's no JSX-like "component" concept either — every symbol here is
`CLASS` or `METHOD`.

Extraction is nesting-depth-agnostic, matching `js_ts.py`/`python.py`: a
nested class, or an enum's own methods, is extracted just like a
top-level one.

`has_docstring` looks for a Javadoc-style `/** ... */` comment — Tree-sitter
calls this node type `block_comment` here (distinct from Python's `comment`
node type, though the `/**` convention itself matches `js_ts.py`'s JSDoc
check) — immediately preceding the declaration. A plain `//` line comment
or a `/* */` block without the second `*` doesn't count.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter import Node

from app.analysis.language_detect import Language
from app.analysis.symbols import Symbol, SymbolKind
from app.analysis.treesitter import parser_registry

_TYPE_DECLARATION_TYPES = frozenset(
    {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
)


@dataclass(frozen=True)
class _Context:
    file_path: str
    module: str
    language: Language


def extract_symbols(
    source: bytes, *, file_path: str, language: Language = Language.JAVA
) -> list[Symbol]:
    """Parses `source` (the raw bytes of one Java file) and returns every
    `Symbol` found in it. `language` defaults to `Language.JAVA` (the only
    language this module handles) but is accepted explicitly to match
    `js_ts.extract_symbols`/`python.extract_symbols`'s signature, so
    callers can treat every per-language extractor uniformly."""
    parser = parser_registry.get_parser(language)
    tree = parser.parse(source)
    ctx = _Context(
        file_path=file_path, module=str(PurePosixPath(file_path).parent), language=language
    )
    symbols: list[Symbol] = []
    _walk(tree.root_node, ctx, symbols)
    return symbols


def _walk(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    if node.type in _TYPE_DECLARATION_TYPES:
        _handle_type_declaration(node, ctx, symbols)
    elif node.type == "method_declaration":
        _handle_method(node, ctx, symbols)
    elif node.type == "constructor_declaration":
        _handle_constructor(node, ctx, symbols)

    for child in node.children:
        _walk(child, ctx, symbols)


def _handle_type_declaration(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    _emit(node, _text(name_node), SymbolKind.CLASS, ctx, symbols)


def _handle_method(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    if node.child_by_field_name("body") is None:
        return  # an abstract method or a plain interface signature — no implementation
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    _emit(node, _text(name_node), SymbolKind.METHOD, ctx, symbols)


def _handle_constructor(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    _emit(node, _text(name_node), SymbolKind.METHOD, ctx, symbols)


def _emit(node: Node, name: str, kind: SymbolKind, ctx: _Context, symbols: list[Symbol]) -> None:
    symbols.append(
        Symbol(
            name=name,
            kind=kind,
            file_path=ctx.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            module=ctx.module,
            language=ctx.language,
            has_docstring=_has_leading_doc_comment(node),
        )
    )


def _has_leading_doc_comment(node: Node) -> bool:
    prev = node.prev_sibling
    return prev is not None and prev.type == "block_comment" and _text(prev).startswith("/**")


def _text(node: Node) -> str:
    return node.text.decode(errors="replace") if node.text is not None else ""
