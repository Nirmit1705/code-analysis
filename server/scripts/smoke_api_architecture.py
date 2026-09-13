"""Manual smoke test for the Architecture backend:
`app/services/module_classification.py::classify_modules`,
`app/analysis/datasource.py::detect_database_provider`,
`app/services/analysis_read_service.py::load_dependency_edges`, and
`GET /repos/{id}/architecture` (`app/api/v1/architecture.py`).

Same `_check`/`run_*()`/PASS-FAIL/exit-code template every other
`smoke_*.py` script uses; seeding helpers duplicated from
`smoke_api_analysis.py`/`smoke_storage.py` rather than imported (this
codebase's established "small helper duplication over cross-script coupling"
convention).

**Revised after real-repo review** (see `server/CLAUDE.md`'s Phase 6.5/6.6
follow-up): the classification rules changed from a pure depth-2 path
truncation to semantic-role recognition (routes/controllers/services/
middleware, grounded in real `ApiEndpoint` ownership and a curated
keyword vocabulary) plus named external-service recognition (Database/
Redis/curated SaaS SDKs). Groups 3 and 5 below were rewritten to match;
group 6 is new, added specifically to regression-test the core motivating
fix — real internal edges between recognized components must survive as
visible graph edges, not be silently absorbed the way the old depth-2
grouping absorbed 97% of them.

Eight groups:

1. `run_not_analyzed()` — a repository with zero completed runs -> `GET`
   returns `{analyzed: false}`, no nodes/edges.
2. `run_ownership_scoping()` — a second user's token -> 404.
3. `run_classify_modules_unit()` — `classify_modules` exercised directly,
   no DB, no HTTP: hand-built `File`/`ApiEndpoint` objects covering, in
   order — real `ApiEndpoint` ownership taking priority over a data-
   keyword folder match at the *grouping* stage (not just kind); the
   pre-existing data/cache/gateway folder-keyword rule; the new
   controllers/services/middleware role vocabulary, including its
   language-aware kind (a frontend-side `services/` folder reads
   `frontend`, not `service`); the frontend language+keyword rule for
   non-role groups; the plain default; the root `"."` group's `"(root)"`
   label; and the >MAX_MODULE_GROUPS folding behavior (30 singleton
   groups -> 24 kept + one `other:service`).
4. `run_load_dependency_edges_roundtrip()` — persists the `cyclic/` fixture
   (already hand-verified by `smoke_graph.py`/`smoke_storage.py`) via the
   real `create_queued_analysis_run`/`persist_analysis_run`, then confirms
   `load_dependency_edges` reconstructs the exact same
   `(source_file, target_file, edge_type, target_package)` set the
   original in-memory `analyze_ingested` result produced (the two fields
   the DB never persists, `raw_specifier`/`line`, are excluded from the
   comparison on purpose — see that function's own docstring).
5. `run_architecture_cyclic_fixture()` — `GET /repos/{id}/architecture`
   against the same persisted `cyclic/` run: exact node set (4 real
   groups, all default `service` — none of `cyclic/`'s folder names hit
   any keyword list), exact edge set including both directions of the
   `module_a`<->`module_b` cycle marked `circular=True`, the deduped
   `module_c`->`module_a` edge (two import statements, one edge) marked
   `circular=False` — and, since Python's `os` (imported by
   `orphan_module`) is a language builtin, now correctly *excluded* from
   the external surface entirely (no external node at all for this
   fixture — the old, less-correct behavior of showing every external
   import including builtins is what this regression-tests against).
6. `run_architecture_layering()` — **new**, the core regression test for
   this fix: a small, real Express-shaped fixture
   (`scripts/fixtures/architecture_layering/`) with real
   `routes/`->`controllers/`->`services/` files that really `require()`
   each other, a real `@prisma/client` + `ioredis` import, and a real
   `schema.prisma`. Confirms — through the full real pipeline, not a
   hand-built shortcut — that routes/controllers/services appear as
   three separate, correctly-labeled, connected nodes (not one merged
   blob swallowing the real edges between them, the old bug), that a
   real "PostgreSQL" node appears (from `schema.prisma`'s provider line)
   with an edge from Services, and a real "Redis" node with an edge from
   Services too.
7. `run_real_repo_dogfood()` (secondary) — this repo's own `server/app` +
   `client/src`. No exact kind/grouping expectations are asserted; only
   structural invariants that must hold regardless of how classification
   groups things: every file is accounted for in exactly one non-external
   node, edge weights are positive, and no edge names a node id absent
   from the node list.
8. `run_live_bonus()` (non-gating) — the real, already-analyzed
   `Nirmit1705/HireSight` repository already in this dev environment's
   database. Skips cleanly if no such repository/completed run exists.

Run: `uv run python scripts/smoke_api_architecture.py` (from `server/`).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.analysis.apis import Framework  # noqa: E402
from app.analysis.dependencies import DependencyEdgeType  # noqa: E402
from app.analysis.ingestion import IngestedFile, IngestionResult  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.pipeline import analyze_ingested  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import analysis_read_service  # noqa: E402
from app.services.module_classification import MAX_MODULE_GROUPS, ModuleKind, classify_modules  # noqa: E402
from app.services.repo_service import create_queued_analysis_run, persist_analysis_run  # noqa: E402

SERVER_ROOT = Path(__file__).resolve().parent.parent  # server/
REPO_ROOT = SERVER_ROOT.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "dependency_cycle"

# Phase 6's confirmed sample repo (docs/PHASE_6_PLAN.md) — live bonus only.
LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- seeding helpers (same pattern as smoke_api_analysis.py/smoke_storage.py) ---


def _get_or_create_user(db: Session, email: str) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _get_or_create_repository(db: Session, user: User, *, github_repo_id: str, owner: str, name: str) -> Repository:
    repository = db.execute(
        select(Repository).where(
            Repository.connected_user_id == user.id, Repository.github_repo_id == github_repo_id
        )
    ).scalar_one_or_none()
    if repository is None:
        repository = Repository(
            owner=owner, name=name, full_name=f"{owner}/{name}", github_repo_id=github_repo_id,
            default_branch="main", connected_user_id=user.id, installation_id="fake-installation-id",
        )
        db.add(repository)
        db.commit()
        db.refresh(repository)
    return repository


def _clear_previous_runs(db: Session, repository: Repository) -> None:
    db.execute(delete(AnalysisRun).where(AnalysisRun.repository_id == repository.id))
    db.commit()


def _load_ingested_files(root: Path, prefix: str) -> list[IngestedFile]:
    return [
        IngestedFile(path=f"{prefix}/{p.relative_to(root).as_posix()}", absolute_path=p, size_bytes=p.stat().st_size)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]


def _walk_real_repo(base: Path, rel_prefix: str, extensions: tuple[str, ...]) -> list[IngestedFile]:
    files: list[IngestedFile] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in extensions:
            continue
        if "__pycache__" in path.parts or "node_modules" in path.parts:
            continue
        rel = str(path.relative_to(base)).replace("\\", "/")
        files.append(IngestedFile(path=f"{rel_prefix}/{rel}", absolute_path=path, size_bytes=path.stat().st_size))
    return files


def _persist_cyclic_fixture(db: Session, repository: Repository) -> AnalysisRun:
    root = FIXTURES_DIR / "cyclic"
    files = _load_ingested_files(root, "cyclic")
    ingestion_result = IngestionResult(root_dir=root, files=files, ignored_paths=[])
    commit_sha = "cyclic".ljust(40, "0")[:40]
    result = analyze_ingested(ingestion_result, commit_sha=commit_sha)

    _clear_previous_runs(db, repository)
    queued_run = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha)
    return persist_analysis_run(db, analysis_run=queued_run, analysis_result=result), result


# --- group 1: not analyzed --------------------------------------------------------


def run_not_analyzed() -> bool:
    print("=== group 1: repository with no completed run -> analyzed: false ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-arch-not-analyzed@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-arch-not-analyzed", owner="smoke-api", name="not-analyzed"
        )
        _clear_previous_runs(db, repository)

        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/architecture", headers=_auth(token))

        all_passed = _check("GET -> 200", resp.status_code == 200, resp.text)
        body = resp.json()
        all_passed &= _check("analyzed == false", body.get("analyzed") is False, str(body))
        all_passed &= _check("nodes == []", body.get("nodes") == [], str(body))
        all_passed &= _check("edges == []", body.get("edges") == [], str(body))
        return all_passed
    finally:
        db.close()


# --- group 2: ownership scoping ----------------------------------------------------


def run_ownership_scoping() -> bool:
    print("\n=== group 2: a second user's token -> 404 ===")
    db = SessionLocal()
    try:
        owner_user = _get_or_create_user(db, "smoke-api-arch-owner@local.test")
        other_user = _get_or_create_user(db, "smoke-api-arch-other@local.test")
        repository = _get_or_create_repository(
            db, owner_user, github_repo_id="smoke-api-arch-ownership", owner="smoke-api", name="ownership"
        )
        _clear_previous_runs(db, repository)

        other_token = create_access_token(subject=other_user.email)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/architecture", headers=_auth(other_token))
        return _check("other user's GET -> 404", resp.status_code == 404, resp.text)
    finally:
        db.close()


# --- group 3: classify_modules unit coverage ---------------------------------------

_FIXED_REPO_ID = uuid.uuid4()
_FIXED_RUN_ID = uuid.uuid4()


def _file(path: str, module: str, language: Language | None, loc: int) -> File:
    return File(
        id=uuid.uuid4(), repository_id=_FIXED_REPO_ID, analysis_run_id=_FIXED_RUN_ID,
        path=path, module=module, language=language, loc=loc, size_bytes=loc * 20,
        last_analyzed_commit_sha="0" * 40,
    )


def _api(method: str, path: str, module: str) -> ApiEndpoint:
    return ApiEndpoint(
        id=uuid.uuid4(), repository_id=_FIXED_REPO_ID, analysis_run_id=_FIXED_RUN_ID,
        method=method, path=path, module=module, framework=Framework.FASTAPI,
        file_id=uuid.uuid4(),
    )


def run_classify_modules_unit() -> bool:
    print("\n=== group 3: classify_modules() unit coverage (no DB, no HTTP) ===")
    all_passed = True

    # --- 3a: real ApiEndpoint ownership takes priority over a data-keyword
    # folder match *at the grouping stage itself* — a module whose path
    # contains "db" (a data keyword) but that owns a real API endpoint is
    # pulled into its own "routes" group, not folded into a data group.
    # Also covers the pre-existing cache/gateway folder-keyword rule
    # (unaffected by the rewrite) in the same pass. ---
    files_a = [
        _file("backend/app/models/user.py", "backend/app/models", Language.PYTHON, 50),  # data keyword, no endpoint
        _file("backend/app/db_routes/handler.py", "backend/app/db_routes", Language.PYTHON, 20),  # "db" keyword + a real endpoint
        _file("infra/cache/store.py", "infra/cache", Language.PYTHON, 40),
        _file("infra/gateway/router.py", "infra/gateway", Language.PYTHON, 25),
    ]
    apis_a = [_api("GET", "/records", "backend/app/db_routes")]
    result_a = classify_modules(files_a, apis_a)
    kind_a = {g.id: g.kind for g in result_a.groups}
    label_a = {g.id: g.label for g in result_a.groups}
    all_passed &= _check(
        "backend/app (models, no endpoint) -> data (folder keyword)",
        kind_a.get("backend/app") is ModuleKind.DATA, str(kind_a),
    )
    all_passed &= _check(
        "backend/routes (db_routes, HAS an endpoint) -> gateway, NOT data — endpoint ownership wins at grouping",
        kind_a.get("backend/routes") is ModuleKind.GATEWAY, str(kind_a),
    )
    all_passed &= _check(
        "backend/routes labeled 'API Routes'", label_a.get("backend/routes") == "API Routes", str(label_a),
    )
    all_passed &= _check("infra/cache -> cache (folder keyword)", kind_a.get("infra/cache") is ModuleKind.CACHE, str(kind_a))
    all_passed &= _check("infra/gateway -> gateway (folder keyword)", kind_a.get("infra/gateway") is ModuleKind.GATEWAY, str(kind_a))
    routes_group = next(g for g in result_a.groups if g.id == "backend/routes")
    all_passed &= _check(
        "backend/routes.apis lists its real endpoint", routes_group.apis == ["GET /records"], str(routes_group.apis),
    )

    # --- 3b: a route-owning group gets recognized regardless of its own
    # folder name (no "routes"/"api" folder-keyword guessing exists at
    # all — ApiEndpoint ownership is the only signal). ---
    files_b = [_file("svc/misc/endpoint.py", "svc/misc", Language.PYTHON, 15)]
    apis_b = [_api("GET", "/misc", "svc/misc")]
    result_b = classify_modules(files_b, apis_b)
    kind_b = {g.id: g.kind for g in result_b.groups}
    label_b = {g.id: g.label for g in result_b.groups}
    all_passed &= _check(
        "svc/misc (no 'routes'/'api' folder name at all) -> svc/routes, gateway, 'API Routes'",
        kind_b.get("svc/routes") is ModuleKind.GATEWAY and label_b.get("svc/routes") == "API Routes",
        f"{kind_b} {label_b}",
    )

    # --- 3c: the new controllers/services/middleware role vocabulary,
    # scanned over every file's full module path (same mechanism as the
    # data/cache/gateway rule). Kind is language-aware: a frontend-side
    # "services" folder reads as frontend, not backend business logic. ---
    files_c = [
        _file("server/src/controllers/user.ts", "server/src/controllers", Language.TYPESCRIPT, 30),
        _file("server/src/services/user.ts", "server/src/services", Language.TYPESCRIPT, 40),
        _file("server/src/middleware/auth.ts", "server/src/middleware", Language.TYPESCRIPT, 10),
        _file("client/src/services/api.ts", "client/src/services", Language.TSX, 20),
    ]
    result_c = classify_modules(files_c, [])
    kind_c = {g.id: g.kind for g in result_c.groups}
    label_c = {g.id: g.label for g in result_c.groups}
    all_passed &= _check(
        "server/controllers -> service, 'Controllers'",
        kind_c.get("server/controllers") is ModuleKind.SERVICE and label_c.get("server/controllers") == "Controllers",
        f"{kind_c} {label_c}",
    )
    all_passed &= _check(
        "server/services -> service, 'Services'",
        kind_c.get("server/services") is ModuleKind.SERVICE and label_c.get("server/services") == "Services",
        f"{kind_c} {label_c}",
    )
    all_passed &= _check(
        "server/middleware -> service, 'Middleware'",
        kind_c.get("server/middleware") is ModuleKind.SERVICE and label_c.get("server/middleware") == "Middleware",
        f"{kind_c} {label_c}",
    )
    all_passed &= _check(
        "client/services -> FRONTEND (language-aware), still labeled 'Services'",
        kind_c.get("client/services") is ModuleKind.FRONTEND and label_c.get("client/services") == "Services",
        f"{kind_c} {label_c}",
    )

    # --- 3d: the pre-existing frontend rule (predominantly JS/TS/TSX +
    # frontend keyword, zero API endpoints) still applies to groups that
    # match none of the roles above; a JS-heavy group with no frontend
    # keyword still falls through to the plain default. ---
    files_d = [
        _file("apps/client/App.tsx", "apps/client", Language.TSX, 60),
        _file("apps/client/index.ts", "apps/client", Language.TYPESCRIPT, 15),
        _file("tools/scripts/build.ts", "tools/scripts", Language.TYPESCRIPT, 25),
    ]
    result_d = classify_modules(files_d, [])
    kind_d = {g.id: g.kind for g in result_d.groups}
    all_passed &= _check("apps/client -> frontend", kind_d.get("apps/client") is ModuleKind.FRONTEND, str(kind_d))
    all_passed &= _check(
        "tools/scripts -> service (JS-heavy but no frontend-keyword path segment)",
        kind_d.get("tools/scripts") is ModuleKind.SERVICE, str(kind_d),
    )

    # --- 3e: the root "." group gets a real display label, not the bare
    # period. ---
    files_e = [_file("README.md", ".", None, 5)]
    result_e = classify_modules(files_e, [])
    root_group = result_e.groups[0]
    all_passed &= _check(
        "root group: id='.', label='(root)'",
        root_group.id == "." and root_group.label == "(root)", str(root_group),
    )

    # --- 3f: folding beyond MAX_MODULE_GROUPS — 30 singleton groups, all
    # defaulting to service, none large enough to differentiate by file
    # count -> ties broken by id, so g00..g23 are kept and g24..g29 fold
    # into one "other:service" node. Unaffected by the rewrite (none of
    # these keys touch any recognized role). ---
    files_f = [_file(f"g{i:02d}/mod/f.py", f"g{i:02d}/mod", Language.PYTHON, 10) for i in range(30)]
    result_f = classify_modules(files_f, [])
    ids_f = {g.id for g in result_f.groups}
    all_passed &= _check(
        f"30 singleton groups fold to {MAX_MODULE_GROUPS} kept + 1 other bucket",
        len(result_f.groups) == MAX_MODULE_GROUPS + 1, str(sorted(ids_f)),
    )
    all_passed &= _check("other:service is present", "other:service" in ids_f, str(sorted(ids_f)))
    other_group_f = next(g for g in result_f.groups if g.id == "other:service")
    all_passed &= _check("other:service folds exactly the 6 overflow groups' files", other_group_f.file_count == 6, str(other_group_f))
    all_passed &= _check(
        "kept groups are exactly g00..g23 (ties broken by id ascending)",
        ids_f - {"other:service"} == {f"g{i:02d}/mod" for i in range(24)}, str(sorted(ids_f)),
    )
    all_passed &= _check(
        "overflow files map to the folded id in file_group_id",
        all(result_f.file_group_id[f"g{i:02d}/mod/f.py"] == "other:service" for i in range(24, 30)),
    )
    all_passed &= _check(
        "kept files map to their own group id in file_group_id",
        all(result_f.file_group_id[f"g{i:02d}/mod/f.py"] == f"g{i:02d}/mod" for i in range(24)),
    )

    return all_passed


# --- group 4: load_dependency_edges round-trip -------------------------------------


def run_load_dependency_edges_roundtrip() -> bool:
    print("\n=== group 4: load_dependency_edges() reconstructs the persisted cyclic/ fixture exactly ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-arch-edges@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-arch-edges", owner="smoke-api", name="edges"
        )
        run, in_memory_result = _persist_cyclic_fixture(db, repository)

        reconstructed = analysis_read_service.load_dependency_edges(db, run.id)

        def _key(e):
            return (e.source_file, e.target_file, e.edge_type, e.target_package)

        expected = {_key(e) for e in in_memory_result.dependency_edges}
        actual = {_key(e) for e in reconstructed}

        all_passed = _check(
            "reconstructed edge set matches the original in-memory result exactly",
            actual == expected, f"expected={expected}\n  actual={actual}",
        )

        file_rows = db.execute(select(File).where(File.analysis_run_id == run.id)).scalars().all()
        module_by_path = {f.path: f.module for f in file_rows}
        all_passed &= _check(
            "every reconstructed edge's module matches its source File's real persisted module",
            all(e.module == module_by_path[e.source_file] for e in reconstructed),
            str([(e.source_file, e.module, module_by_path.get(e.source_file)) for e in reconstructed]),
        )
        return all_passed
    finally:
        db.close()


# --- group 5: GET /architecture against the persisted cyclic/ fixture --------------


def run_architecture_cyclic_fixture() -> bool:
    print("\n=== group 5: GET /repos/{id}/architecture against the persisted cyclic/ fixture ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-arch-cyclic@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-arch-cyclic", owner="smoke-api", name="cyclic"
        )
        run, _ = _persist_cyclic_fixture(db, repository)

        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/architecture", headers=_auth(token))
        all_passed = _check("GET -> 200", resp.status_code == 200, resp.text)
        if not all_passed:
            return False
        body = resp.json()
        all_passed &= _check("analyzed == true", body["analyzed"] is True, str(body))
        all_passed &= _check("analysis_run_id matches the persisted run", body["analysis_run_id"] == str(run.id), str(body))

        node_ids = {n["id"] for n in body["nodes"]}
        all_passed &= _check(
            "exactly the 4 real groups — no external node (orphan_module's only import, "
            "Python's `os` builtin, is now correctly excluded, not shown as 'architecture')",
            node_ids == {"cyclic/module_a", "cyclic/module_b", "cyclic/module_c", "cyclic/orphan_module"},
            str(node_ids),
        )
        kinds = {n["id"]: n["kind"] for n in body["nodes"]}
        all_passed &= _check(
            "the 4 real groups all default to service (no keyword/API/frontend signal in this fixture)",
            all(kinds[k] == "service" for k in ("cyclic/module_a", "cyclic/module_b", "cyclic/module_c", "cyclic/orphan_module")),
            str(kinds),
        )

        edges_by_pair = {(e["source"], e["target"]): e for e in body["edges"]}
        all_passed &= _check(
            "both directions of the module_a<->module_b cycle are present and circular",
            edges_by_pair.get(("cyclic/module_a", "cyclic/module_b"), {}).get("circular") is True
            and edges_by_pair.get(("cyclic/module_b", "cyclic/module_a"), {}).get("circular") is True,
            str(edges_by_pair),
        )
        mc_to_ma = edges_by_pair.get(("cyclic/module_c", "cyclic/module_a"))
        all_passed &= _check(
            "module_c -> module_a is present, weight 1 (deduped from 2 import statements), NOT circular",
            mc_to_ma == {"source": "cyclic/module_c", "target": "cyclic/module_a", "weight": 1, "circular": False},
            str(mc_to_ma),
        )
        all_passed &= _check(
            "no edge into 'external' at all — orphan_module's `import os` is excluded, not shown",
            not any(e["target"] == "external" for e in body["edges"]),
            str(body["edges"]),
        )
        all_passed &= _check("exactly 3 edges total (the os-builtin edge no longer exists)", len(body["edges"]) == 3, str(body["edges"]))
        return all_passed
    finally:
        db.close()


# --- group 6: real component layering (the core regression test) ------------------


def run_architecture_layering() -> bool:
    print("\n=== group 6: routes -> controllers -> services layering, real edges survive, named external services ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-arch-layering@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-arch-layering", owner="smoke-api", name="layering"
        )

        fixture_root = Path(__file__).resolve().parent / "fixtures" / "architecture_layering"
        files = [
            IngestedFile(path=p.relative_to(fixture_root).as_posix(), absolute_path=p, size_bytes=p.stat().st_size)
            for p in sorted(fixture_root.rglob("*"))
            if p.is_file()
        ]
        ingestion_result = IngestionResult(root_dir=fixture_root, files=files, ignored_paths=[])
        commit_sha = "layering".ljust(40, "0")[:40]
        result = analyze_ingested(ingestion_result, commit_sha=commit_sha)

        _clear_previous_runs(db, repository)
        queued_run = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha)
        run = persist_analysis_run(db, analysis_run=queued_run, analysis_result=result)

        all_passed = _check(
            "AnalysisRun.database_provider persisted from schema.prisma", run.database_provider == "postgresql", str(run.database_provider)
        )

        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/architecture", headers=_auth(token))
        all_passed &= _check("GET -> 200", resp.status_code == 200, resp.text)
        if not all_passed:
            return False
        body = resp.json()

        nodes_by_id = {n["id"]: n for n in body["nodes"]}
        all_passed &= _check(
            "routes/controllers/services are three separate nodes (not one merged blob)",
            {"server/routes", "server/controllers", "server/services"} <= set(nodes_by_id),
            str(sorted(nodes_by_id)),
        )
        all_passed &= _check(
            "server/routes: kind=gateway, label='API Routes', carries the real GET /users endpoint",
            nodes_by_id["server/routes"]["kind"] == "gateway"
            and nodes_by_id["server/routes"]["label"] == "API Routes"
            and nodes_by_id["server/routes"]["apis"] == ["GET /users"],
            str(nodes_by_id["server/routes"]),
        )
        all_passed &= _check(
            "server/controllers: kind=service, label='Controllers'",
            nodes_by_id["server/controllers"]["kind"] == "service" and nodes_by_id["server/controllers"]["label"] == "Controllers",
            str(nodes_by_id["server/controllers"]),
        )
        all_passed &= _check(
            "server/services: kind=service, label='Services'",
            nodes_by_id["server/services"]["kind"] == "service" and nodes_by_id["server/services"]["label"] == "Services",
            str(nodes_by_id["server/services"]),
        )

        edge_pairs = {(e["source"], e["target"]) for e in body["edges"]}
        all_passed &= _check(
            "real edge server/routes -> server/controllers survives (the core fix — this used to be "
            "absorbed as 'same group' under the old depth-2 grouping)",
            ("server/routes", "server/controllers") in edge_pairs, str(sorted(edge_pairs)),
        )
        all_passed &= _check(
            "real edge server/controllers -> server/services survives",
            ("server/controllers", "server/services") in edge_pairs, str(sorted(edge_pairs)),
        )

        db_node = nodes_by_id.get("data:database")
        all_passed &= _check(
            "a real 'PostgreSQL' node exists (from schema.prisma's provider line, not a generic guess)",
            db_node is not None and db_node["label"] == "PostgreSQL" and db_node["kind"] == "data",
            str(db_node),
        )
        all_passed &= _check(
            "server/services -> data:database edge exists (real @prisma/client import)",
            ("server/services", "data:database") in edge_pairs, str(sorted(edge_pairs)),
        )

        redis_node = nodes_by_id.get("cache:redis")
        all_passed &= _check(
            "a real 'Redis' node exists (from the real ioredis import)",
            redis_node is not None and redis_node["label"] == "Redis" and redis_node["kind"] == "cache",
            str(redis_node),
        )
        all_passed &= _check(
            "server/services -> cache:redis edge exists (real ioredis import)",
            ("server/services", "cache:redis") in edge_pairs, str(sorted(edge_pairs)),
        )
        # `express` itself is a real external import too, and it's
        # deliberately *not* curated (Express is already represented via
        # the "routes" recognition — a redundant "Express" external node
        # would add noise, not signal) — so it correctly falls into the
        # generic catch-all rather than vanishing.
        all_passed &= _check(
            "the generic 'External Dependencies' node exists for express (not curated, not excluded)",
            "external" in nodes_by_id, str(sorted(nodes_by_id)),
        )
        all_passed &= _check(
            "server/routes -> external edge exists (the real require('express'))",
            ("server/routes", "external") in edge_pairs, str(sorted(edge_pairs)),
        )
        return all_passed
    finally:
        db.close()


# --- group 7 (secondary): real dogfood run -----------------------------------------


def run_real_repo_dogfood() -> bool:
    print("\n=== group 7 (secondary): real dogfood run — this repo's own server/app + client/src ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-arch-dogfood@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-arch-dogfood", owner="smoke-api", name="dogfood"
        )

        files = _walk_real_repo(REPO_ROOT / "server" / "app", "server/app", (".py",))
        files += _walk_real_repo(REPO_ROOT / "client" / "src", "client/src", (".ts", ".tsx"))
        ingestion_result = IngestionResult(root_dir=REPO_ROOT, files=files, ignored_paths=[])
        commit_sha = "dogfood".ljust(40, "0")[:40]
        result = analyze_ingested(ingestion_result, commit_sha=commit_sha)

        _clear_previous_runs(db, repository)
        queued_run = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha)
        run = persist_analysis_run(db, analysis_run=queued_run, analysis_result=result)

        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/architecture", headers=_auth(token))
        all_passed = _check("GET -> 200", resp.status_code == 200, resp.text)
        if not all_passed:
            return False
        body = resp.json()
        all_passed &= _check("analyzed == true", body["analyzed"] is True, str(body))

        total_file_count = db.execute(select(File).where(File.analysis_run_id == run.id)).scalars().all()
        # Every synthetic node (the generic "external" catch-all, or a
        # named one like "data:database"/"cache:redis") always reports
        # files=0 — summing over *all* nodes is equivalent to summing only
        # the real file-derived ones, without needing to enumerate every
        # possible synthetic node id.
        all_passed &= _check(
            "sum of all node file counts == total persisted File rows",
            sum(n["files"] for n in body["nodes"]) == len(total_file_count),
            f"{sum(n['files'] for n in body['nodes'])} vs {len(total_file_count)}",
        )
        node_ids = {n["id"] for n in body["nodes"]}
        all_passed &= _check(
            "every edge names nodes that are actually in the node list",
            all(e["source"] in node_ids and e["target"] in node_ids for e in body["edges"]),
            str([e for e in body["edges"] if e["source"] not in node_ids or e["target"] not in node_ids]),
        )
        all_passed &= _check(
            "every edge has a positive weight", all(e["weight"] > 0 for e in body["edges"]),
        )
        print(f"  node kinds observed: {sorted({n['kind'] for n in body['nodes']})} ({len(body['nodes'])} nodes, {len(body['edges'])} edges)")
        return all_passed
    finally:
        db.close()


# --- group 8 (non-gating): live bonus against Nirmit1705/HireSight ----------------


def run_live_bonus() -> bool:
    print(f"\n=== group 8 (non-gating): live GET /architecture against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
    db = SessionLocal()
    try:
        repository = db.execute(
            select(Repository).where(Repository.owner == LIVE_TEST_OWNER, Repository.name == LIVE_TEST_REPO)
        ).scalars().first()
        if repository is None:
            print(f"  [SKIP] no {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} repository found in this database")
            return True

        run = analysis_read_service.get_latest_completed_run(db, repository.id)
        if run is None:
            print(f"  [SKIP] {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} has no completed analysis run in this database")
            return True

        owner_user = db.get(User, repository.connected_user_id)
        token = create_access_token(subject=owner_user.email)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/architecture", headers=_auth(token))
        all_passed = _check("GET -> 200", resp.status_code == 200, resp.text)
        if not all_passed:
            return False
        body = resp.json()
        all_passed &= _check("analyzed == true", body["analyzed"] is True, str(body))

        by_kind: dict[str, int] = {}
        for n in body["nodes"]:
            by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
        print(f"  {len(body['nodes'])} nodes, {len(body['edges'])} edges, kind mix: {by_kind}")
        for n in sorted(body["nodes"], key=lambda n: -n["files"])[:10]:
            print(f"    {n['id']:<30} kind={n['kind']:<10} files={n['files']:<5} apis={len(n['apis'])}")
        return all_passed
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [ERROR] live bonus check failed: {exc!r}")
        return False
    finally:
        db.close()


def main() -> int:
    r1 = run_not_analyzed()
    r2 = run_ownership_scoping()
    r3 = run_classify_modules_unit()
    r4 = run_load_dependency_edges_roundtrip()
    r5 = run_architecture_cyclic_fixture()
    r6 = run_architecture_layering()
    r7 = run_real_repo_dogfood()
    r8 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. Not analyzed:                {'PASS' if r1 else 'FAIL'}")
    print(f"2. Ownership scoping:           {'PASS' if r2 else 'FAIL'}")
    print(f"3. classify_modules() unit:     {'PASS' if r3 else 'FAIL'}")
    print(f"4. load_dependency_edges:       {'PASS' if r4 else 'FAIL'}")
    print(f"5. Architecture (cyclic fixture): {'PASS' if r5 else 'FAIL'}")
    print(f"6. Architecture layering (core fix): {'PASS' if r6 else 'FAIL'}")
    print(f"7. Real repo dogfood (secondary): {'PASS' if r7 else 'FAIL'}")
    print(f"8. Live bonus (non-gating):     {'PASS' if r8 else 'FAIL (see above)'}")
    return 0 if (r1 and r2 and r3 and r4 and r5 and r6 and r7) else 1


if __name__ == "__main__":
    sys.exit(main())
