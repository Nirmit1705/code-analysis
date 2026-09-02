"""FastAPI / Flask (Python) route extraction (Phase 3.6).

Walks the Tree-sitter AST (via `app.analysis.treesitter.parser_registry`,
reused as-is from Phase 3.2) for a set of Python files and produces the
shared `app.analysis.apis.ApiRoute` representation for:

- `@app.get/post/put/delete/patch(path)` and `@app.route(path,
  methods=[...])` (defaulting to `["GET"]` when `methods` is omitted,
  matching Flask's own default) on any variable created via `FastAPI()`,
  `APIRouter(prefix=...)`, `Flask(...)`, or `Blueprint(..., url_prefix=...)`
  — matched by constructor name only (not by verified import provenance,
  unlike `express.py`'s stricter check: `FastAPI`/`APIRouter`/`Flask`/
  `Blueprint` are distinctive enough names that this is a safe
  simplification in practice).
- `APIRouter`'s own construction-time `prefix=` and `include_router(...,
  prefix=...)`'s mount-time prefix, and the equivalent Flask
  `Blueprint(..., url_prefix=...)` / `register_blueprint(..., url_prefix=
  ...)` pair — both composed with `_mount_graph.resolve_prefixes` (shared
  with `express.py`), which is what lets `own_prefix` (the router's own
  fixed prefix) and mount-time prefixes stack correctly across files, e.g.
  this repo's own `app/api/v1/github.py`'s `router = APIRouter(prefix=
  "/auth/github")` mounted via `app/api/v1/router.py`'s
  `api_router.include_router(github.router)` under `api_router`'s own
  `prefix="/api/v1"` composes to `/api/v1/auth/github/...` — see
  `scripts/smoke_apis_fastapi_flask.py`'s real-repo spot-check.
- linked to the decorated function's own `Symbol` (from
  `app.analysis.symbols.python`, matched by name + the enclosing
  `decorated_definition`'s own start line — the same span `python.py`
  uses for a decorated symbol) — unlike Express, a FastAPI/Flask route's
  handler is always the function the decorator sits directly on, so this
  link is always same-file and unambiguous.

Entry points mirror `express.py`: `extract_file(source, file_path)` for a
single file, `compose_routes(files, symbols_by_file=None)` as the real
multi-file entry point.

Known limitations:
- Only a plain string-literal route path is understood (no f-strings/
  variables) — matches the rest of Phase 3.
- `include_router`/`register_blueprint` cross-file resolution only
  understands two Python import shapes: `from pkg import submodule` (or
  its relative-import / `as`-aliased forms) used as `submodule.router`,
  and `from pkg.submodule import name` used as the bare identifier `name`
  — covers this repo's own real `app/api/v1/router.py` pattern and the
  equivalent common Flask `Blueprint` pattern, but not a re-exporting
  `__init__.py` barrel or a `sys.path`/namespace-package layout.
- A bare `import pkg.submodule` with no `as` alias isn't tracked (the
  local name it binds is the *top* package, not the submodule — correctly
  handling that adds real complexity for a pattern this style of FastAPI
  routing code essentially never uses); only the `as`-aliased and
  `from ... import ...` forms are.
- Flask's real `register_blueprint(bp, url_prefix=X)` *replaces* `bp`'s
  own construction-time `url_prefix` when `X` is given, rather than
  stacking with it — this module always stacks (same composition as
  FastAPI's `include_router(prefix=...)`), which is only wrong when
  *both* a `Blueprint(..., url_prefix=...)` and a
  `register_blueprint(..., url_prefix=...)` are given for the same
  blueprint at once. The far more common case (only one of the two set)
  composes identically either way, so this is a deliberate, narrow
  simplification rather than an oversight.
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
from app.analysis.symbols.python import extract_symbols as extract_python_symbols
from app.analysis.treesitter import parser_registry

_HTTP_VERBS = frozenset({"get", "post", "put", "delete", "patch"})
_ROUTER_CONSTRUCTORS = {
    "FastAPI": Framework.FASTAPI,
    "APIRouter": Framework.FASTAPI,
    "Flask": Framework.FLASK,
    "Blueprint": Framework.FLASK,
}
_OWN_PREFIX_CONSTRUCTORS = {"APIRouter": "prefix", "Blueprint": "url_prefix"}
_INCLUDE_METHODS = frozenset({"include_router", "register_blueprint"})
_INCLUDE_PREFIX_KEYS = ("prefix", "url_prefix")


@dataclass(frozen=True)
class _RouteDecorator:
    var_name: str
    method: str  # upper-case
    path: str
    func_name: str
    def_node: Node  # the decorated_definition node (span + handler-symbol matching)
    line_start: int
    line_end: int


@dataclass(frozen=True)
class _IncludeCall:
    parent_var: str
    prefix: str
    child_kind: str  # "identifier" | "attribute"
    child_name: str  # identifier text, or the attribute's own final name (e.g. "router" in "auth.router")
    child_module_alias: str | None  # the object identifier text for the "attribute" form (e.g. "auth")


@dataclass(frozen=True)
class _ImportRef:
    """Resolution candidates for one imported local name — tried in
    different orders depending on how the name is later used (see
    `_resolve_include_target`'s two branches)."""

    submodule_prefix: str  # path if the imported name is itself a submodule/file
    base_prefix: str  # path of the containing package/module, for the attr fallback below
    attr_if_not_submodule: str  # the top-level name to look up in base_prefix's file, if any


@dataclass(frozen=True)
class _FileExtraction:
    file_path: str
    module: str
    routes: list[_RouteDecorator] = field(default_factory=list)
    includes: list[_IncludeCall] = field(default_factory=list)
    router_vars: dict[str, tuple[Framework, str]] = field(default_factory=dict)  # var -> (framework, own_prefix)
    imports: dict[str, _ImportRef] = field(default_factory=dict)


def extract_file(source: bytes, *, file_path: str) -> _FileExtraction:
    """Parses `source` (one Python file) and returns everything needed to
    later resolve its routes: raw route decorators, include/register
    calls, and enough import bookkeeping for `compose_routes` to follow a
    mounted router/blueprint across files."""
    if detect_language(file_path) != Language.PYTHON:
        return _FileExtraction(file_path=file_path, module=str(PurePosixPath(file_path).parent))
    parser = parser_registry.get_parser(Language.PYTHON)
    tree = parser.parse(source)
    extraction = _FileExtraction(file_path=file_path, module=str(PurePosixPath(file_path).parent))
    _walk(tree.root_node, extraction, file_path)
    return extraction


def _walk(node: Node, extraction: _FileExtraction, file_path: str) -> None:
    if node.type == "assignment":
        _handle_assignment(node, extraction)
    elif node.type == "import_from_statement":
        _handle_import_from(node, extraction, file_path)
    elif node.type == "import_statement":
        _handle_import_statement(node, extraction)
    elif node.type == "decorated_definition":
        _handle_decorated_definition(node, extraction)
    elif node.type == "call":
        _handle_call(node, extraction)

    for child in node.children:
        _walk(child, extraction, file_path)


# --- router/app/blueprint variable tracking ---------------------------------


def _handle_assignment(node: Node, extraction: _FileExtraction) -> None:
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or right is None or left.type != "identifier" or right.type != "call":
        return
    func = right.child_by_field_name("function")
    if func is None:
        return
    constructor = _callee_name(func)
    framework = _ROUTER_CONSTRUCTORS.get(constructor)
    if framework is None:
        return
    prefix_key = _OWN_PREFIX_CONSTRUCTORS.get(constructor)
    own_prefix = _keyword_string_arg(right, prefix_key) if prefix_key else None
    extraction.router_vars[_text(left)] = (framework, own_prefix or "")


def _callee_name(func: Node) -> str:
    if func.type == "identifier":
        return _text(func)
    if func.type == "attribute":
        attr = func.child_by_field_name("attribute")
        return _text(attr) if attr is not None else ""
    return ""


# --- route decorators --------------------------------------------------------


def _handle_decorated_definition(node: Node, extraction: _FileExtraction) -> None:
    definition = node.child_by_field_name("definition")
    if definition is None or definition.type != "function_definition":
        return
    name_node = definition.child_by_field_name("name")
    if name_node is None:
        return
    func_name = _text(name_node)

    for decorator in node.children:
        if decorator.type != "decorator":
            continue
        call = next((c for c in decorator.children if c.type == "call"), None)
        if call is None:
            continue
        func = call.child_by_field_name("function")
        if func is None or func.type != "attribute":
            continue
        obj = func.child_by_field_name("object")
        attr = func.child_by_field_name("attribute")
        if obj is None or attr is None or obj.type != "identifier":
            continue
        var_name = _text(obj)
        if var_name not in extraction.router_vars:
            continue

        verb = _text(attr).lower()
        args = call.child_by_field_name("arguments")
        path = _first_positional_string(args) or ""
        if verb in _HTTP_VERBS:
            methods = [verb.upper()]
        elif verb == "route":
            methods = _methods_kwarg(args) or ["GET"]
        else:
            continue

        for method in methods:
            extraction.routes.append(
                _RouteDecorator(
                    var_name=var_name,
                    method=method,
                    path=path,
                    func_name=func_name,
                    def_node=node,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                )
            )


# --- include_router / register_blueprint ------------------------------------


def _handle_call(node: Node, extraction: _FileExtraction) -> None:
    func = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if func is None or func.type != "attribute" or args is None:
        return
    obj = func.child_by_field_name("object")
    attr = func.child_by_field_name("attribute")
    if obj is None or attr is None or obj.type != "identifier":
        return
    parent_var = _text(obj)
    if parent_var not in extraction.router_vars or _text(attr) not in _INCLUDE_METHODS:
        return

    positional = _first_positional_arg(args)
    if positional is None:
        return
    if positional.type == "identifier":
        child_kind, child_name, child_module_alias = "identifier", _text(positional), None
    elif positional.type == "attribute":
        obj2 = positional.child_by_field_name("object")
        attr2 = positional.child_by_field_name("attribute")
        if obj2 is None or attr2 is None or obj2.type != "identifier":
            return
        child_kind, child_name, child_module_alias = "attribute", _text(attr2), _text(obj2)
    else:
        return

    prefix = next((p for key in _INCLUDE_PREFIX_KEYS if (p := _keyword_string_arg(node, key)) is not None), "")
    extraction.includes.append(
        _IncludeCall(
            parent_var=parent_var, prefix=prefix, child_kind=child_kind, child_name=child_name,
            child_module_alias=child_module_alias,
        )
    )


# --- imports (for cross-file include/register resolution) ------------------


def _handle_import_from(node: Node, extraction: _FileExtraction, file_path: str) -> None:
    module_node = node.child_by_field_name("module_name")
    if module_node is None:
        return
    base_prefix = _module_prefix_from(module_node, file_path)
    if base_prefix is None:
        return

    for name_node in node.children_by_field_name("name"):
        if name_node.type == "aliased_import":
            inner = name_node.child_by_field_name("name")
            alias_node = name_node.child_by_field_name("alias")
            imported_name = _dotted_name_last(inner) if inner is not None else ""
            local = _text(alias_node) if alias_node is not None else imported_name
        elif name_node.type == "dotted_name":
            imported_name = _dotted_name_last(name_node)
            local = imported_name
        else:
            continue
        if not imported_name:
            continue
        submodule_prefix = f"{base_prefix}/{imported_name}" if base_prefix else imported_name
        extraction.imports[local] = _ImportRef(
            submodule_prefix=submodule_prefix, base_prefix=base_prefix, attr_if_not_submodule=imported_name
        )


def _handle_import_statement(node: Node, extraction: _FileExtraction) -> None:
    """Only the `import a.b.c as w` form is tracked — see the module
    docstring's limitations note on the un-aliased form."""
    name_node = node.child_by_field_name("name")
    if name_node is None or name_node.type != "aliased_import":
        return
    inner = name_node.child_by_field_name("name")
    alias_node = name_node.child_by_field_name("alias")
    if inner is None or alias_node is None:
        return
    module_path = _dotted_name_path(inner)
    if not module_path:
        return
    extraction.imports[_text(alias_node)] = _ImportRef(
        submodule_prefix=module_path, base_prefix="", attr_if_not_submodule=""
    )


def _module_prefix_from(module_node: Node, file_path: str) -> str | None:
    """The POSIX path prefix a `from <module_node> import ...` statement's
    module resolves to — an absolute dotted path (`app.api.v1` ->
    `"app/api/v1"`) or a relative one resolved against `file_path`'s own
    directory (`.` -> that directory itself, `..` -> its parent, ...)."""
    if module_node.type == "dotted_name":
        return _dotted_name_path(module_node)
    if module_node.type != "relative_import":
        return None
    prefix_node = next((c for c in module_node.children if c.type == "import_prefix"), None)
    if prefix_node is None:
        return None
    level = len(_text(prefix_node))  # "." -> 1, ".." -> 2, ...
    base_dir = PurePosixPath(file_path).parent
    for _ in range(level - 1):
        base_dir = base_dir.parent
    dotted = next((c for c in module_node.children if c.type == "dotted_name"), None)
    if dotted is None:
        return str(base_dir)
    suffix = _dotted_name_path(dotted)
    return str(base_dir / suffix) if suffix else str(base_dir)


def _dotted_name_path(dotted_name: Node) -> str:
    return "/".join(_text(c) for c in dotted_name.children if c.type == "identifier")


def _dotted_name_last(dotted_name: Node) -> str:
    identifiers = [c for c in dotted_name.children if c.type == "identifier"]
    return _text(identifiers[-1]) if identifiers else ""


# --- cross-file composition --------------------------------------------------


def compose_routes(
    files: dict[str, bytes], *, symbols_by_file: dict[str, list[Symbol]] | None = None
) -> list[ApiRoute]:
    """Extracts every Python file in `files`, resolves the FastAPI
    `include_router`/Flask `register_blueprint` graph across all of them,
    and returns every fully composed `ApiRoute`. `files` maps
    POSIX-relative file path to raw source bytes."""
    extractions: dict[str, _FileExtraction] = {}
    for file_path, source in files.items():
        if detect_language(file_path) != Language.PYTHON:
            continue
        extractions[file_path] = extract_file(source, file_path=file_path)

    if symbols_by_file is None:
        symbols_by_file = {fp: extract_python_symbols(files[fp], file_path=fp) for fp in extractions}

    own_prefix: dict[tuple[str, str], str] = {}
    edges: list[tuple[tuple[str, str], str, tuple[str, str]]] = []
    resolve_target = _resolve_include_target(extractions)
    for file_path, extraction in extractions.items():
        for var_name, (_framework, prefix) in extraction.router_vars.items():
            own_prefix[(file_path, var_name)] = prefix
        for include in extraction.includes:
            target = resolve_target(file_path, include)
            if target is not None:
                edges.append(((file_path, include.parent_var), include.prefix, target))

    reachable = resolve_prefixes(edges, own_prefix=own_prefix)

    routes: list[ApiRoute] = []
    for file_path, extraction in extractions.items():
        for route in extraction.routes:
            key = (file_path, route.var_name)
            framework = extraction.router_vars[route.var_name][0]
            bases = reachable.get(key) or [own_prefix.get(key, "")]
            handler_symbol = _find_function_symbol(symbols_by_file.get(file_path, []), route.func_name, route.def_node)
            for base in bases:
                routes.append(
                    ApiRoute(
                        method=route.method,
                        path=join_url_path(base, route.path),
                        framework=framework,
                        file_path=file_path,
                        module=extraction.module,
                        handler_symbol=handler_symbol,
                        handler_name=route.func_name,
                        line_start=route.line_start,
                        line_end=route.line_end,
                    )
                )
    return routes


def _resolve_include_target(extractions: dict[str, _FileExtraction]):
    """Returns a `(file_path, _IncludeCall) -> (file_path, var_name) | None`
    resolver closed over `extractions`. The two `_IncludeCall.child_kind`s
    need opposite resolution orders: a bare identifier (`register_blueprint
    (bp, ...)`) was bound by `from pkg.module import bp` — `bp` names a
    top-level var, not a submodule, so only the attr-fallback applies; a
    qualified attribute (`include_router(auth.router)`) needs `auth` itself
    to resolve to a whole submodule file first, then `router` is looked up
    as a top-level var *in that file*."""

    def resolve(file_path: str, include: _IncludeCall) -> tuple[str, str] | None:
        extraction = extractions.get(file_path)
        if extraction is None:
            return None

        if include.child_kind == "identifier":
            if include.child_name in extraction.router_vars:
                return (file_path, include.child_name)
            ref = extraction.imports.get(include.child_name)
            if ref is None or not ref.attr_if_not_submodule:
                return None
            target_file = _resolve_module_prefix(ref.base_prefix, extractions)
            if target_file is None:
                return None
            if ref.attr_if_not_submodule in extractions[target_file].router_vars:
                return (target_file, ref.attr_if_not_submodule)
            return None

        # "attribute": `<child_module_alias>.<child_name>`
        ref = extraction.imports.get(include.child_module_alias or "")
        if ref is None:
            return None
        target_file = _resolve_module_prefix(ref.submodule_prefix, extractions)
        if target_file is None:
            return None
        if include.child_name in extractions[target_file].router_vars:
            return (target_file, include.child_name)
        return None

    return resolve


def _resolve_module_prefix(prefix: str, extractions: dict[str, _FileExtraction]) -> str | None:
    """Resolves `prefix` (a dotted-import-derived, extension-less POSIX
    path, e.g. `"app/api/v1"`) to a real file in `extractions` — first by
    an exact match against the ingested repo root, then — since a Python
    package's actual import root is often a subdirectory of the ingested
    repo root (e.g. this repo's own `server/` for its `app.*` absolute
    imports) — by a suffix match against a path ending in
    `"/" + candidate`. An ambiguous suffix match (more than one file in
    the repo happens to end the same way) is treated as unresolved rather
    than guessed at.

    Same technique as `app.analysis.dependencies.imports._resolve_by_suffix`
    (Phase 3.7), duplicated here rather than imported so this module's own
    dependency surface is unchanged — this was a real gap in the original
    exact-match-only version, only surfaced once Phase 3.7's pipeline
    ingested this repo's real files at their true root-relative paths."""
    for candidate in (f"{prefix}.py", f"{prefix}/__init__.py"):
        if candidate in extractions:
            return candidate
        suffix = f"/{candidate}"
        matches = [key for key in extractions if key.endswith(suffix)]
        if len(matches) == 1:
            return matches[0]
    return None


def _find_function_symbol(symbols: list[Symbol], name: str, def_node: Node) -> Symbol | None:
    target_line = def_node.start_point[0] + 1
    return next(
        (
            s
            for s in symbols
            if s.name == name and s.line_start == target_line and s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
        ),
        None,
    )


# --- small argument/text helpers ---------------------------------------------


def _first_positional_arg(args: Node | None) -> Node | None:
    if args is None:
        return None
    for child in args.children:
        if child.type in ("(", ")", ",") or child.type == "keyword_argument":
            continue
        return child
    return None


def _first_positional_string(args: Node | None) -> str | None:
    node = _first_positional_arg(args)
    return _python_string_value(node) if node is not None and node.type == "string" else None


def _keyword_string_arg(call_node: Node, key: str) -> str | None:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type != "keyword_argument":
            continue
        name_node = child.child_by_field_name("name")
        value_node = child.child_by_field_name("value")
        if name_node is not None and _text(name_node) == key and value_node is not None and value_node.type == "string":
            return _python_string_value(value_node)
    return None


def _methods_kwarg(args: Node | None) -> list[str] | None:
    if args is None:
        return None
    for child in args.children:
        if child.type != "keyword_argument":
            continue
        name_node = child.child_by_field_name("name")
        value_node = child.child_by_field_name("value")
        if name_node is None or _text(name_node) != "methods" or value_node is None or value_node.type != "list":
            continue
        methods = [_python_string_value(c) for c in value_node.children if c.type == "string"]
        return [m.upper() for m in methods if m]
    return None


def _text(node: Node) -> str:
    return node.text.decode(errors="replace") if node.text is not None else ""


def _python_string_value(node: Node) -> str | None:
    if node.type != "string":
        return None
    content = next((c for c in node.children if c.type == "string_content"), None)
    return _text(content) if content is not None else ""
