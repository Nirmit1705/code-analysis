"""Import/require dependency-edge extraction, plus manifest-declared
external dependency discovery (Phase 3.7).

Two independent halves, both file-level (not symbol-level — see
`app.analysis.dependencies.DependencyEdge`'s own docstring for why):

1. `extract_import_edges(files)` — walks every JS/TS/TSX, Python, and Java
   file's own import/require/from-import syntax (via
   `app.analysis.treesitter.parser_registry`, reused as-is from Phase 3.2)
   and produces one `DependencyEdge` per import, resolved either to another
   file already present in `files` (`DependencyEdgeType.INTERNAL`) or to an
   external package/module name (`DependencyEdgeType.EXTERNAL`) when it
   doesn't resolve to one.
2. `discover_manifest_dependencies(files)` — finds and parses
   `package.json`, `requirements.txt`, `pyproject.toml` (this repo's own
   Python dependency manifest — not in the plan's original bullet list,
   added because it's what `server/pyproject.toml` actually uses instead
   of `requirements.txt`), and `pom.xml` anywhere in `files`, producing the
   flat `ExternalDependency` list of what a project *declares* it depends
   on. Deliberately separate from (1): cross-referencing "declared but
   never imported" / "imported but never declared" is a debt-detection
   rule for a later phase (`analysis/debt.py`, Phase 4), not this module's
   job — this module only reports the two raw fact lists.

Internal-import resolution:
- JS/TS/TSX: a relative specifier (`./x`, `../y`) is resolved against the
  importing file's own directory, trying common extensions/`index` files
  (`_JS_MODULE_CANDIDATE_SUFFIXES`) — using `posixpath.normpath` for real
  `..`-segment collapsing (Phase 3.6's `express.py`/`fastapi_flask.py`
  compose a path the same general way but never actually collapse `..`
  through `str(PurePosixPath(...))`, which leaves the literal `..` segment
  in the string and silently fails to match any real file for a `../`
  import from a non-root-level file — a real latent gap in that already
  signed-off code, noted here rather than fixed there, since 3.6 is out of
  this chunk's scope). A bare specifier is checked against this repo's own
  `tsconfig*.json` `compilerOptions.paths` aliases first (best-effort
  JSONC — comments/trailing commas stripped before `json.loads`; only the
  common `"prefix/*": ["target/*"]` wildcard form is understood, and only
  the first target candidate when more than one is configured), falling
  back to an external package name (`@scope/name`, or the first path
  segment) when it isn't an alias or the alias doesn't resolve.
- Python: `from .` / `from ..pkg` relative imports resolve deterministically
  by walking up `.parent` directories (no string `..`, so no equivalent of
  the JS gap above) — matching `app.analysis.apis.fastapi_flask`'s own
  proven relative-resolution approach. An absolute `import a.b.c` /
  `from a.b import c` is resolved by *suffix* match against every file in
  `files` (`a/b/c.py`, or a file path ending in `/a/b/c.py`) — this is what
  correctly resolves this repo's own `from app.api.v1 import auth` even
  though `app/` isn't the ingested repo root (`server/` is one level above
  it); an ambiguous suffix match (more than one file happens to end the
  same way) is treated as unresolved rather than guessed at. Unlike
  `fastapi_flask.py`'s route-mount resolution, a `from X import a, b, c`
  is resolved as a **single edge to module `X` itself**, not per
  imported name — this module doesn't attempt fastapi_flask.py's
  submodule-vs-attribute disambiguation (real added complexity for route
  *linking* specifically), since a file-grain dependency fact is
  unaffected by exactly which names came from `X`.
- Java: `import com.foo.Bar;` resolves the same way as Python's absolute
  case — suffix match on `com/foo/Bar.java`. A `static` import
  (`import static com.foo.Utils.helper;`) first drops the trailing member
  name to resolve the class itself. A wildcard import (`import
  com.foo.*;`) can't be attributed to one file and is always external, at
  the package grain.

Everything here is pure: no DB, no ORM, no FastAPI, no Celery.
"""

from __future__ import annotations

import json
import posixpath
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from tree_sitter import Node

from app.analysis.dependencies import DependencyEdge, DependencyEdgeType, Ecosystem, ExternalDependency
from app.analysis.language_detect import Language, detect_language
from app.analysis.treesitter import parser_registry

_JS_LANGUAGES = (Language.JAVASCRIPT, Language.TYPESCRIPT, Language.TSX)

# Tried in order when resolving a relative/aliased JS/TS specifier (with any
# explicit extension already present in the specifier tried bare, via the
# leading "" entry) to a real file already in the ingested file set.
_JS_MODULE_CANDIDATE_SUFFIXES = (
    "",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".d.ts",
    "/index.js",
    "/index.jsx",
    "/index.ts",
    "/index.tsx",
)

_PY_MODULE_CANDIDATE_SUFFIXES = (".py", "/__init__.py")

_MANIFEST_PARSER_NAMES = {"package.json", "requirements.txt", "pyproject.toml", "pom.xml"}


# --- entry points -------------------------------------------------------------


def extract_import_edges(
    files: dict[str, bytes], *, ts_path_aliases: dict[str, str] | None = None
) -> list[DependencyEdge]:
    """Extracts every import/require dependency edge across `files` (maps
    POSIX-relative file path to raw source bytes). `ts_path_aliases`, if
    supplied, is the result of `discover_ts_path_aliases(files)` — reused
    instead of recomputed when a caller already has it; computed internally
    otherwise."""
    files_keys = frozenset(files)
    aliases = ts_path_aliases if ts_path_aliases is not None else discover_ts_path_aliases(files)
    edges: list[DependencyEdge] = []
    for file_path, source in files.items():
        language = detect_language(file_path)
        if language in _JS_LANGUAGES:
            edges.extend(
                _extract_js_ts_file(
                    source, file_path=file_path, language=language, files_keys=files_keys, path_aliases=aliases
                )
            )
        elif language is Language.PYTHON:
            edges.extend(_extract_python_file(source, file_path=file_path, files_keys=files_keys))
        elif language is Language.JAVA:
            edges.extend(_extract_java_file(source, file_path=file_path, files_keys=files_keys))
    return edges


def discover_manifest_dependencies(files: dict[str, bytes]) -> list[ExternalDependency]:
    """Finds every `package.json` / `requirements.txt` / `pyproject.toml` /
    `pom.xml` anywhere in `files` and parses each into its declared
    dependencies. More than one of the same manifest kind (a monorepo with
    both a `client/package.json` and a `server/pyproject.toml`, as in this
    very repo) is fully supported — every match is parsed, each dependency
    tagged with the `manifest_path` it came from."""
    deps: list[ExternalDependency] = []
    for file_path, source in files.items():
        name = PurePosixPath(file_path).name
        if name not in _MANIFEST_PARSER_NAMES:
            continue
        if name == "package.json":
            deps.extend(parse_package_json(source, file_path=file_path))
        elif name == "requirements.txt":
            deps.extend(parse_requirements_txt(source, file_path=file_path))
        elif name == "pyproject.toml":
            deps.extend(parse_pyproject_toml(source, file_path=file_path))
        elif name == "pom.xml":
            deps.extend(parse_pom_xml(source, file_path=file_path))
    return deps


# --- JS/TS/TSX -----------------------------------------------------------------


def _extract_js_ts_file(
    source: bytes,
    *,
    file_path: str,
    language: Language,
    files_keys: frozenset[str],
    path_aliases: dict[str, str],
) -> list[DependencyEdge]:
    parser = parser_registry.get_parser(language)
    tree = parser.parse(source)
    module = str(PurePosixPath(file_path).parent)
    raw: list[tuple[str, int]] = []
    _collect_js_specifiers(tree.root_node, raw)
    return [
        _make_js_edge(file_path, module, language, specifier, line, files_keys, path_aliases)
        for specifier, line in raw
    ]


def _collect_js_specifiers(node: Node, out: list[tuple[str, int]]) -> None:
    if node.type in ("import_statement", "export_statement"):
        source_node = node.child_by_field_name("source")
        if source_node is not None:
            specifier = _js_string_value(source_node)
            if specifier is not None:
                out.append((specifier, node.start_point[0] + 1))
    elif node.type == "call_expression":
        func = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if func is not None and args is not None:
            is_require = func.type == "identifier" and _text(func) == "require"
            is_dynamic_import = func.type == "import"
            if is_require or is_dynamic_import:
                arg_strings = [c for c in args.children if c.type == "string"]
                if len(arg_strings) == 1:
                    specifier = _js_string_value(arg_strings[0])
                    if specifier is not None:
                        out.append((specifier, node.start_point[0] + 1))

    for child in node.children:
        _collect_js_specifiers(child, out)


def _make_js_edge(
    file_path: str,
    module: str,
    language: Language,
    specifier: str,
    line: int,
    files_keys: frozenset[str],
    path_aliases: dict[str, str],
) -> DependencyEdge:
    is_relative = specifier.startswith(".")
    target = _resolve_js_specifier(file_path, specifier, files_keys, path_aliases)
    if target is not None:
        return DependencyEdge(
            source_file=file_path,
            module=module,
            language=language,
            edge_type=DependencyEdgeType.INTERNAL,
            target_file=target,
            target_package=None,
            raw_specifier=specifier,
            line=line,
        )
    target_package = specifier if is_relative else _js_package_name(specifier)
    return DependencyEdge(
        source_file=file_path,
        module=module,
        language=language,
        edge_type=DependencyEdgeType.EXTERNAL,
        target_file=None,
        target_package=target_package,
        raw_specifier=specifier,
        line=line,
    )


def _resolve_js_specifier(
    file_path: str, specifier: str, files_keys: frozenset[str], path_aliases: dict[str, str]
) -> str | None:
    if specifier.startswith("."):
        base_dir = posixpath.dirname(file_path)
        normalized = posixpath.normpath(posixpath.join(base_dir, specifier)) if base_dir else posixpath.normpath(specifier)
        return _match_js_candidate(normalized, files_keys)

    for alias_prefix, target_prefix in path_aliases.items():
        if specifier == alias_prefix or specifier.startswith(alias_prefix + "/"):
            rest = specifier[len(alias_prefix) :].lstrip("/")
            normalized = posixpath.normpath(posixpath.join(target_prefix, rest)) if rest else target_prefix
            resolved = _match_js_candidate(normalized, files_keys)
            if resolved is not None:
                return resolved
    return None


def _match_js_candidate(normalized: str, files_keys: frozenset[str]) -> str | None:
    for suffix in _JS_MODULE_CANDIDATE_SUFFIXES:
        candidate = normalized + suffix
        if candidate in files_keys:
            return candidate
    return None


def _js_package_name(specifier: str) -> str:
    """The installable package name a bare (non-relative, non-aliased)
    specifier belongs to: the first two `/`-segments for a scoped package
    (`@scope/name`), otherwise just the first segment (`lodash/get` ->
    `lodash`)."""
    if specifier.startswith("@"):
        parts = specifier.split("/", 2)
        if len(parts) >= 2:
            return "/".join(parts[:2])
        return specifier
    return specifier.split("/", 1)[0]


# --- tsconfig path-alias discovery ---------------------------------------------


def discover_ts_path_aliases(files: dict[str, bytes]) -> dict[str, str]:
    """Scans every `tsconfig*.json` in `files` for `compilerOptions.paths`
    wildcard entries (`"@/*": ["./src/*"]`, this repo's own `client/tsconfig
    .json` convention) and returns `{alias_prefix: target_prefix}` (both
    POSIX paths, `target_prefix` relative to the ingested repo root). Only
    the `"prefix/*": ["target/*"]` wildcard shape is understood; a
    non-wildcard exact-match pattern, or a `paths` entry with no candidates,
    is skipped. When more than one `tsconfig*.json` declares the same
    prefix, the first one encountered wins."""
    aliases: dict[str, str] = {}
    for file_path, source in files.items():
        name = PurePosixPath(file_path).name
        if not (name == "tsconfig.json" or (name.startswith("tsconfig.") and name.endswith(".json"))):
            continue
        try:
            data = json.loads(_strip_jsonc(source.decode("utf-8", errors="replace")))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        compiler_options = data.get("compilerOptions")
        if not isinstance(compiler_options, dict):
            continue
        paths = compiler_options.get("paths")
        if not isinstance(paths, dict):
            continue

        tsconfig_dir = str(PurePosixPath(file_path).parent)
        base_url = compiler_options.get("baseUrl") or "."
        base_dir = posixpath.normpath(posixpath.join(tsconfig_dir, base_url))

        for pattern, targets in paths.items():
            if not isinstance(pattern, str) or not pattern.endswith("/*"):
                continue
            if not isinstance(targets, list) or not targets or not isinstance(targets[0], str):
                continue
            target = targets[0]
            if not target.endswith("/*"):
                continue
            alias_prefix = pattern[: -len("/*")]
            target_prefix = posixpath.normpath(posixpath.join(base_dir, target[: -len("/*")]))
            aliases.setdefault(alias_prefix, target_prefix)
    return aliases


_JSONC_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_jsonc(text: str) -> str:
    """Best-effort JSONC -> JSON: strips `//`/`/* */` comments (respecting
    string literals, so a URL or regex-looking value inside a string is
    left alone) and trailing commas before `}`/`]`. Not a full JSONC
    parser — good enough for the common `tsconfig.json` shapes this
    resolver actually needs to read."""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if token.startswith('"') else ""

    without_comments = _JSONC_TOKEN_RE.sub(_replace, text)
    return _TRAILING_COMMA_RE.sub(r"\1", without_comments)


# --- Python ----------------------------------------------------------------


def _extract_python_file(source: bytes, *, file_path: str, files_keys: frozenset[str]) -> list[DependencyEdge]:
    parser = parser_registry.get_parser(Language.PYTHON)
    tree = parser.parse(source)
    module = str(PurePosixPath(file_path).parent)
    edges: list[DependencyEdge] = []
    _walk_python(tree.root_node, file_path, module, files_keys, edges)
    return edges


def _walk_python(node: Node, file_path: str, module: str, files_keys: frozenset[str], edges: list[DependencyEdge]) -> None:
    if node.type == "import_statement":
        _handle_python_import(node, file_path, module, files_keys, edges)
    elif node.type == "import_from_statement":
        _handle_python_import_from(node, file_path, module, files_keys, edges)

    for child in node.children:
        _walk_python(child, file_path, module, files_keys, edges)


def _handle_python_import(
    node: Node, file_path: str, module: str, files_keys: frozenset[str], edges: list[DependencyEdge]
) -> None:
    """`import a.b.c` / `import x, y as z` — each comma-separated name is
    its own unambiguous absolute module path (no submodule-vs-attribute
    question the way `from ... import ...` has)."""
    line = node.start_point[0] + 1
    for name_node in node.children_by_field_name("name"):
        dotted = name_node.child_by_field_name("name") if name_node.type == "aliased_import" else name_node
        if dotted is None or dotted.type != "dotted_name":
            continue
        dotted_path = _dotted_name_path(dotted)
        if dotted_path:
            edges.append(_resolve_python_absolute(file_path, module, dotted_path, line, files_keys))


def _handle_python_import_from(
    node: Node, file_path: str, module: str, files_keys: frozenset[str], edges: list[DependencyEdge]
) -> None:
    """`from X import a, b, c` — resolved as a single edge to module `X`
    itself, regardless of how many names follow `import` (see module
    docstring for why per-name resolution is deliberately not attempted
    here)."""
    module_node = node.child_by_field_name("module_name")
    if module_node is None:
        return
    line = node.start_point[0] + 1

    if module_node.type == "relative_import":
        edges.append(_resolve_python_relative(node, module_node, file_path, module, line, files_keys))
    elif module_node.type == "dotted_name":
        dotted_path = _dotted_name_path(module_node)
        if dotted_path:
            edges.append(_resolve_python_absolute(file_path, module, dotted_path, line, files_keys))


def _resolve_python_absolute(
    file_path: str, module: str, dotted_path: str, line: int, files_keys: frozenset[str]
) -> DependencyEdge:
    candidate = dotted_path.replace(".", "/")
    target = _resolve_by_suffix(candidate, _PY_MODULE_CANDIDATE_SUFFIXES, files_keys)
    if target is not None:
        return DependencyEdge(
            source_file=file_path,
            module=module,
            language=Language.PYTHON,
            edge_type=DependencyEdgeType.INTERNAL,
            target_file=target,
            target_package=None,
            raw_specifier=dotted_path,
            line=line,
        )
    return DependencyEdge(
        source_file=file_path,
        module=module,
        language=Language.PYTHON,
        edge_type=DependencyEdgeType.EXTERNAL,
        target_file=None,
        target_package=dotted_path.split(".", 1)[0],
        raw_specifier=dotted_path,
        line=line,
    )


def _resolve_python_relative(
    node: Node, module_node: Node, file_path: str, module: str, line: int, files_keys: frozenset[str]
) -> DependencyEdge:
    prefix_node = next((c for c in module_node.children if c.type == "import_prefix"), None)
    level = len(_text(prefix_node)) if prefix_node is not None else 1
    base_dir = PurePosixPath(file_path).parent
    for _ in range(level - 1):
        base_dir = base_dir.parent

    dotted = next((c for c in module_node.children if c.type == "dotted_name"), None)
    suffix_path = _dotted_name_path(dotted).replace(".", "/") if dotted is not None else ""
    candidate = str(base_dir / suffix_path) if suffix_path else str(base_dir)
    raw_specifier = _text(module_node)

    for suffix in _PY_MODULE_CANDIDATE_SUFFIXES:
        full = candidate + suffix
        if full in files_keys:
            return DependencyEdge(
                source_file=file_path,
                module=module,
                language=Language.PYTHON,
                edge_type=DependencyEdgeType.INTERNAL,
                target_file=full,
                target_package=None,
                raw_specifier=raw_specifier,
                line=line,
            )
    return DependencyEdge(
        source_file=file_path,
        module=module,
        language=Language.PYTHON,
        edge_type=DependencyEdgeType.EXTERNAL,
        target_file=None,
        target_package=raw_specifier,
        raw_specifier=raw_specifier,
        line=line,
    )


def _dotted_name_path(dotted_name: Node) -> str:
    return ".".join(_text(c) for c in dotted_name.children if c.type == "identifier")


# --- Java --------------------------------------------------------------------


def _extract_java_file(source: bytes, *, file_path: str, files_keys: frozenset[str]) -> list[DependencyEdge]:
    parser = parser_registry.get_parser(Language.JAVA)
    tree = parser.parse(source)
    module = str(PurePosixPath(file_path).parent)
    edges: list[DependencyEdge] = []
    for node in tree.root_node.children:
        if node.type == "import_declaration":
            edge = _resolve_java_import(node, file_path, module, files_keys)
            if edge is not None:
                edges.append(edge)
    return edges


def _resolve_java_import(node: Node, file_path: str, module: str, files_keys: frozenset[str]) -> DependencyEdge | None:
    line = node.start_point[0] + 1
    is_static = any(c.type == "static" for c in node.children)
    is_wildcard = any(c.type == "asterisk" for c in node.children)
    scoped = next((c for c in node.children if c.type == "scoped_identifier"), None)
    if scoped is None:
        return None
    dotted_path = _text(scoped)
    raw_specifier = dotted_path + (".*" if is_wildcard else "")

    if is_wildcard:
        # Imports a whole package — no single file to attribute this to.
        return DependencyEdge(
            source_file=file_path,
            module=module,
            language=Language.JAVA,
            edge_type=DependencyEdgeType.EXTERNAL,
            target_file=None,
            target_package=dotted_path,
            raw_specifier=raw_specifier,
            line=line,
        )

    segments = dotted_path.split(".")
    # A static import's last segment is a member name, not a class — drop it
    # to resolve the class itself.
    class_segments = segments[:-1] if is_static and len(segments) > 1 else segments
    class_path = "/".join(class_segments) + ".java"
    target = _resolve_by_suffix(class_path, ("",), files_keys)
    if target is not None:
        return DependencyEdge(
            source_file=file_path,
            module=module,
            language=Language.JAVA,
            edge_type=DependencyEdgeType.INTERNAL,
            target_file=target,
            target_package=None,
            raw_specifier=raw_specifier,
            line=line,
        )
    package_name = ".".join(class_segments[:-1]) if len(class_segments) > 1 else dotted_path
    return DependencyEdge(
        source_file=file_path,
        module=module,
        language=Language.JAVA,
        edge_type=DependencyEdgeType.EXTERNAL,
        target_file=None,
        target_package=package_name,
        raw_specifier=raw_specifier,
        line=line,
    )


# --- shared: suffix-based absolute-import resolution ----------------------


def _resolve_by_suffix(base_path: str, suffixes: tuple[str, ...], files_keys: frozenset[str]) -> str | None:
    """Resolves `base_path` (an extension-less POSIX path, e.g.
    `"app/api/v1/auth"`) to a real file in `files_keys` by trying each of
    `suffixes` appended, matching either an exact repo-root-relative path
    or — since a language's actual package/import root is often a
    subdirectory of the ingested repo root (e.g. this repo's own
    `server/` for its `app.*` absolute imports) — a path ending in
    `"/" + candidate`. An ambiguous suffix match (more than one file in
    the repo happens to end the same way) is treated as unresolved rather
    than guessed at."""
    for suffix in suffixes:
        full = base_path + suffix
        matches = [k for k in files_keys if k == full or k.endswith("/" + full)]
        if len(matches) == 1:
            return matches[0]
    return None


# --- manifest parsing -----------------------------------------------------


_PIP_SPEC_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")


def parse_package_json(source: bytes, *, file_path: str) -> list[ExternalDependency]:
    """`dependencies` + `devDependencies` (peer/optional deps aren't
    included — they describe compatibility constraints, not what the
    project itself installs)."""
    try:
        data = json.loads(source.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    deps: list[ExternalDependency] = []
    for key in ("dependencies", "devDependencies"):
        section = data.get(key)
        if not isinstance(section, dict):
            continue
        for name, version in section.items():
            if not isinstance(name, str):
                continue
            deps.append(
                ExternalDependency(
                    name=name,
                    version=str(version) if version is not None else None,
                    manifest_path=file_path,
                    ecosystem=Ecosystem.NPM,
                )
            )
    return deps


def parse_requirements_txt(source: bytes, *, file_path: str) -> list[ExternalDependency]:
    """One `ExternalDependency` per non-blank, non-option (`-r`/`-e`/...)
    line. A VCS/URL requirement (`git+https://...`) isn't a supported
    shape — the regex still matches a name-looking prefix of it, which is
    a known, accepted inaccuracy for that uncommon line form."""
    deps: list[ExternalDependency] = []
    text = source.decode("utf-8", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        parsed = _parse_pip_spec(line)
        if parsed is None:
            continue
        name, version = parsed
        deps.append(ExternalDependency(name=name, version=version, manifest_path=file_path, ecosystem=Ecosystem.PIP))
    return deps


def parse_pyproject_toml(source: bytes, *, file_path: str) -> list[ExternalDependency]:
    """Only PEP 621's `[project] dependencies = ["pkg>=1.0", ...]` array
    form (what this repo's own `server/pyproject.toml`, and any `uv`-
    managed project, uses) — Poetry's older `[tool.poetry.dependencies]`
    table form is a different shape and isn't read here."""
    try:
        data = tomllib.loads(source.decode("utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return []
    deps: list[ExternalDependency] = []
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    for spec in project.get("dependencies") or []:
        if not isinstance(spec, str):
            continue
        parsed = _parse_pip_spec(spec)
        if parsed is None:
            continue
        name, version = parsed
        deps.append(ExternalDependency(name=name, version=version, manifest_path=file_path, ecosystem=Ecosystem.PIP))
    return deps


def parse_pom_xml(source: bytes, *, file_path: str) -> list[ExternalDependency]:
    """Every `<dependency>` anywhere in the document (including inside
    `<dependencyManagement>`, which declares versions without necessarily
    using them — not distinguished here) with both a `groupId` and
    `artifactId`; `version` is `None` when omitted (parent/BOM-managed).
    Maven property placeholders (`${spring.version}`) are reported as
    their literal, unresolved text."""
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return []
    deps: list[ExternalDependency] = []
    for node in root.iter():
        if _local_tag(node) != "dependency":
            continue
        group_id = _child_text(node, "groupId")
        artifact_id = _child_text(node, "artifactId")
        if not group_id or not artifact_id:
            continue
        deps.append(
            ExternalDependency(
                name=f"{group_id}:{artifact_id}",
                version=_child_text(node, "version"),
                manifest_path=file_path,
                ecosystem=Ecosystem.MAVEN,
            )
        )
    return deps


def _parse_pip_spec(spec: str) -> tuple[str, str | None] | None:
    spec = spec.split(";", 1)[0].strip()  # drop PEP 508 environment markers
    match = _PIP_SPEC_RE.match(spec)
    if not match:
        return None
    name = match.group(1)
    version = match.group(2).strip() or None
    return name, version


def _local_tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""


def _child_text(node: ET.Element, local_name: str) -> str | None:
    for child in node:
        if _local_tag(child) == local_name:
            return (child.text or "").strip() or None
    return None


# --- small text helpers -----------------------------------------------------


def _text(node: Node) -> str:
    return node.text.decode(errors="replace") if node.text is not None else ""


def _js_string_value(node: Node) -> str | None:
    if node.type != "string":
        return None
    fragment = next((c for c in node.children if c.type == "string_fragment"), None)
    return _text(fragment) if fragment is not None else ""
