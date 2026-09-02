"""Manual smoke test for Phase 3.7's `app.analysis.dependencies.imports`.

Four parts:

1. `run_js_ts()` — `scripts/fixtures/dependencies/js_ts/`: a relative
   import, a `tsconfig.json` `"@/*"` alias resolving to the *same* target
   as the relative import (cross-check), a scoped (`@scope/pkg`) and
   unscoped (`react`) external package, a CommonJS `require(...)`, a
   dynamic `import(...)`, two `export ... from` re-exports, a `"../../"`
   (two-level) relative import from a nested file (regression check for
   real `..`-collapsing — see `imports.py`'s module docstring on the gap
   this exercises that `express.py`'s own Phase 3.6 resolver has), and a
   relative import pointing outside the file set entirely (unresolved ->
   external fallback).
2. `run_python()` — `scripts/fixtures/dependencies/python/`, ingested
   under a synthetic `server/` prefix (the dict keys don't match the
   fixture's on-disk layout — see the script below) specifically to
   exercise suffix-based absolute-import resolution the same way this
   repo's own `from app.api.v1 import auth` resolves despite `app/` not
   being the ingested repo root.
3. `run_java()` — `scripts/fixtures/dependencies/java/`: a resolvable
   class import, a `static` import, a wildcard import (always external by
   design), and a stdlib import.
4. `run_manifests()` — `discover_manifest_dependencies` against all of the
   above plus `scripts/fixtures/dependencies/manifests/pom.xml`.

Run: `uv run python scripts/smoke_dependencies.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.dependencies import DependencyEdge, DependencyEdgeType, Ecosystem  # noqa: E402
from app.analysis.dependencies.imports import (  # noqa: E402
    discover_manifest_dependencies,
    discover_ts_path_aliases,
    extract_import_edges,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "dependencies"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _load_tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def _edges_from(edges: list[DependencyEdge], source_file: str) -> list[DependencyEdge]:
    return [e for e in edges if e.source_file == source_file]


def _find(edges: list[DependencyEdge], raw_specifier: str) -> DependencyEdge | None:
    return next((e for e in edges if e.raw_specifier == raw_specifier), None)


def run_js_ts() -> bool:
    print("=== JS/TS import-edge checks ===")
    all_passed = True

    files = _load_tree(FIXTURES_DIR / "js_ts")
    aliases = discover_ts_path_aliases(files)
    all_passed &= _check("tsconfig alias discovered: '@' -> 'src'", aliases.get("@") == "src", str(aliases))

    edges = extract_import_edges(files, ts_path_aliases=aliases)
    widget_edges = _edges_from(edges, "src/components/Widget.tsx")

    react_edge = _find(widget_edges, "react")
    all_passed &= _check(
        "react -> EXTERNAL package 'react'",
        react_edge is not None and react_edge.edge_type == DependencyEdgeType.EXTERNAL and react_edge.target_package == "react",
        str(react_edge),
    )

    scoped_edge = _find(widget_edges, "@scope/pkg")
    all_passed &= _check(
        "@scope/pkg -> EXTERNAL package '@scope/pkg'",
        scoped_edge is not None
        and scoped_edge.edge_type == DependencyEdgeType.EXTERNAL
        and scoped_edge.target_package == "@scope/pkg",
        str(scoped_edge),
    )

    relative_edge = _find(widget_edges, "../utils/helpers")
    alias_edge = _find(widget_edges, "@/utils/helpers")
    all_passed &= _check(
        "'../utils/helpers' and '@/utils/helpers' both resolve to src/utils/helpers.ts",
        relative_edge is not None
        and alias_edge is not None
        and relative_edge.target_file == "src/utils/helpers.ts"
        and alias_edge.target_file == "src/utils/helpers.ts",
        f"{relative_edge} / {alias_edge}",
    )

    require_edge = _find(widget_edges, "./local")
    all_passed &= _check(
        "require('./local') -> INTERNAL src/components/local.js",
        require_edge is not None
        and require_edge.edge_type == DependencyEdgeType.INTERNAL
        and require_edge.target_file == "src/components/local.js",
        str(require_edge),
    )

    dynamic_edge = _find(widget_edges, "./lazy")
    all_passed &= _check(
        "dynamic import('./lazy') -> INTERNAL src/components/lazy.ts",
        dynamic_edge is not None
        and dynamic_edge.edge_type == DependencyEdgeType.INTERNAL
        and dynamic_edge.target_file == "src/components/lazy.ts",
        str(dynamic_edge),
    )

    index_edges = _edges_from(edges, "src/index.ts")
    reexport_named = _find(index_edges, "./utils/helpers")
    reexport_star = _find(index_edges, "./components/Widget")
    all_passed &= _check(
        "export { helperFn } from './utils/helpers' -> INTERNAL",
        reexport_named is not None and reexport_named.target_file == "src/utils/helpers.ts",
        str(reexport_named),
    )
    all_passed &= _check(
        "export * from './components/Widget' -> INTERNAL",
        reexport_star is not None and reexport_star.target_file == "src/components/Widget.tsx",
        str(reexport_star),
    )

    deep_edges = _edges_from(edges, "src/components/nested/Deep.ts")
    deep_edge = _find(deep_edges, "../../utils/helpers")
    all_passed &= _check(
        "'../../utils/helpers' from a nested file -> INTERNAL src/utils/helpers.ts (.. collapsing)",
        deep_edge is not None and deep_edge.target_file == "src/utils/helpers.ts",
        str(deep_edge),
    )

    orphan_edges = _edges_from(edges, "src/components/Orphan.ts")
    orphan_edge = _find(orphan_edges, "../../../nonexistent")
    all_passed &= _check(
        "relative import outside the file set -> EXTERNAL, raw specifier preserved",
        orphan_edge is not None
        and orphan_edge.edge_type == DependencyEdgeType.EXTERNAL
        and orphan_edge.target_package == "../../../nonexistent",
        str(orphan_edge),
    )

    return all_passed


def run_python() -> bool:
    print("\n=== Python import-edge checks ===")
    all_passed = True

    raw_files = _load_tree(FIXTURES_DIR / "python")
    # Ingest under a synthetic "server/" prefix so "app/..." isn't the
    # ingested repo root — exercises suffix (not exact) resolution, same
    # as this repo's own real server/app/... layout.
    files = {f"server/{path}": source for path, source in raw_files.items()}

    edges = extract_import_edges(files)
    auth_edges = _edges_from(edges, "server/app/api/v1/auth.py")

    same_pkg = _find(auth_edges, ".")
    all_passed &= _check(
        "'from . import items' -> INTERNAL server/app/api/v1/__init__.py",
        same_pkg is not None and same_pkg.target_file == "server/app/api/v1/__init__.py",
        str(same_pkg),
    )

    parent_pkg = _find(auth_edges, "..models")
    all_passed &= _check(
        "'from ..models import User' -> INTERNAL server/app/api/models.py",
        parent_pkg is not None and parent_pkg.target_file == "server/app/api/models.py",
        str(parent_pkg),
    )

    os_edge = _find(auth_edges, "os")
    requests_edge = _find(auth_edges, "requests")
    all_passed &= _check(
        "'import os' / 'import requests' -> EXTERNAL",
        os_edge is not None
        and os_edge.edge_type == DependencyEdgeType.EXTERNAL
        and os_edge.target_package == "os"
        and requests_edge is not None
        and requests_edge.target_package == "requests",
        f"{os_edge} / {requests_edge}",
    )

    items_edges = _edges_from(edges, "server/app/api/v1/items.py")
    absolute_edge = _find(items_edges, "app.utils.helpers")
    all_passed &= _check(
        "'from app.utils.helpers import helper' (absolute, suffix match) -> INTERNAL server/app/utils/helpers.py",
        absolute_edge is not None
        and absolute_edge.edge_type == DependencyEdgeType.INTERNAL
        and absolute_edge.target_file == "server/app/utils/helpers.py",
        str(absolute_edge),
    )

    return all_passed


def run_java() -> bool:
    print("\n=== Java import-edge checks ===")
    all_passed = True

    files = _load_tree(FIXTURES_DIR / "java")
    edges = extract_import_edges(files)
    controller_edges = _edges_from(edges, "src/main/java/com/example/app/Controller.java")

    service_edge = _find(controller_edges, "com.example.app.service.UserService")
    all_passed &= _check(
        "regular import -> INTERNAL UserService.java (suffix match under src/main/java/)",
        service_edge is not None
        and service_edge.edge_type == DependencyEdgeType.INTERNAL
        and service_edge.target_file == "src/main/java/com/example/app/service/UserService.java",
        str(service_edge),
    )

    static_edge = _find(controller_edges, "com.example.app.util.Constants.MAX")
    all_passed &= _check(
        "static import -> INTERNAL Constants.java (member name dropped before resolving)",
        static_edge is not None
        and static_edge.edge_type == DependencyEdgeType.INTERNAL
        and static_edge.target_file == "src/main/java/com/example/app/util/Constants.java",
        str(static_edge),
    )

    wildcard_edge = _find(controller_edges, "com.example.app.model.*")
    all_passed &= _check(
        "wildcard import -> always EXTERNAL at the package grain, even though matching files exist",
        wildcard_edge is not None
        and wildcard_edge.edge_type == DependencyEdgeType.EXTERNAL
        and wildcard_edge.target_package == "com.example.app.model",
        str(wildcard_edge),
    )

    stdlib_edge = _find(controller_edges, "java.util.List")
    all_passed &= _check(
        "java.util.List -> EXTERNAL package 'java.util'",
        stdlib_edge is not None
        and stdlib_edge.edge_type == DependencyEdgeType.EXTERNAL
        and stdlib_edge.target_package == "java.util",
        str(stdlib_edge),
    )

    return all_passed


def run_manifests() -> bool:
    print("\n=== Manifest discovery checks ===")
    all_passed = True

    files: dict[str, bytes] = {}
    files.update(_load_tree(FIXTURES_DIR / "js_ts"))
    files.update({f"py/{k}": v for k, v in _load_tree(FIXTURES_DIR / "python").items()})
    files.update({f"mvn/{k}": v for k, v in _load_tree(FIXTURES_DIR / "manifests").items()})

    deps = discover_manifest_dependencies(files)

    npm_deps = [d for d in deps if d.ecosystem == Ecosystem.NPM]
    react_dep = next((d for d in npm_deps if d.name == "react"), None)
    all_passed &= _check(
        "package.json: react dependency found with version",
        react_dep is not None and react_dep.version == "^18.3.1",
        str(react_dep),
    )
    ts_dep = next((d for d in npm_deps if d.name == "typescript"), None)
    all_passed &= _check("package.json: devDependencies included (typescript)", ts_dep is not None, str(ts_dep))

    pip_deps = [d for d in deps if d.ecosystem == Ecosystem.PIP]
    names = {d.name for d in pip_deps}
    all_passed &= _check(
        "requirements.txt + pyproject.toml both parsed",
        {"fastapi", "requests", "python-dotenv", "sqlalchemy", "httpx"} <= names,
        str(sorted(names)),
    )
    requests_dep = next((d for d in pip_deps if d.name == "requests"), None)
    all_passed &= _check(
        "requirements.txt: pinned version captured ('==2.31.0')",
        requests_dep is not None and requests_dep.version == "==2.31.0",
        str(requests_dep),
    )

    maven_deps = [d for d in deps if d.ecosystem == Ecosystem.MAVEN]
    spring_dep = next((d for d in maven_deps if d.name == "org.springframework.boot:spring-boot-starter-web"), None)
    jackson_dep = next((d for d in maven_deps if d.name == "com.fasterxml.jackson.core:jackson-databind"), None)
    all_passed &= _check(
        "pom.xml: versioned dependency parsed",
        spring_dep is not None and spring_dep.version == "3.2.0",
        str(spring_dep),
    )
    all_passed &= _check(
        "pom.xml: version-omitted (parent/BOM-managed) dependency -> version is None",
        jackson_dep is not None and jackson_dep.version is None,
        str(jackson_dep),
    )

    return all_passed


if __name__ == "__main__":
    r1 = run_js_ts()
    r2 = run_python()
    r3 = run_java()
    r4 = run_manifests()
    print("\n=== Summary ===")
    print(f"JS/TS:      {'PASS' if r1 else 'FAIL'}")
    print(f"Python:     {'PASS' if r2 else 'FAIL'}")
    print(f"Java:       {'PASS' if r3 else 'FAIL'}")
    print(f"Manifests:  {'PASS' if r4 else 'FAIL'}")
    sys.exit(0 if (r1 and r2 and r3 and r4) else 1)
