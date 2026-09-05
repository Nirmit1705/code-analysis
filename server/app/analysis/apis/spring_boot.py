"""Spring Boot (Java) route extraction (Phase 3.6).

Walks the Tree-sitter AST (via `app.analysis.treesitter.parser_registry`,
reused as-is from Phase 3.2) for a single Java file and produces the
shared `app.analysis.apis.ApiRoute` representation for every
`@RestController`/`@Controller`-annotated class:

- the class-level `@RequestMapping("...")` prefix (default `""` if absent)
- each method-level `@GetMapping`/`@PostMapping`/`@PutMapping`/
  `@DeleteMapping`/`@PatchMapping`, and `@RequestMapping(method = ...)`
  (defaulting to `GET` when no `method` is given, matching Spring's own
  default) composed with the class prefix into the full path
- linked to the annotated method's own `Symbol` (from
  `app.analysis.symbols.java`, matched by name + the method_declaration's
  own start line — the same node `java.py` emits a `Symbol` for)

No cross-file resolution is needed here (unlike `express.py`/
`fastapi_flask.py`): a Spring controller's class-level and method-level
annotations always live in the same file as the class itself, so this
module's entry point is single-file, with `compose_routes` provided only
as a thin convenience for iterating many files.

Known limitations:
- Only a single string value per `@RequestMapping`/`@GetMapping`/etc. is
  read (`value = "..."` or the bare positional form, and `path = "..."`
  as an alias) — an array of multiple paths on one annotation
  (`@RequestMapping({"/a", "/b"})`) takes the first only.
- `method = RequestMethod.X` is read as a single value the same way — a
  multi-method array isn't split into separate routes.
- A class-level `@RequestMapping` that itself omits `method` doesn't
  constrain the HTTP method (Spring allows all methods in that case);
  that's naturally handled here since method resolution is purely
  per-endpoint-annotation, not inherited from the class annotation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter import Node

from app.analysis.apis import ApiRoute, Framework
from app.analysis.apis._paths import join_url_path
from app.analysis.language_detect import Language
from app.analysis.symbols import Symbol, SymbolKind
from app.analysis.symbols.java import extract_symbols as extract_java_symbols
from app.analysis.treesitter import parser_registry

logger = logging.getLogger(__name__)

_CONTROLLER_ANNOTATIONS = frozenset({"RestController", "Controller"})
_METHOD_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}
_PATH_KEYS = ("value", "path")


@dataclass(frozen=True)
class _RawRoute:
    class_name: str
    class_prefix: str
    method: str
    path: str
    method_node: Node  # the method_declaration node itself (for handler linking + span)
    func_name: str


def extract_routes(source: bytes, *, file_path: str, symbols: list[Symbol] | None = None) -> list[ApiRoute]:
    """Parses `source` (one Java file) and returns every `ApiRoute` found
    in it. `symbols`, if supplied, is that file's already-extracted
    `Symbol` list (from `app.analysis.symbols.java.extract_symbols`) —
    reused for handler linking instead of re-parsing; computed internally
    if omitted."""
    parser = parser_registry.get_parser(Language.JAVA)
    tree = parser.parse(source)
    file_symbols = symbols if symbols is not None else extract_java_symbols(source, file_path=file_path)
    module = str(PurePosixPath(file_path).parent)

    raw_routes: list[_RawRoute] = []
    _walk(tree.root_node, raw_routes)

    routes: list[ApiRoute] = []
    for raw in raw_routes:
        handler_symbol = _find_method_symbol(file_symbols, raw.func_name, raw.method_node)
        routes.append(
            ApiRoute(
                method=raw.method,
                path=join_url_path(raw.class_prefix, raw.path),
                framework=Framework.SPRING_BOOT,
                file_path=file_path,
                module=module,
                handler_symbol=handler_symbol,
                handler_name=raw.func_name,
                line_start=raw.method_node.start_point[0] + 1,
                line_end=raw.method_node.end_point[0] + 1,
            )
        )
    return routes


def compose_routes(files: dict[str, bytes], *, symbols_by_file: dict[str, list[Symbol]] | None = None) -> list[ApiRoute]:
    """Convenience wrapper over `extract_routes` for many `.java` files at
    once; `files` maps POSIX-relative file path to raw source bytes."""
    routes: list[ApiRoute] = []
    for file_path, source in files.items():
        if not file_path.endswith(".java"):
            continue
        symbols = symbols_by_file.get(file_path) if symbols_by_file is not None else None
        try:
            file_routes = extract_routes(source, file_path=file_path, symbols=symbols)
        except Exception as exc:  # noqa: BLE001 — a per-file extraction bug must not abort the whole run
            logger.warning("skipping Spring Boot route extraction for %r: %s", file_path, exc)
            continue
        routes.extend(file_routes)
    return routes


def _walk(node: Node, raw_routes: list[_RawRoute]) -> None:
    if node.type == "class_declaration":
        _handle_class(node, raw_routes)
    for child in node.children:
        _walk(child, raw_routes)


def _handle_class(node: Node, raw_routes: list[_RawRoute]) -> None:
    modifiers = _find_child(node, "modifiers")
    if modifiers is None or not any(_annotation_name(a) in _CONTROLLER_ANNOTATIONS for a in _annotations(modifiers)):
        return  # not a Spring controller — its methods aren't API routes
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = _text(name_node)
    class_mapping = next((a for a in _annotations(modifiers) if _annotation_name(a) == "RequestMapping"), None)
    class_prefix = _annotation_string_value(class_mapping) if class_mapping is not None else ""

    body = node.child_by_field_name("body")
    if body is None:
        return
    for member in body.children:
        if member.type != "method_declaration":
            continue
        _handle_method(member, class_name, class_prefix or "", raw_routes)


def _handle_method(node: Node, class_name: str, class_prefix: str, raw_routes: list[_RawRoute]) -> None:
    modifiers = _find_child(node, "modifiers")
    if modifiers is None:
        return
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    func_name = _text(name_node)

    for annotation in _annotations(modifiers):
        annotation_name = _annotation_name(annotation)
        if annotation_name in _METHOD_ANNOTATIONS:
            method = _METHOD_ANNOTATIONS[annotation_name]
        elif annotation_name == "RequestMapping":
            method = _annotation_method_value(annotation) or "GET"
        else:
            continue
        path = _annotation_string_value(annotation) or ""
        raw_routes.append(
            _RawRoute(
                class_name=class_name,
                class_prefix=class_prefix,
                method=method,
                path=path,
                method_node=node,
                func_name=func_name,
            )
        )


def _find_method_symbol(symbols: list[Symbol], name: str, method_node: Node) -> Symbol | None:
    target_line = method_node.start_point[0] + 1
    return next(
        (s for s in symbols if s.kind == SymbolKind.METHOD and s.name == name and s.line_start == target_line),
        None,
    )


def _annotations(modifiers: Node) -> list[Node]:
    return [c for c in modifiers.children if c.type in ("marker_annotation", "annotation")]


def _annotation_name(annotation: Node) -> str:
    name_node = annotation.child_by_field_name("name")
    return _text(name_node) if name_node is not None else ""


def _annotation_string_value(annotation: Node) -> str | None:
    """The single string value of an annotation: the bare positional form
    (`@RequestMapping("/x")`) or a `value =`/`path =` keyed form
    (`@RequestMapping(value = "/x")`)."""
    if annotation.type != "annotation":
        return None
    args = annotation.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type == "string_literal":
            return _java_string_value(child)
        if child.type == "element_value_pair":
            key = child.child_by_field_name("key")
            value = child.child_by_field_name("value")
            if key is not None and _text(key) in _PATH_KEYS and value is not None and value.type == "string_literal":
                return _java_string_value(value)
    return None


def _annotation_method_value(annotation: Node) -> str | None:
    """`method = RequestMethod.GET` — reads the field-access value's own
    field name (`GET`), ignoring the `RequestMethod.` qualifier."""
    if annotation.type != "annotation":
        return None
    args = annotation.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type != "element_value_pair":
            continue
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if key is None or _text(key) != "method" or value is None:
            continue
        if value.type == "field_access":
            field_node = value.child_by_field_name("field")
            if field_node is not None:
                return _text(field_node).upper()
    return None


def _find_child(node: Node, child_type: str) -> Node | None:
    return next((c for c in node.children if c.type == child_type), None)


def _text(node: Node) -> str:
    return node.text.decode(errors="replace") if node.text is not None else ""


def _java_string_value(node: Node) -> str | None:
    if node.type != "string_literal":
        return None
    fragment = next((c for c in node.children if c.type == "string_fragment"), None)
    return _text(fragment) if fragment is not None else ""
