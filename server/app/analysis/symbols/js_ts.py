"""JavaScript/TypeScript/TSX symbol extraction (Phase 3.3).

Walks the Tree-sitter AST (via `app.analysis.treesitter.parser_registry`)
for a single file and produces the shared `Symbol` representation
(`app.analysis.symbols.Symbol`) for:

- function declarations (`function foo() {}`, `function* foo() {}`)
- a named `const`/`let`/`var` bound to a function/arrow-function value
  (`const foo = () => {}`) — an anonymous inline callback that's never
  bound to a name (e.g. an argument to `.map(...)`) is not a symbol
- classes (`class Foo {}`, `abstract class Foo {}`)
- class methods (`method_definition`) and class fields whose value is a
  function/arrow-function (a common React pattern: `handleClick = () => {}`)
- components: a function/arrow-function symbol above whose body returns
  JSX, or a class extending `React.Component`/`React.PureComponent` — per
  the plan's own heuristic, reclassified as `SymbolKind.COMPONENT` instead
  of `FUNCTION`/`CLASS`/`METHOD`

Type-only declarations (`interface`, `type` aliases) aren't symbols — they
have no runtime behavior. Extraction is nesting-depth-agnostic: a
locally-defined helper function inside another function is extracted just
like a top-level one; no attempt is made to distinguish "module-level API"
from "implementation detail" here — that's a job for a later phase to
filter using `line_start`/`line_end` context if it ever matters, not this
one.

`has_docstring` looks for a JSDoc-style `/** ... */` block comment
immediately preceding the declaration (or, for an exported declaration,
immediately preceding the `export` statement) — a plain `//` or `/* */`
comment doesn't count, matching the common JSDoc convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter import Node

from app.analysis.language_detect import Language
from app.analysis.symbols import Symbol, SymbolKind
from app.analysis.treesitter import parser_registry

# Node types whose own JSX (if any) doesn't "belong" to an enclosing
# function/class being scanned by `_contains_jsx` — i.e. scope boundaries.
# One documented limitation from this: a curried higher-order-component
# factory (`const withFoo = () => () => <jsx/>`) won't have its inner JSX
# attributed to the outer arrow, since the inner arrow is itself a boundary.
_SCOPE_BOUNDARY_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "abstract_class_declaration",
    }
)

_JSX_NODE_TYPES = frozenset({"jsx_element", "jsx_fragment", "jsx_self_closing_element"})

_FUNCTION_VALUE_TYPES = frozenset(
    {"arrow_function", "function_expression", "generator_function"}
)

_REACT_BASE_CLASS_NAMES = frozenset(
    {"Component", "PureComponent", "React.Component", "React.PureComponent"}
)

_DECLARATION_CONTAINER_TYPES = frozenset({"lexical_declaration", "variable_declaration"})


@dataclass(frozen=True)
class _Context:
    file_path: str
    module: str
    language: Language


def extract_symbols(source: bytes, *, file_path: str, language: Language) -> list[Symbol]:
    """Parses `source` (the raw bytes of one file already known to be
    `language`) and returns every `Symbol` found in it. `language` must be
    one of `Language.JAVASCRIPT`, `Language.TYPESCRIPT`, `Language.TSX` —
    this module doesn't handle Python/Java (see the sibling `python.py`/
    `java.py` modules from Phase 3.4/3.5)."""
    parser = parser_registry.get_parser(language)
    tree = parser.parse(source)
    ctx = _Context(
        file_path=file_path, module=str(PurePosixPath(file_path).parent), language=language
    )
    symbols: list[Symbol] = []
    _walk(tree.root_node, ctx, symbols)
    return symbols


def _walk(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    if node.type in ("function_declaration", "generator_function_declaration"):
        _handle_named_function(node, ctx, symbols)
    elif node.type in ("class_declaration", "abstract_class_declaration"):
        _handle_class(node, ctx, symbols)
    elif node.type == "method_definition":
        _handle_method(node, ctx, symbols)
    elif node.type in ("public_field_definition", "field_definition"):
        _handle_field(node, ctx, symbols)
    elif node.type in _DECLARATION_CONTAINER_TYPES:
        _handle_variable_declaration(node, ctx, symbols)

    for child in node.children:
        _walk(child, ctx, symbols)


def _handle_named_function(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return  # anonymous, e.g. `export default function () {}` — no name to report
    body = node.child_by_field_name("body")
    kind = SymbolKind.COMPONENT if (body is not None and _contains_jsx(body)) else SymbolKind.FUNCTION
    _emit(node, _text(name_node), kind, ctx, symbols, doc_anchor=_export_unwrap(node))


def _handle_class(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    kind = SymbolKind.COMPONENT if _extends_react_component(node) else SymbolKind.CLASS
    _emit(node, _text(name_node), kind, ctx, symbols, doc_anchor=_export_unwrap(node))


def _handle_method(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    body = node.child_by_field_name("body")
    kind = SymbolKind.COMPONENT if (body is not None and _contains_jsx(body)) else SymbolKind.METHOD
    _emit(node, _text(name_node), kind, ctx, symbols, doc_anchor=node)


def _handle_field(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    """A class field whose value is a function/arrow-function
    (`handleClick = () => {}`) — a common idiom, especially in React class
    components, for a bound method. A field holding non-function data is
    not a symbol."""
    value = node.child_by_field_name("value")
    if value is None or value.type not in _FUNCTION_VALUE_TYPES:
        return
    name_node = next((c for c in node.children if c.type == "property_identifier"), None)
    if name_node is None:
        return
    kind = SymbolKind.COMPONENT if _contains_jsx(value) else SymbolKind.METHOD
    _emit(node, _text(name_node), kind, ctx, symbols, doc_anchor=node)


def _handle_variable_declaration(node: Node, ctx: _Context, symbols: list[Symbol]) -> None:
    """`const`/`let`/`var foo = () => {}` (or `function () {}`)."""
    doc_anchor = _export_unwrap(node)
    for declarator in node.children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        if value is None or value.type not in _FUNCTION_VALUE_TYPES:
            continue
        name_node = declarator.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue  # skip destructuring targets — no single meaningful name
        kind = SymbolKind.COMPONENT if _contains_jsx(value) else SymbolKind.FUNCTION
        _emit(node, _text(name_node), kind, ctx, symbols, doc_anchor=doc_anchor)


def _emit(
    node: Node,
    name: str,
    kind: SymbolKind,
    ctx: _Context,
    symbols: list[Symbol],
    *,
    doc_anchor: Node,
) -> None:
    symbols.append(
        Symbol(
            name=name,
            kind=kind,
            file_path=ctx.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            module=ctx.module,
            language=ctx.language,
            has_docstring=_has_leading_doc_comment(doc_anchor),
        )
    )


def _export_unwrap(node: Node) -> Node:
    """If `node`'s immediate parent is `export_statement` (`export
    function foo() {}` / `export const foo = ...`), a JSDoc comment
    precedes the *export statement*, not `node` itself — return the
    export_statement in that case so "what immediately precedes this
    declaration" looks in the right place."""
    parent = node.parent
    if parent is not None and parent.type == "export_statement":
        return parent
    return node


def _has_leading_doc_comment(anchor: Node) -> bool:
    prev = anchor.prev_sibling
    return prev is not None and prev.type == "comment" and _text(prev).startswith("/**")


def _contains_jsx(node: Node, *, _is_root: bool = True) -> bool:
    """Whether `node` (a function/arrow-function body, or a class-field's
    function value) returns JSX anywhere within it — without descending
    into a nested function/class's own body, so a nested helper's JSX
    doesn't get attributed to the outer function."""
    if not _is_root and node.type in _SCOPE_BOUNDARY_TYPES:
        return False
    if node.type in _JSX_NODE_TYPES:
        return True
    return any(_contains_jsx(child, _is_root=False) for child in node.children)


def _extends_react_component(node: Node) -> bool:
    heritage = next((c for c in node.children if c.type == "class_heritage"), None)
    if heritage is None:
        return False
    extends_clause = next((c for c in heritage.children if c.type == "extends_clause"), None)
    if extends_clause is None:
        return False
    value = next((c for c in extends_clause.children if c.type != "extends"), None)
    if value is None:
        return False
    return _text(value).strip() in _REACT_BASE_CLASS_NAMES


def _text(node: Node) -> str:
    return node.text.decode(errors="replace") if node.text is not None else ""
