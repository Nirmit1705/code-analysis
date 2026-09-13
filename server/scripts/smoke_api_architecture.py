"""Manual smoke test for Phase 6.5's Architecture backend:
`app/services/module_classification.py::classify_modules`,
`app/services/analysis_read_service.py::load_dependency_edges`, and
`GET /repos/{id}/architecture` (`app/api/v1/architecture.py`).

Same `_check`/`run_*()`/PASS-FAIL/exit-code template every other
`smoke_*.py` script uses; seeding helpers duplicated from
`smoke_api_analysis.py`/`smoke_storage.py` rather than imported (this
codebase's established "small helper duplication over cross-script coupling"
convention).

Seven groups:

1. `run_not_analyzed()` — a repository with zero completed runs -> `GET`
   returns `{analyzed: false}`, no nodes/edges.
2. `run_ownership_scoping()` — a second user's token -> 404.
3. `run_classify_modules_unit()` — `classify_modules` exercised directly,
   no DB, no HTTP: hand-built `File`/`ApiEndpoint` objects covering all
   four ordered classification rules (folder-keyword incl. the
   "matches on any file's full module path, not just the group's own
   truncated depth-2 key" behavior; a folder-keyword group suppressing
   rule 2's gateway-promotion; the promotion itself when no rule-1 gateway
   exists, picking the busiest+thinnest API group; the frontend
   language+keyword rule; the plain default) plus the >MAX_MODULE_GROUPS
   folding behavior (30 singleton groups -> 24 kept + one `other:service`).
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
   `circular=False`, and a synthetic `external` node + edge for
   `orphan_module`'s `import os`.
6. `run_real_repo_dogfood()` (secondary) — this repo's own `server/app` +
   `client/src`, same ingestion technique as `smoke_storage.py`'s own
   group 4. No exact kind/grouping expectations are asserted (this repo's
   own shape collapses almost entirely to two depth-2 groups,
   `server/app` and `client/src` — see the module-level note in
   `server/CLAUDE.md`'s Phase 6.5 write-up for why that's a real,
   observed consequence of the plan's literal grouping rule, not a bug);
   only structural invariants that must hold regardless of grouping
   granularity: every file is accounted for in exactly one non-external
   node, edge weights are positive, and no edge names a node id absent
   from the node list.
7. `run_live_bonus()` (non-gating) — the real, already-analyzed
   `Nirmit1705/HireSight` repository already in this dev environment's
   database (Phase 6.1-6.4's own sample repo), for the plan's own
   "eyeball that the kind mix looks sane for HireSight's real
   frontend/backend split" verification step. Skips cleanly if no such
   repository/completed run exists in this database.

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

    # --- 3a: rule-1 (folder keyword, matched over the group's full file
    # paths, not just its truncated 2-segment key) takes priority over
    # rule 2 (API endpoints present) even when the group HAS endpoints;
    # also covers cache and gateway-by-folder in the same pass. ---
    files_a = [
        _file("backend/app/models/user.py", "backend/app/models", Language.PYTHON, 50),
        _file("backend/app/api/routes.py", "backend/app/api", Language.PYTHON, 20),  # same group as above (depth-2 "backend/app")
        _file("infra/cache/store.py", "infra/cache", Language.PYTHON, 40),
        _file("infra/gateway/router.py", "infra/gateway", Language.PYTHON, 25),
    ]
    apis_a = [
        _api("GET", "/users", "backend/app/api"),
        _api("GET", "/health", "infra/gateway"),
    ]
    result_a = classify_modules(files_a, apis_a)
    kind_a = {g.id: g.kind for g in result_a.groups}
    all_passed &= _check(
        "backend/app -> data (folder-keyword 'models' beats its own API endpoint)",
        kind_a.get("backend/app") is ModuleKind.DATA, str(kind_a),
    )
    all_passed &= _check("infra/cache -> cache (folder keyword)", kind_a.get("infra/cache") is ModuleKind.CACHE, str(kind_a))
    all_passed &= _check(
        "infra/gateway -> gateway (folder keyword, not the rule-2 promotion path)",
        kind_a.get("infra/gateway") is ModuleKind.GATEWAY, str(kind_a),
    )
    backend_group = next(g for g in result_a.groups if g.id == "backend/app")
    all_passed &= _check(
        "backend/app.apis still lists its real endpoint despite being classified data",
        backend_group.apis == ["GET /users"], str(backend_group.apis),
    )

    # --- 3b: rule-2 promotion — no rule-1 gateway anywhere in this second,
    # independent classify_modules() call, so the single busiest+thinnest
    # API group gets promoted to gateway; the others stay service. ---
    files_b = [
        *[_file(f"svc/thin/f{i}.py", "svc/thin", Language.PYTHON, 10) for i in range(5)],  # 5 tiny files, avg loc 10
        *[_file(f"svc/thick/f{i}.py", "svc/thick", Language.PYTHON, 200) for i in range(2)],  # 2 huge files, avg loc 200
        _file("svc/quiet/f0.py", "svc/quiet", Language.PYTHON, 30),  # no API endpoints at all
    ]
    apis_b = [
        *[_api("GET", f"/thin/{i}", "svc/thin") for i in range(5)],  # highest endpoint count (5), smallest avg size
        _api("GET", "/thick", "svc/thick"),
    ]
    result_b = classify_modules(files_b, apis_b)
    kind_b = {g.id: g.kind for g in result_b.groups}
    all_passed &= _check(
        "svc/thin (5 endpoints, avg loc 10) promoted to gateway",
        kind_b.get("svc/thin") is ModuleKind.GATEWAY, str(kind_b),
    )
    all_passed &= _check("svc/thick stays service (not the promoted one)", kind_b.get("svc/thick") is ModuleKind.SERVICE, str(kind_b))
    all_passed &= _check("svc/quiet stays service (default rule, zero endpoints)", kind_b.get("svc/quiet") is ModuleKind.SERVICE, str(kind_b))
    all_passed &= _check(
        "exactly one gateway group results from promotion", sum(1 for k in kind_b.values() if k is ModuleKind.GATEWAY) == 1, str(kind_b)
    )

    # --- 3c: rule-3 frontend (predominantly JS/TS/TSX + frontend keyword,
    # zero API endpoints) vs. a JS-heavy group that ISN'T under a
    # frontend-keyword path, which must fall through to the rule-4 default. ---
    files_c = [
        _file("apps/client/App.tsx", "apps/client", Language.TSX, 60),
        _file("apps/client/index.ts", "apps/client", Language.TYPESCRIPT, 15),
        _file("tools/scripts/build.ts", "tools/scripts", Language.TYPESCRIPT, 25),  # JS-heavy but no frontend keyword
    ]
    result_c = classify_modules(files_c, [])
    kind_c = {g.id: g.kind for g in result_c.groups}
    all_passed &= _check("apps/client -> frontend", kind_c.get("apps/client") is ModuleKind.FRONTEND, str(kind_c))
    all_passed &= _check(
        "tools/scripts -> service (JS-heavy but no frontend-keyword path segment)",
        kind_c.get("tools/scripts") is ModuleKind.SERVICE, str(kind_c),
    )

    # --- 3d: folding beyond MAX_MODULE_GROUPS — 30 singleton groups, all
    # defaulting to service, none large enough to differentiate by file
    # count -> ties broken by id, so g00..g23 are kept and g24..g29 fold
    # into one "other:service" node. ---
    files_d = [_file(f"g{i:02d}/mod/f.py", f"g{i:02d}/mod", Language.PYTHON, 10) for i in range(30)]
    result_d = classify_modules(files_d, [])
    ids_d = {g.id for g in result_d.groups}
    all_passed &= _check(
        f"30 singleton groups fold to {MAX_MODULE_GROUPS} kept + 1 other bucket",
        len(result_d.groups) == MAX_MODULE_GROUPS + 1, str(sorted(ids_d)),
    )
    all_passed &= _check("other:service is present", "other:service" in ids_d, str(sorted(ids_d)))
    other_group_d = next(g for g in result_d.groups if g.id == "other:service")
    all_passed &= _check("other:service folds exactly the 6 overflow groups' files", other_group_d.file_count == 6, str(other_group_d))
    all_passed &= _check(
        "kept groups are exactly g00..g23 (ties broken by id ascending)",
        ids_d - {"other:service"} == {f"g{i:02d}/mod" for i in range(24)}, str(sorted(ids_d)),
    )
    all_passed &= _check(
        "overflow files map to the folded id in file_group_id",
        all(result_d.file_group_id[f"g{i:02d}/mod/f.py"] == "other:service" for i in range(24, 30)),
    )
    all_passed &= _check(
        "kept files map to their own group id in file_group_id",
        all(result_d.file_group_id[f"g{i:02d}/mod/f.py"] == f"g{i:02d}/mod" for i in range(24)),
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
            "exactly the 4 real groups + the synthetic external node",
            node_ids == {"cyclic/module_a", "cyclic/module_b", "cyclic/module_c", "cyclic/orphan_module", "external"},
            str(node_ids),
        )
        kinds = {n["id"]: n["kind"] for n in body["nodes"]}
        all_passed &= _check(
            "the 4 real groups all default to service (no keyword/API/frontend signal in this fixture)",
            all(kinds[k] == "service" for k in ("cyclic/module_a", "cyclic/module_b", "cyclic/module_c", "cyclic/orphan_module")),
            str(kinds),
        )
        all_passed &= _check("external node's own kind is 'external'", kinds["external"] == "external", str(kinds))

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
        orphan_to_external = edges_by_pair.get(("cyclic/orphan_module", "external"))
        all_passed &= _check(
            "orphan_module's `import os` produces an edge into the synthetic external node",
            orphan_to_external == {"source": "cyclic/orphan_module", "target": "external", "weight": 1, "circular": False},
            str(orphan_to_external),
        )
        all_passed &= _check("exactly 4 edges total", len(body["edges"]) == 4, str(body["edges"]))
        return all_passed
    finally:
        db.close()


# --- group 6 (secondary): real dogfood run -----------------------------------------


def run_real_repo_dogfood() -> bool:
    print("\n=== group 6 (secondary): real dogfood run — this repo's own server/app + client/src ===")
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
        non_external_nodes = [n for n in body["nodes"] if n["id"] != "external"]
        all_passed &= _check(
            "sum of non-external node file counts == total persisted File rows",
            sum(n["files"] for n in non_external_nodes) == len(total_file_count),
            f"{sum(n['files'] for n in non_external_nodes)} vs {len(total_file_count)}",
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


# --- group 7 (non-gating): live bonus against Nirmit1705/HireSight ----------------


def run_live_bonus() -> bool:
    print(f"\n=== group 7 (non-gating): live GET /architecture against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
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
    r6 = run_real_repo_dogfood()
    r7 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. Not analyzed:                {'PASS' if r1 else 'FAIL'}")
    print(f"2. Ownership scoping:           {'PASS' if r2 else 'FAIL'}")
    print(f"3. classify_modules() unit:     {'PASS' if r3 else 'FAIL'}")
    print(f"4. load_dependency_edges:       {'PASS' if r4 else 'FAIL'}")
    print(f"5. Architecture (cyclic fixture): {'PASS' if r5 else 'FAIL'}")
    print(f"6. Real repo dogfood (secondary): {'PASS' if r6 else 'FAIL'}")
    print(f"7. Live bonus (non-gating):     {'PASS' if r7 else 'FAIL (see above)'}")
    return 0 if (r1 and r2 and r3 and r4 and r5 and r6) else 1


if __name__ == "__main__":
    sys.exit(main())
