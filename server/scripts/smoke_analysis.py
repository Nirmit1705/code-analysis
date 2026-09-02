"""Manual smoke test for Phase 3.7's final pipeline assembly
(`app.analysis.pipeline`) — the first checked-in smoke script that
exercises the *whole* Phase 3 pipeline (3.1 ingestion through 3.7
dependencies/metrics) as one assembled unit, rather than one module at a
time.

Three parts:

1. `run_fixture_pipeline()` — a small, fully known FastAPI fixture
   (reusing `scripts/fixtures/fastapi_flask`'s own `run_fastapi_fixture`
   file set from `smoke_apis_fastapi_flask.py`, so the expected route
   count is already independently verified there) run through
   `pipeline.analyze_ingested` end to end, with exact counts asserted for
   every fact list `AnalysisResult` produces.
2. `run_real_repo_pipeline()` — this repo's own real `server/app` (Python)
   and `client/src` (TS/TSX), plus `server/pyproject.toml` /
   `client/package.json` / `client/tsconfig.json`, read straight off disk
   into a synthetic `IngestionResult` (no live GitHub tarball download —
   this dogfoods everything except that one I/O step, matching the
   precedent every prior Phase 3.x smoke script already set for real-repo
   spot-checks). Prints overall counts plus a handful of hand-verifiable
   checks: known real API routes (cross-checked against
   `smoke_apis_fastapi_flask.py`'s own real-repo expectations), a known
   real cross-file Python dependency edge, a known real `@/`-aliased TS
   import resolving through this repo's own `client/tsconfig.json`, and
   known real manifest dependencies from `server/pyproject.toml` /
   `client/package.json`.
3. `run_live_analyze_commit()` — the one part of the pipeline (2) doesn't
   exercise: `analyze_commit` itself, the real GitHub-tarball-backed entry
   point, end to end. Mirrors `smoke_ingestion.py`'s own
   `run_live_smoke_test` precedent exactly — same live GitHub App
   installation, same one-off `Nirmit1705/SubTrack` repo (not a claim that
   it's "the" fixed Phase 3+ sample repo, which is still unnamed per
   `CLAUDE.md`), same SKIP-if-unavailable / non-fatal-to-the-suite
   handling for a network/credentials-dependent check. The one thing this
   part alone confirms: `analyze_commit`'s explicit-`commit_sha` contract
   (a real commit sha resolved *here*, by the caller, then handed in) and
   the real tarball-download -> extract -> full-pipeline path all work
   together, not just each piece in isolation.

Run: `uv run python scripts/smoke_analysis.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import pipeline  # noqa: E402
from app.analysis.dependencies import DependencyEdgeType, Ecosystem  # noqa: E402
from app.analysis.ingestion import IngestedFile, IngestionResult  # noqa: E402
from app.services import github_service  # noqa: E402

SERVER_ROOT = Path(__file__).resolve().parent.parent  # `server/`
REPO_ROOT = SERVER_ROOT.parent
FASTAPI_FIXTURES_DIR = SERVER_ROOT / "scripts" / "fixtures" / "fastapi_flask"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _ingested_file(base: Path, rel_path: str) -> IngestedFile:
    absolute = base / rel_path
    return IngestedFile(path=rel_path, absolute_path=absolute, size_bytes=absolute.stat().st_size)


def run_fixture_pipeline() -> bool:
    print("=== Fixture pipeline: fastapi_flask's FastAPI fixture, run end to end ===")
    all_passed = True

    rel_paths = ["simple_app.py", "app/api/v1/auth.py", "app/api/v1/items.py", "app/api/v1/router.py"]
    files = [_ingested_file(FASTAPI_FIXTURES_DIR, rel) for rel in rel_paths]
    ingestion_result = IngestionResult(root_dir=FASTAPI_FIXTURES_DIR, files=files, ignored_paths=["ignored/dummy.txt"])

    result = pipeline.analyze_ingested(ingestion_result, commit_sha="fixture0000000000000000000000000000000000")

    all_passed &= _check("commit_sha carried through unchanged", result.commit_sha == "fixture0000000000000000000000000000000000")
    all_passed &= _check("files: 4", len(result.files) == 4, str(len(result.files)))
    all_passed &= _check(
        "ignored_paths carried through from ingestion", result.ignored_paths == ["ignored/dummy.txt"], str(result.ignored_paths)
    )
    all_passed &= _check("language_counts: python == 4", result.language_counts.get("python") == 4, str(result.language_counts))

    expected_symbol_names = {"register", "login", "me", "list_items", "get_item", "root", "legacy"}
    actual_symbol_names = {s.name for s in result.symbols}
    all_passed &= _check(
        "symbols: exactly the 7 route handlers, nothing else",
        actual_symbol_names == expected_symbol_names,
        str(sorted(actual_symbol_names)),
    )

    all_passed &= _check(
        "api_routes: 8 (matches smoke_apis_fastapi_flask.py's own fixture count)", len(result.api_routes) == 8, str(len(result.api_routes))
    )
    linked = [r for r in result.api_routes if r.handler_symbol is not None]
    all_passed &= _check("every route's handler resolved to a real symbol", len(linked) == len(result.api_routes))

    all_passed &= _check(
        "dependency_edges: 5 (one 'fastapi' import per file, plus router.py's "
        "'from app.api.v1 import auth, items' collapsed to one more)",
        len(result.dependency_edges) == 5,
        str([(e.source_file, e.raw_specifier, e.edge_type) for e in result.dependency_edges]),
    )
    fastapi_edges = [e for e in result.dependency_edges if e.target_package == "fastapi"]
    all_passed &= _check("every file imports the external 'fastapi' package", len(fastapi_edges) == 4, str(fastapi_edges))

    all_passed &= _check(
        "function_complexity: 7 entries, all complexity 1 (no branches in fixture bodies)",
        len(result.function_complexity) == 7 and all(fc.complexity == 1 for fc in result.function_complexity),
        str(result.function_complexity),
    )

    modules_by_name = {m.module: m for m in result.module_metrics}
    all_passed &= _check("module_metrics: 2 modules ('.' and 'app/api/v1')", set(modules_by_name) == {".", "app/api/v1"}, str(set(modules_by_name)))
    v1_module = modules_by_name.get("app/api/v1")
    all_passed &= _check(
        "module 'app/api/v1': 3 files, 5 functions (register/login/me/list_items/get_item)",
        v1_module is not None and v1_module.file_count == 3 and v1_module.function_count == 5,
        str(v1_module),
    )

    return all_passed


def _walk_real(base: Path, rel_prefix: str, extensions: tuple[str, ...]) -> list[IngestedFile]:
    files: list[IngestedFile] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in extensions:
            continue
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(base)).replace("\\", "/")
        files.append(IngestedFile(path=f"{rel_prefix}/{rel}", absolute_path=path, size_bytes=path.stat().st_size))
    return files


def run_real_repo_pipeline() -> bool:
    print("\n=== Real-repo pipeline: this repo's own server/app + client/src ===")
    all_passed = True

    files = _walk_real(REPO_ROOT / "server" / "app", "server/app", (".py",))
    files += _walk_real(REPO_ROOT / "client" / "src", "client/src", (".ts", ".tsx"))
    for manifest_rel in ("server/pyproject.toml", "client/package.json", "client/tsconfig.json"):
        manifest_path = REPO_ROOT / manifest_rel
        if manifest_path.is_file():
            files.append(IngestedFile(path=manifest_rel, absolute_path=manifest_path, size_bytes=manifest_path.stat().st_size))

    ingestion_result = IngestionResult(root_dir=REPO_ROOT, files=files, ignored_paths=[])
    result = pipeline.analyze_ingested(ingestion_result, commit_sha="real0000000000000000000000000000000000000")

    print(
        f"  files={len(result.files)} symbols={len(result.symbols)} api_routes={len(result.api_routes)} "
        f"dependency_edges={len(result.dependency_edges)} external_dependencies={len(result.external_dependencies)} "
        f"function_complexity={len(result.function_complexity)} module_metrics={len(result.module_metrics)}"
    )
    print(f"  language_counts={result.language_counts}")

    all_passed &= _check("files: python + ts/tsx counts are both non-zero", result.language_counts.get("python", 0) > 0 and (
        result.language_counts.get("typescript", 0) + result.language_counts.get("tsx", 0) + result.language_counts.get("javascript", 0) > 0
    ), str(result.language_counts))

    # This broader run (files ingested at their real root-relative paths,
    # "server/app/..." — server/ is a subdirectory of the git repo root,
    # not the Python package root) is what originally surfaced a genuine
    # Phase 3.6 gap: `fastapi_flask.py`'s cross-file `include_router`
    # resolution used to do an exact-match-only lookup, so `router.py`'s
    # `include_router(auth.router)` couldn't resolve `auth` back to its
    # defining file here, and every route composed unprefixed
    # ("/auth/register" instead of "/api/v1/auth/register"). Now fixed
    # (`fastapi_flask.py::_resolve_module_prefix` falls back to a suffix
    # match, same technique as this chunk's own
    # `imports.py::_resolve_by_suffix`) — these routes must retain their
    # correct `/api/v1` prefix here too.
    real_route_expectations = [
        ("POST", "/api/v1/auth/register"),
        ("GET", "/api/v1/auth/github/install"),
        ("GET", "/api/v1/github/repos"),
        ("POST", "/api/v1/repos/{repository_id}/branches/{branch_name:path}/select"),
    ]
    for method, path in real_route_expectations:
        found = next((r for r in result.api_routes if r.method == method and r.path == path), None)
        all_passed &= _check(f"real route present with correct /api/v1 prefix: {method} {path}", found is not None, str(found))

    # A known real cross-file Python dependency: app/api/v1/github.py -> app/api/deps.py.
    github_edges = [e for e in result.dependency_edges if e.source_file == "server/app/api/v1/github.py"]
    deps_edge = next((e for e in github_edges if e.raw_specifier == "app.api.deps"), None)
    all_passed &= _check(
        "real edge: server/app/api/v1/github.py -> INTERNAL server/app/api/deps.py",
        deps_edge is not None and deps_edge.edge_type == DependencyEdgeType.INTERNAL and deps_edge.target_file == "server/app/api/deps.py",
        str(deps_edge),
    )

    # A known real "@/"-aliased TS import, resolved via this repo's own client/tsconfig.json.
    login_edges = [e for e in result.dependency_edges if e.source_file == "client/src/routes/login.tsx"]
    api_alias_edge = next((e for e in login_edges if e.raw_specifier == "@/lib/api"), None)
    all_passed &= _check(
        "real edge: client/src/routes/login.tsx '@/lib/api' -> INTERNAL client/src/lib/api.ts",
        api_alias_edge is not None
        and api_alias_edge.edge_type == DependencyEdgeType.INTERNAL
        and api_alias_edge.target_file == "client/src/lib/api.ts",
        str(api_alias_edge),
    )

    # Known real manifest dependencies.
    npm_names = {d.name for d in result.external_dependencies if d.ecosystem == Ecosystem.NPM}
    pip_names = {d.name for d in result.external_dependencies if d.ecosystem == Ecosystem.PIP}
    all_passed &= _check("server/pyproject.toml: fastapi + sqlalchemy declared", {"fastapi", "sqlalchemy"} <= pip_names, str(sorted(pip_names)))
    all_passed &= _check("client/package.json: react declared", "react" in npm_names, str(len(npm_names)))

    complex_fns = sorted(result.function_complexity, key=lambda fc: fc.complexity, reverse=True)[:5]
    print("  top 5 by complexity:")
    for fc in complex_fns:
        print(f"    {fc.complexity:>3}  {fc.symbol.file_path}:{fc.symbol.line_start}  {fc.symbol.name}")

    return all_passed


LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "SubTrack"


def run_live_analyze_commit() -> bool:
    print(f"\n=== Live smoke test: analyze_commit() against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
    try:
        installations = github_service.list_installations()
        installation = next(
            (i for i in installations if i["account"]["login"].lower() == LIVE_TEST_OWNER.lower()), None
        )
        if installation is None:
            print(f"  [SKIP] no installation found for account {LIVE_TEST_OWNER!r}")
            return True

        token_data = github_service.get_installation_access_token(str(installation["id"]))
        token = token_data["token"]

        branch = github_service.get_branch(token, LIVE_TEST_OWNER, LIVE_TEST_REPO, "main")
        commit_sha = branch["commit"]["sha"]
        print(f"  resolved {LIVE_TEST_OWNER}/{LIVE_TEST_REPO}@main -> {commit_sha} (resolved here, by the caller — analyze_commit itself never does this)")

        result = pipeline.analyze_commit(
            installation_token=token, owner=LIVE_TEST_OWNER, repo=LIVE_TEST_REPO, commit_sha=commit_sha
        )
        print(
            f"  files={len(result.files)} symbols={len(result.symbols)} api_routes={len(result.api_routes)} "
            f"dependency_edges={len(result.dependency_edges)} external_dependencies={len(result.external_dependencies)} "
            f"function_complexity={len(result.function_complexity)} module_metrics={len(result.module_metrics)}"
        )
        print(f"  language_counts={result.language_counts}")
        return result.commit_sha == commit_sha and len(result.files) > 0
    except Exception as exc:  # noqa: BLE001 — smoke script, report and continue
        print(f"  [ERROR] live smoke test failed: {exc!r}")
        return False


if __name__ == "__main__":
    r1 = run_fixture_pipeline()
    r2 = run_real_repo_pipeline()
    r3 = run_live_analyze_commit()
    print("\n=== Summary ===")
    print(f"Fixture pipeline:        {'PASS' if r1 else 'FAIL'}")
    print(f"Real-repo pipeline:      {'PASS' if r2 else 'FAIL'}")
    print(f"Live analyze_commit():   {'PASS' if r3 else 'FAIL (see above — may be a network/credentials issue)'}")
    # Matches smoke_ingestion.py's own precedent: the live network/credentials
    # check doesn't gate the suite's exit code, so CI-style runs without live
    # GitHub access still pass on the two fully offline parts.
    sys.exit(0 if (r1 and r2) else 1)
