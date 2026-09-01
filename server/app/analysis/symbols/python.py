"""Python symbol extraction (Phase 3.4).

Walks the Tree-sitter AST (via `app.analysis.treesitter.parser_registry`)
for a single file and produces the shared `Symbol` representation
(`app.analysis.symbols.Symbol`) for:

- function definitions, `async def` included (`async` is just a leading
  keyword on the same `function_definition` node in this grammar, not a
  separate node type — nothing special needed to handle it)
- class definitions
- methods — a `function_definition` is a `METHOD` instead of a `FUNCTION`
  exactly when its immediately enclosing block belongs to a
  `class_definition`; every other function (top-level, or nested inside
  another function) is a `FUNCTION` — matching `js_ts.py`'s own
  nesting-agnostic scope (a locally-defined helper function is extracted
  just like a top-level one)
- decorators (`@staticmethod`, `@property`, custom decorators, stacked
  decorators) — a decorated definition's reported span
  (`line_start`/`line_end`) includes its decorator lines, by using the
  enclosing `decorated_definition` node's own range rather than the inner
  `function_definition`/`class_definition`'s

Python has no equivalent of `js_ts.py`'s "component" heuristic — every
symbol produced here is `FUNCTION`, `CLASS`, or `METHOD`. Lambdas
(`foo = lambda x: x + 1`) are deliberately not extracted as symbols: unlike
JS/TS, where an arrow function bound to a name is the idiomatic way to
declare a function, a bare `def` is Python's idiomatic form and lambdas
rarely carry the kind of meaningful body/docstring this extractor reports.

`has_docstring` follows Python's actual docstring convention: the first
statement in the function/class body is a bare string-literal expression
(any quoting style — a plain string, an f-string, single- or triple-quoted
— all count), not a preceding comment as in `js_ts.py`'s JSDoc convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter import Node

from app.analysis.language_detect import Language
from app.analysis.symbols import Symbol, SymbolKind
from app.analysis.treesitter import parser_registry


@dataclass(frozen=True)
class _Context:
    file_path: str
    module: str
    language: Language


def extract_symbols(
    source: bytes, *, file_path: str, language: Language = Language.PYTHON
) -> list[Symbol]:
    """Parses `source` (the raw bytes of one Python file) and returns
    every `Symbol` found in it. `language` defaults to `Language.PYTHON`
    (the only language this module handles) but is accepted explicitly to
    match `js_ts.extract_symbols`'s signature, so callers can treat every
    per-language extractor uniformly."""
    parser = parser_registry.get_parser(language)
    tree = parser.parse(source)
    ctx = _Context(
        file_path=file_path, module=str(PurePosixPath(file_path).parent), language=language
    )
    symbols: list[Symbol] = []
    _walk(tree.root_node, ctx, symbols)
    return symbols


def _walk(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    if node.type == "function_definition":
        _handle_function(node, ctx, symbols)
    elif node.type == "class_definition":
        _handle_class(node, ctx, symbols)

    for child in node.children:
        _walk(child, ctx, symbols)


def _handle_function(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    kind = SymbolKind.METHOD if _is_method(node) else SymbolKind.FUNCTION
    _emit(node, _text(name_node), kind, ctx, symbols, body=node.child_by_field_name("body"))


def _handle_class(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    _emit(
        node, _text(name_node), SymbolKind.CLASS, ctx, symbols, body=node.child_by_field_name("body")
    )


def _is_method(node: Node) -> bool:
    """Whether `node` (a `function_definition`) is a method — i.e. its
    immediately enclosing block belongs to a `class_definition` — rather
    than a module-level or nested-in-a-function `FUNCTION`."""
    block = _span_node(node).parent
    return (
        block is not None
        and block.type == "block"
        and block.parent is not None
        and block.parent.type == "class_definition"
    )


def _span_node(node: Node) -> Node:
    """`node` itself, or its enclosing `decorated_definition` if it has
    one, so a decorated symbol's reported line range includes its
    decorator line(s) and `_is_method` looks at the right enclosing
    block."""
    parent = node.parent
    return parent if parent is not None and parent.type == "decorated_definition" else node


def _emit(
    node: Node,
    name: str,
    kind: SymbolKind,
    ctx: _Context,
    symbols: list[Symbol],
    *,
    body: Node | None,
) -> None:
    span_node = _span_node(node)
    symbols.append(
        Symbol(
            name=name,
            kind=kind,
            file_path=ctx.file_path,
            line_start=span_node.start_point[0] + 1,
            line_end=span_node.end_point[0] + 1,
            module=ctx.module,
            language=ctx.language,
            has_docstring=_has_docstring(body),
        )
    )


def _has_docstring(body: Node | None) -> bool:
    if body is None or not body.children:
        return False
    first_statement = body.children[0]
    if first_statement.type != "expression_statement" or not first_statement.children:
        return False
    return first_statement.children[0].type == "string"


def _text(node: Node) -> str:
    return node.text.decode(errors="replace") if node.text is not None else ""
