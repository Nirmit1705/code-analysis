"""Express (Node/JS/TS) route extraction (Phase 3.6).

Walks the Tree-sitter AST (via `app.analysis.treesitter.parser_registry`,
reused as-is from Phase 3.2) for a set of JS/TS/TSX files and produces the
shared `app.analysis.apis.ApiRoute` representation for:

- `app.get/post/put/delete/patch(path, ...)` and `router.METHOD(path, ...)`
  calls, where `app`/`router` is a variable created via `express()`,
  `express.Router()`, or a destructured `Router()` — verified against the
  file's own `require('express')`/`import ... from 'express'`, not just
  matched by variable name, to avoid false positives from unrelated code
  using the same common names.
- mounted sub-routers: `app.use(router)` / `app.use('/prefix', router)`,
  resolved across files (not just within one file) by following the
  mounted identifier back through the local file's imports/`require`s to
  the file that actually defines and exports that router, using
  `_mount_graph.resolve_prefixes` for the resulting graph of mount edges —
  the same composition helper `fastapi_flask.py` uses for
  `include_router`/`register_blueprint`.

Entry points:
- `extract_file(source, file_path)` — single-file, pre-resolution
  extraction (`_FileExtraction`); mostly an internal building block, but
  exposed for callers that only have one file in hand.
- `compose_routes(files, symbols_by_file=None)` — the real entry point:
  extracts every file, resolves the cross-file mount graph, and returns
  every fully composed `ApiRoute`. `files` maps POSIX-relative file path
  to raw source bytes; `symbols_by_file` (if supplied) maps the same paths
  to that file's `Symbol` list from `app.analysis.symbols.js_ts` — used to
  link each route's handler by name. If omitted, it's computed internally
  via `js_ts.extract_symbols` (kept as a parameter mainly so a caller that
  already extracted symbols for the same pipeline run doesn't pay for it
  twice).

Known limitations, called out here rather than scattered as TODOs:
- Only a plain string-literal route path is understood; a dynamic path
  (a template literal, a variable) is skipped — matches the rest of
  Phase 3's "no partial evaluation of runtime values" stance.
- Module resolution for cross-file mounting only understands *relative*
  specifiers already present in the given `files` map (guessing common
  extensions/`index` files) — no `node_modules`/bare-specifier resolution,
  no bundler path aliases.
- Handler-symbol linking only looks in the *same file* as the route call —
  a handler imported from another file (`import { getUsers } from
  './controllers/users'; router.get('/users', getUsers)`) is still
  reported (`handler_name="getUsers"`) but with `handler_symbol=None`.
- Chained route builders (`app.route('/x').get(h1).post(h2)`) are not
  recognized — not in this chunk's required bullet list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from tree_sitter import Node

from app.analysis.apis import ApiRoute, Framework
from app.analysis.apis._mount_graph import resolve_prefixes
from app.analysis.apis._paths import join_url_path
from app.analysis.language_detect import Language, detect_language
from app.analysis.symbols import Symbol, SymbolKind
from app.analysis.symbols.js_ts import extract_symbols as extract_js_ts_symbols
from app.analysis.treesitter import parser_registry

_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch"})
_EXPRESS_JS_LANGUAGES = (Language.JAVASCRIPT, Language.TYPESCRIPT, Language.TSX)
_FUNCTION_VALUE_TYPES = frozenset({"arrow_function", "function_expression", "generator_function"})

# Candidate suffixes tried, in order, when resolving a relative import/require
# specifier (with any explicit extension already stripped) to a file in the
# given file set.
_MODULE_CANDIDATE_SUFFIXES = (
    "",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    "/index.js",
    "/index.jsx",
    "/index.ts",
    "/index.tsx",
)


@dataclass(frozen=True)
class _RouteCall:
    router_var: str
    method: str  # upper-case
    path_literal: str | None
    handler_ref: str | None  # simple identifier text, if the handler arg is a bare identifier
    line_start: int
    line_end: int


@dataclass(frozen=True)
class _MountCall:
    parent_var: str
    prefix: str  # "" if mounted with no explicit prefix string
    child_refs: tuple[str, ...]  # one or more identifiers mounted at `prefix`


@dataclass(frozen=True)
class _FileExtraction:
    file_path: str
    module: str
    routes: list[_RouteCall] = field(default_factory=list)
    mounts: list[_MountCall] = field(default_factory=list)
    router_vars: set[str] = field(default_factory=set)
    # local identifier -> relative specifier text as written (e.g. "./routes/users"),
    # already known to start with "." or ".." — resolved to an actual file at compose time.
    imports: dict[str, str] = field(default_factory=dict)
    # export alias ("default", or a named export's own name) -> local identifier
    exports: dict[str, str] = field(default_factory=dict)


def extract_file(source: bytes, *, file_path: str, language: Language | None = None) -> _FileExtraction:
    """Parses `source` (one JS/TS/TSX file) and returns everything needed
    to later resolve its routes: raw route calls, mount calls, and enough
    import/export bookkeeping for `compose_routes` to follow a mounted
    router across files. `language` defaults to detecting it from
    `file_path`; pass it explicitly to avoid a redundant detection when
    the caller already knows it."""
    lang = language or detect_language(file_path)
    if lang not in _EXPRESS_JS_LANGUAGES:
        return _FileExtraction(file_path=file_path, module=str(PurePosixPath(file_path).parent))
    parser = parser_registry.get_parser(lang)
    tree = parser.parse(source)

    extraction = _FileExtraction(file_path=file_path, module=str(PurePosixPath(file_path).parent))
    express_names: set[str] = set()  # locally bound to the `express` module itself
    router_factory_names: set[str] = set()  # locally bound to `express`'s `Router` export

    _walk(tree.root_node, extraction, express_names, router_factory_names)
    return extraction


def _walk(node: Node, extraction: _FileExtraction, express_names: set[str], router_factory_names: set[str]) -> None:
    if node.type in ("lexical_declaration", "variable_declaration"):
        _handle_declaration(node, extraction, express_names, router_factory_names)
    elif node.type == "import_statement":
        _handle_import_statement(node, extraction, express_names, router_factory_names)
    elif node.type == "assignment_expression":
        _handle_assignment(node, extraction)
    elif node.type == "export_statement":
        _handle_export_statement(node, extraction)
    elif node.type == "call_expression":
        _handle_call(node, extraction)

    for child in node.children:
        _walk(child, extraction, express_names, router_factory_names)


# --- router/app variable tracking -------------------------------------------


def _handle_declaration(
    node: Node, extraction: _FileExtraction, express_names: set[str], router_factory_names: set[str]
) -> None:
    for declarator in node.children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        name_node = declarator.child_by_field_name("name")
        if value is None or name_node is None:
            continue

        if value.type == "call_expression":
            specifier = _require_specifier(value)
            if specifier == "express":
                if name_node.type == "identifier":
                    express_names.add(_text(name_node))
                else:
                    _bind_destructured_router_factory(name_node, router_factory_names)
                continue
            if specifier is not None and specifier.startswith(".") and name_node.type == "identifier":
                # `const usersRouter = require('./routes/users')` — the CommonJS
                # equivalent of `import usersRouter from './routes/users'`,
                # recorded the same way `_handle_import_statement` records an
                # ESM default import so `_resolve_mount_targets` can follow it
                # identically regardless of module system.
                extraction.imports[_text(name_node)] = specifier
                continue

        if name_node.type != "identifier" or value.type != "call_expression":
            continue
        var_name = _text(name_node)
        if _is_router_creation(value, express_names, router_factory_names):
            extraction.router_vars.add(var_name)


def _require_specifier(call: Node) -> str | None:
    """The module specifier string of a `require('...')` call — the
    callee is the bare identifier `require` and the sole argument is a
    string literal. `None` if `call` isn't a plain `require(...)` call at
    all (a different callee, no arguments, or a non-literal argument)."""
    func = call.child_by_field_name("function")
    args = call.child_by_field_name("arguments")
    if func is None or func.type != "identifier" or _text(func) != "require" or args is None:
        return None
    arg_strings = [c for c in args.children if c.type == "string"]
    return _js_string_value(arg_strings[0]) if len(arg_strings) == 1 else None


def _bind_destructured_router_factory(pattern_node: Node, router_factory_names: set[str]) -> None:
    """`const { Router } = require('express')` / `const { Router: R } = ...`."""
    if pattern_node.type != "object_pattern":
        return
    for child in pattern_node.children:
        if child.type == "shorthand_property_identifier_pattern" and _text(child) == "Router":
            router_factory_names.add("Router")
        elif child.type == "pair_pattern":
            key = child.child_by_field_name("key")
            value = child.child_by_field_name("value")
            if key is not None and _text(key) == "Router" and value is not None:
                router_factory_names.add(_text(value))


def _is_router_creation(call: Node, express_names: set[str], router_factory_names: set[str]) -> bool:
    """`express()` (an app), `express.Router()`, or a destructured
    `Router()` — all treated uniformly as "this variable can have routes
    registered on it and can itself be mounted"."""
    func = call.child_by_field_name("function")
    if func is None:
        return False
    if func.type == "identifier":
        text = _text(func)
        return text in express_names or text in router_factory_names
    if func.type == "member_expression":
        obj = func.child_by_field_name("object")
        prop = func.child_by_field_name("property")
        return (
            obj is not None
            and prop is not None
            and obj.type == "identifier"
            and _text(obj) in express_names
            and _text(prop) == "Router"
        )
    return False


# --- imports / exports (for cross-file mount resolution) -------------------


def _handle_import_statement(
    node: Node, extraction: _FileExtraction, express_names: set[str], router_factory_names: set[str]
) -> None:
    source_node = node.child_by_field_name("source")
    if source_node is None:
        return
    specifier = _js_string_value(source_node)
    if specifier is None:
        return

    clause = next((c for c in node.children if c.type == "import_clause"), None)
    if clause is None:
        return

    if specifier == "express":
        # `import express from 'express'` / `import express, { Router } from 'express'`
        default_id = next((c for c in clause.children if c.type == "identifier"), None)
        if default_id is not None:
            express_names.add(_text(default_id))
        namespace = next((c for c in clause.children if c.type == "namespace_import"), None)
        if namespace is not None:
            ns_id = next((c for c in namespace.children if c.type == "identifier"), None)
            if ns_id is not None:
                express_names.add(_text(ns_id))
        named = next((c for c in clause.children if c.type == "named_imports"), None)
        if named is not None:
            for spec in named.children:
                if spec.type != "import_specifier":
                    continue
                name_node = spec.child_by_field_name("name")
                if name_node is not None and _text(name_node) == "Router":
                    alias_node = spec.child_by_field_name("alias")
                    local = _text(alias_node) if alias_node is not None else _text(name_node)
                    router_factory_names.add(local)
        return

    if not specifier.startswith("."):
        return  # not resolvable against the given file set (a bare package specifier)

    default_id = next((c for c in clause.children if c.type == "identifier"), None)
    if default_id is not None:
        extraction.imports[_text(default_id)] = specifier
    namespace = next((c for c in clause.children if c.type == "namespace_import"), None)
    if namespace is not None:
        ns_id = next((c for c in namespace.children if c.type == "identifier"), None)
        if ns_id is not None:
            extraction.imports[_text(ns_id)] = specifier
    named = next((c for c in clause.children if c.type == "named_imports"), None)
    if named is not None:
        for spec in named.children:
            if spec.type != "import_specifier":
                continue
            name_node = spec.child_by_field_name("name")
            if name_node is None:
                continue
            alias_node = spec.child_by_field_name("alias")
            local = _text(alias_node) if alias_node is not None else _text(name_node)
            extraction.imports[local] = specifier


def _handle_assignment(node: Node, extraction: _FileExtraction) -> None:
    """`module.exports = router`, `module.exports.foo = foo`,
    `exports.foo = foo` — CommonJS export forms. `const usersRouter =
    require('./routes/users')` is handled in `_handle_declaration` for the
    `require`-as-import side; this handles the *exporting* file's side."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or right is None or left.type != "member_expression" or right.type != "identifier":
        return
    obj = left.child_by_field_name("object")
    prop = left.child_by_field_name("property")
    if obj is None or prop is None:
        return

    if obj.type == "identifier" and _text(obj) == "module" and _text(prop) == "exports":
        extraction.exports["default"] = _text(right)
        return
    if obj.type == "identifier" and _text(obj) == "exports":
        extraction.exports[_text(prop)] = _text(right)
        return
    if (
        obj.type == "member_expression"
        and (inner_obj := obj.child_by_field_name("object")) is not None
        and inner_obj.type == "identifier"
        and _text(inner_obj) == "module"
        and (inner_prop := obj.child_by_field_name("property")) is not None
        and _text(inner_prop) == "exports"
    ):
        extraction.exports[_text(prop)] = _text(right)


def _handle_export_statement(node: Node, extraction: _FileExtraction) -> None:
    value = node.child_by_field_name("value")
    if value is not None and value.type == "identifier":
        extraction.exports["default"] = _text(value)  # `export default router;`
        return

    declaration = node.child_by_field_name("declaration")
    if declaration is not None and declaration.type in ("lexical_declaration", "variable_declaration"):
        for declarator in declaration.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            if name_node is not None and name_node.type == "identifier":
                extraction.exports[_text(name_node)] = _text(name_node)  # `export const router = ...`
        return

    clause = next((c for c in node.children if c.type == "export_clause"), None)
    if clause is not None:
        for spec in clause.children:
            if spec.type != "export_specifier":
                continue
            name_node = spec.child_by_field_name("name")
            if name_node is None:
                continue
            alias_node = spec.child_by_field_name("alias")
            exported_as = _text(alias_node) if alias_node is not None else _text(name_node)
            extraction.exports[exported_as] = _text(name_node)  # `export { router }` / `export { x as default }`


# --- route / mount calls -----------------------------------------------------


def _handle_call(node: Node, extraction: _FileExtraction) -> None:
    func = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if func is None or func.type != "member_expression" or args is None:
        return
    obj = func.child_by_field_name("object")
    prop = func.child_by_field_name("property")
    if obj is None or prop is None or obj.type != "identifier":
        return
    router_var = _text(obj)
    method_name = _text(prop)
    arg_nodes = [c for c in args.children if c.type not in ("(", ")", ",")]

    if method_name.lower() in _HTTP_METHODS and arg_nodes:
        _record_route(node, router_var, method_name.upper(), arg_nodes, extraction)
    elif method_name == "use" and arg_nodes:
        _record_mount(router_var, arg_nodes, extraction)


def _record_route(node: Node, router_var: str, method: str, arg_nodes: list[Node], extraction: _FileExtraction) -> None:
    path_literal = _js_string_value(arg_nodes[0]) if arg_nodes[0].type == "string" else None
    handler_arg = arg_nodes[-1]
    handler_ref = _text(handler_arg) if handler_arg.type == "identifier" else None
    extraction.routes.append(
        _RouteCall(
            router_var=router_var,
            method=method,
            path_literal=path_literal,
            handler_ref=handler_ref,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
        )
    )


def _record_mount(router_var: str, arg_nodes: list[Node], extraction: _FileExtraction) -> None:
    prefix = ""
    rest = arg_nodes
    if arg_nodes[0].type == "string":
        prefix = _js_string_value(arg_nodes[0]) or ""
        rest = arg_nodes[1:]
    child_refs = tuple(_text(a) for a in rest if a.type == "identifier")
    if child_refs:
        extraction.mounts.append(_MountCall(parent_var=router_var, prefix=prefix, child_refs=child_refs))


# --- cross-file composition --------------------------------------------------


def compose_routes(
    files: dict[str, bytes], *, symbols_by_file: dict[str, list[Symbol]] | None = None
) -> list[ApiRoute]:
    """Extracts every JS/TS/TSX file in `files`, resolves the mount graph
    across all of them, and returns every fully composed `ApiRoute`.
    `files` maps POSIX-relative file path to raw source bytes. See the
    module docstring for what's in and out of scope for the cross-file
    resolution."""
    extractions: dict[str, _FileExtraction] = {}
    for file_path, source in files.items():
        language = detect_language(file_path)
        if language not in _EXPRESS_JS_LANGUAGES:
            continue
        extractions[file_path] = extract_file(source, file_path=file_path, language=language)

    if symbols_by_file is None:
        symbols_by_file = {
            file_path: extract_js_ts_symbols(files[file_path], file_path=file_path, language=detect_language(file_path))
            for file_path in extractions
        }

    resolved_target = _resolve_mount_targets(extractions)
    edges: list[tuple[tuple[str, str], str, tuple[str, str]]] = []
    for file_path, extraction in extractions.items():
        for mount in extraction.mounts:
            parent_key = (file_path, mount.parent_var)
            for child_ref in mount.child_refs:
                target = resolved_target(file_path, child_ref)
                if target is not None:
                    edges.append((parent_key, mount.prefix, target))

    reachable = resolve_prefixes(edges)

    routes: list[ApiRoute] = []
    for file_path, extraction in extractions.items():
        for route in extraction.routes:
            if route.path_literal is None:
                continue
            key = (file_path, route.router_var)
            prefixes = reachable.get(key, [""])
            handler_symbol, handler_name = _link_handler(route, symbols_by_file.get(file_path, []))
            for prefix in prefixes:
                routes.append(
                    ApiRoute(
                        method=route.method,
                        path=join_url_path(prefix, route.path_literal),
                        framework=Framework.EXPRESS,
                        file_path=file_path,
                        module=extraction.module,
                        handler_symbol=handler_symbol,
                        handler_name=handler_name,
                        line_start=route.line_start,
                        line_end=route.line_end,
                    )
                )
    return routes


def _resolve_mount_targets(extractions: dict[str, _FileExtraction]):
    """Returns a `(file_path, identifier) -> (file_path, router_var) | None`
    resolver closed over `extractions`, following same-file router
    variables directly and cross-file references through the (relative-
    only) import graph."""

    def resolve(file_path: str, identifier: str) -> tuple[str, str] | None:
        extraction = extractions.get(file_path)
        if extraction is None:
            return None
        if identifier in extraction.router_vars:
            return (file_path, identifier)

        specifier = extraction.imports.get(identifier)
        if specifier is None:
            return None
        target_file = _resolve_relative_module(file_path, specifier, extractions)
        if target_file is None:
            return None
        target_extraction = extractions[target_file]
        # The imported local name might be a default import (matched against
        # the target's "default" export) or a named import (matched by name).
        exported_local = target_extraction.exports.get("default") or target_extraction.exports.get(identifier)
        if exported_local is None:
            return None
        if exported_local in target_extraction.router_vars:
            return (target_file, exported_local)
        return None

    return resolve


def _resolve_relative_module(
    from_file: str, specifier: str, extractions: dict[str, _FileExtraction]
) -> str | None:
    base_dir = PurePosixPath(from_file).parent
    joined = str(base_dir / specifier) if specifier.startswith(".") else specifier
    normalized = str(PurePosixPath(joined))
    for suffix in _MODULE_CANDIDATE_SUFFIXES:
        candidate = normalized + suffix
        if candidate in extractions:
            return candidate
    return None


def _link_handler(route: _RouteCall, file_symbols: list[Symbol]) -> tuple[Symbol | None, str]:
    if route.handler_ref is None:
        return None, "<inline>"
    for symbol in file_symbols:
        if symbol.name == route.handler_ref and symbol.kind in (SymbolKind.FUNCTION, SymbolKind.COMPONENT):
            return symbol, route.handler_ref
    return None, route.handler_ref


# --- small text helpers -------------------------------------------------------


def _text(node: Node) -> str:
    return node.text.decode(errors="replace") if node.text is not None else ""


def _js_string_value(node: Node) -> str | None:
    """The decoded contents of a JS `string` node (its `string_fragment`
    child, absent when there's nothing between the quotes — an empty
    string literal correctly yields `""`, not `None`). Returns `None` only
    when `node` isn't a string literal at all, so callers can use `is
    None` to mean "not a plain string"."""
    if node.type != "string":
        return None
    fragment = next((c for c in node.children if c.type == "string_fragment"), None)
    return _text(fragment) if fragment is not None else ""
