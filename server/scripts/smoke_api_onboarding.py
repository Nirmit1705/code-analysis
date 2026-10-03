"""Manual smoke test for Phase 10.10's onboarding path:
`services/onboarding_service.py` (pure ordering/cap/file picks + the DB path)
and `app/api/v1/onboarding.py` (`GET /repos/{id}/onboarding`,
`POST /repos/{id}/onboarding/progress`).

`fastapi.testclient.TestClient(app)` against hand-inserted completed runs (same
"insert directly, don't run the real pipeline" precedent as
`smoke_api_tracer.py`). No LLM is involved anywhere in this feature.

Seeded fixture (expected values worked out by hand in the comments below):

    server/routes/users.ts   GET /users, POST /users  -> controllers/userController.ts
    server/routes/health.ts  GET /health              -> lib/a.ts
    server/controllers/userController.ts -> services/userService.ts
    server/services/userService.ts       -> models/user.ts, lib/a.ts
    server/services/mailService.ts       -> utils/x.ts
    server/lib/a.ts   -> utils/x.ts      server/utils/x.ts -> lib/b.ts
        (so the *components* server/lib and server/utils import each other,
        though no file-level cycle exists)
    server/models/user.ts (+ schema.prisma, .keep)
    server/__tests__/user.test.ts -> services/userService.ts   (test: excluded)
    server/data/seed.json   (no source: excluded)
    web/main.tsx -> web/components/App.tsx -> web/api/client.ts, web/components/Button.tsx
    tools/build.ts  (imports nothing, imported by nothing)
    README.md, server/README.md

Gating groups (offline, real DB):
1. `run_pure()` — `order_path` (exact order, topological invariant, cycle grouping,
   isolated/data placement, determinism under shuffling), `cap_path`,
   `pick_read_first`, `pick_starting_points`.
2. `run_not_analyzed()` — `analyzed:false`; POST -> 409.
3. `run_structure()` — the fixture's full path (order, titles, kinds, counts,
   builds_on, in_cycle_with, depended_on_by, read_first) and starting points.
4. `run_progress()` — tick/untick, idempotency, per-user isolation, stale ids
   ignored, ticks surviving re-analysis (and returning with their component),
   errors (404/422).

Group 5 (non-gating) checks HireSight's real path against raw SQL.

Run: `uv run python scripts/smoke_api_onboarding.py` (from `server/`).
"""

from __future__ import annotations

import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.analysis.apis import Framework  # noqa: E402
from app.analysis.dependencies import DependencyEdgeType  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.dependency import DependencyEdge as DependencyEdgeRow  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.onboarding_progress import OnboardingProgress  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import analysis_read_service, architecture_service  # noqa: E402
from app.services.onboarding_service import (  # noqa: E402
    PathComponent,
    SourceFile,
    cap_path,
    order_path,
    pick_read_first,
    pick_starting_points,
)
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"

TS, TSX = Language.TYPESCRIPT, Language.TSX


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(subject=user.email)}"}


# --- seeding -----------------------------------------------------------------------

# path -> (loc, language or None, average complexity or None)
FILES: dict[str, tuple[int, Language | None, int | None]] = {
    "README.md": (40, None, None),
    "server/README.md": (25, None, None),
    "server/routes/users.ts": (60, TS, None),
    "server/routes/health.ts": (10, TS, None),
    "server/controllers/userController.ts": (80, TS, 2),
    "server/services/userService.ts": (120, TS, 6),
    "server/services/mailService.ts": (40, TS, 2),
    "server/models/user.ts": (20, TS, 1),
    "server/models/schema.prisma": (30, None, None),
    "server/models/.keep": (1, None, None),
    "server/lib/a.ts": (15, TS, 1),
    "server/lib/b.ts": (12, TS, 1),
    "server/utils/x.ts": (18, TS, 1),
    "server/__tests__/user.test.ts": (50, TS, 1),
    "server/data/seed.json": (300, None, None),
    "web/main.tsx": (12, TSX, 1),
    "web/components/App.tsx": (90, TSX, 1),
    "web/components/Button.tsx": (25, TSX, 4),
    "web/api/client.ts": (35, TS, 2),
    "tools/build.ts": (22, TS, 1),
}
EDGES = [
    ("server/routes/users.ts", "server/controllers/userController.ts"),
    ("server/routes/health.ts", "server/lib/a.ts"),
    ("server/controllers/userController.ts", "server/services/userService.ts"),
    ("server/services/userService.ts", "server/models/user.ts"),
    ("server/services/userService.ts", "server/lib/a.ts"),
    ("server/services/mailService.ts", "server/utils/x.ts"),
    ("server/lib/a.ts", "server/utils/x.ts"),
    ("server/utils/x.ts", "server/lib/b.ts"),
    ("server/__tests__/user.test.ts", "server/services/userService.ts"),
    ("web/main.tsx", "web/components/App.tsx"),
    ("web/components/App.tsx", "web/api/client.ts"),
    ("web/components/App.tsx", "web/components/Button.tsx"),
]
ENDPOINTS = [
    ("GET", "/users", "server/routes/users.ts"),
    ("POST", "/users", "server/routes/users.ts"),
    ("GET", "/health", "server/routes/health.ts"),
]

EXPECTED_ORDER = [
    "server/models",       # data, ready from the start
    "server/lib",          # the lib<->utils cycle group (importance 3, then 2)
    "server/utils",
    "server/services",
    "server/controllers",
    "server/routes",       # gateway after the service-kind components
    "web/api",             # frontend after the backend
    "web/components",
    "web",
    "tools",               # isolated: last
]


def _seed_run(
    db: Session,
    repository: Repository,
    *,
    commit_sha: str,
    completed_ago_minutes: int = 0,
    status: AnalysisRunStatus = AnalysisRunStatus.COMPLETED,
    drop: set[str] = frozenset(),
) -> AnalysisRun:
    files = {p: v for p, v in FILES.items() if p not in drop}
    edges = [(s, t) for s, t in EDGES if s in files and t in files]
    endpoints = [e for e in ENDPOINTS if e[2] in files]
    run = AnalysisRun(
        repository_id=repository.id, branch="main", commit_sha=commit_sha, status=status,
        file_count=len(files), module_count=1, api_count=len(endpoints), dependency_count=len(edges),
        debt_score=0.0, database_provider="postgresql",
        completed_at=(datetime.now(timezone.utc) - timedelta(minutes=completed_ago_minutes))
        if status is AnalysisRunStatus.COMPLETED else None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    rows: dict[str, File] = {}
    for path, (loc, language, complexity) in files.items():
        row = File(
            repository_id=repository.id, analysis_run_id=run.id, path=path, language=language,
            size_bytes=100, module=str(Path(path).parent).replace("\\", "/"), loc=loc,
            complexity_score=complexity, last_analyzed_commit_sha=commit_sha,
        )
        db.add(row)
        db.flush()
        rows[path] = row
    for source, target in edges:
        db.add(DependencyEdgeRow(
            repository_id=repository.id, analysis_run_id=run.id,
            source_file_id=rows[source].id, target_file_id=rows[target].id, edge_type=DependencyEdgeType.INTERNAL,
        ))
    for method, path, file_path in endpoints:
        db.add(ApiEndpoint(
            repository_id=repository.id, analysis_run_id=run.id, method=method, path=path,
            file_id=rows[file_path].id, module=rows[file_path].module, framework=Framework.EXPRESS,
        ))
    db.commit()
    return run


def _fresh_repository(db: Session, user: User, name: str) -> Repository:
    repository = _get_or_create_repository(db, user, github_repo_id=f"smoke-onboarding-{name}", owner="smoke-api", name=f"onboarding-{name}")
    _clear_previous_runs(db, repository)
    db.execute(delete(OnboardingProgress).where(OnboardingProgress.repository_id == repository.id))
    db.commit()
    return repository


# --- group 1: pure --------------------------------------------------------------------


def _pc(cid: str, kind: str, importance: int, size: int = 10) -> PathComponent:
    return PathComponent(id=cid, label=cid, kind=kind, importance=importance, size=size)


def run_pure() -> bool:
    print("=== group 1 (gating): pure ordering, cap and file picks ===")
    ok = True

    comps = [
        _pc("db", "data", 1), _pc("core", "service", 4), _pc("svc", "service", 1), _pc("api", "gateway", 2),
        _pc("ui", "frontend", 0), _pc("uikit", "frontend", 1), _pc("x", "service", 2), _pc("y", "service", 1),
        _pc("cfg", "service", 0), _pc("schema", "data", 0),
    ]
    deps = {
        "svc": {"core", "db", "x"}, "api": {"svc", "core"}, "ui": {"uikit", "api"},  # ui -> api: cross-stack import
        "x": {"y"}, "y": {"x", "core"}, "core": set(), "ghost-target": {"db"}, "uikit": {"nope"},
    }
    order = order_path(comps, deps)
    flat = [c for g in order for c in g]
    ok &= _check("every component exactly once (unknown ids ignored)", sorted(flat) == sorted(c.id for c in comps), str(flat))
    ok &= _check(
        "exact order: data foundations, core, cycle group, services, routes, frontend, isolated last",
        order == [["db"], ["schema"], ["core"], ["x", "y"], ["svc"], ["api"], ["uikit"], ["ui"], ["cfg"]],
        str(order),
    )
    pos = {c: i for i, c in enumerate(flat)}
    group_of = {c: i for i, g in enumerate(order) for c in g}
    violations = [
        (a, b) for a, targets in deps.items() for b in targets
        if a in pos and b in pos and group_of[a] != group_of[b] and pos[b] > pos[a]
    ]
    ok &= _check("topological: every import target comes before its importer (outside a cycle)", not violations, str(violations))
    shuffled = comps[:]
    random.Random(7).shuffle(shuffled)
    ok &= _check("deterministic under input shuffling", order_path(shuffled, deps) == order)
    ok &= _check("empty input -> empty path", order_path([], {}) == [])

    many = comps + [_pc("extra1", "service", 0, size=5), _pc("extra2", "service", 3, size=1)]
    capped = cap_path(order_path(many, deps), many, limit=10)
    capped_flat = [c for g in capped for c in g]
    ok &= _check(
        "cap keeps the 10 most important (importance, then size), in path order",
        sorted(capped_flat) == sorted(["db", "core", "svc", "api", "uikit", "x", "y", "extra2", "ui", "schema"])
        and capped_flat == [c for c in [x for g in order_path(many, deps) for x in g] if c in set(capped_flat)],
        str(capped_flat),
    )
    trimmed = cap_path([["a"], ["b", "c"], ["d"]], [_pc("a", "service", 5), _pc("b", "service", 0), _pc("c", "service", 4), _pc("d", "service", 3)], limit=3)
    ok &= _check("cap trims a cycle group to its survivors and drops emptied groups", trimmed == [["a"], ["c"], ["d"]], str(trimmed))
    ok &= _check("no cap when under the limit", cap_path(order, comps, limit=10) == order)

    files = [
        SourceFile("r/big.ts", loc=300, complexity=2, endpoints=0, importers=0),
        SourceFile("r/routes.ts", loc=40, complexity=None, endpoints=5, importers=0),
        SourceFile("r/second.ts", loc=30, complexity=None, endpoints=1, importers=2),
        SourceFile("r/shared.ts", loc=20, complexity=1, endpoints=0, importers=9),
        SourceFile("r/hairy.ts", loc=80, complexity=12, endpoints=0, importers=0),
        SourceFile("r/empty.ts", loc=0, complexity=None, endpoints=0, importers=0),
    ]
    picked = [(p.path, p.reason, p.count) for p in pick_read_first(files)]
    ok &= _check(
        "read-first interleaves endpoints / importers / complexity, dedupes, fills by size, caps at 5",
        picked == [
            ("r/routes.ts", "endpoints", 5), ("r/shared.ts", "imported_by", 9), ("r/hairy.ts", "complexity", 12),
            ("r/second.ts", "endpoints", 1), ("r/big.ts", "largest", 300),
        ],
        str(picked),
    )
    low = [SourceFile("a.ts", loc=10, complexity=2, endpoints=0, importers=0)]
    other = [SourceFile("schema.prisma", loc=50, complexity=None, endpoints=0, importers=0)]
    ok &= _check(
        "complexity below the bar is not called out; non-source files only fill by size",
        [(p.path, p.reason) for p in pick_read_first(low, other)] == [("schema.prisma", "largest"), ("a.ts", "largest")],
    )

    imports = {"main.ts": {"app.ts"}, "app.ts": {"a.ts", "b.ts"}, "a.ts": {"util.ts"}, "b.ts": {"util.ts"}, "lone.ts": {"util.ts"}}
    importers = {"app.ts": 1, "a.ts": 1, "b.ts": 1, "util.ts": 3}
    sources = {"main.ts", "app.ts", "a.ts", "b.ts", "util.ts", "lone.ts"}
    points = [(p.path, p.reason, p.count) for p in pick_starting_points(
        ["docs/README.md", "README.md", "pkg/sub/readme.txt", *sources], sources, imports, importers)]
    ok &= _check(
        "starting points: 2 shallowest READMEs, entry points with reach >= 3, most-imported >= 2",
        points == [
            ("README.md", "readme", None), ("docs/README.md", "readme", None),
            ("main.ts", "entry_point", 4), ("util.ts", "imported_by", 3),
        ],
        str(points),
    )
    return ok


# --- group 2 ----------------------------------------------------------------------------


def run_not_analyzed() -> bool:
    print("\n=== group 2 (gating): never analyzed ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-onboarding-a@example.com")
        repository = _fresh_repository(db, user, "empty")
        _seed_run(db, repository, commit_sha="q" * 40, status=AnalysisRunStatus.QUEUED)
        client = TestClient(app)
        ok = True
        resp = client.get(f"/api/v1/repos/{repository.id}/onboarding", headers=_auth(user))
        ok &= _check("GET -> 200 analyzed:false, nothing else", resp.status_code == 200 and resp.json() == {
            "analyzed": False, "analysis_run_id": None, "steps": [], "starting_points": [], "completed_count": 0, "total_components": 0,
        }, resp.text)
        resp = client.post(f"/api/v1/repos/{repository.id}/onboarding/progress", json={"step_id": "server/routes", "done": True}, headers=_auth(user))
        ok &= _check("POST -> 409 (no path yet)", resp.status_code == 409, resp.text)
        return ok
    finally:
        db.close()


# --- group 3 ----------------------------------------------------------------------------


def run_structure() -> bool:
    print("\n=== group 3 (gating): the fixture's path ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-onboarding-a@example.com")
        repository = _fresh_repository(db, user, "main")
        run = _seed_run(db, repository, commit_sha="a" * 40)
        body = TestClient(app).get(f"/api/v1/repos/{repository.id}/onboarding", headers=_auth(user)).json()
        steps = {s["id"]: s for s in body["steps"]}
        ok = True
        ok &= _check("analyzed, the latest run", body["analyzed"] and body["analysis_run_id"] == str(run.id))
        ok &= _check("order", [s["id"] for s in body["steps"]] == EXPECTED_ORDER, str([s["id"] for s in body["steps"]]))
        ok &= _check("test-only, JSON-only and README-only components are not steps; total_components = 10",
                     body["total_components"] == 10 and not {"server/__tests__", "server/data", ".", "server"} & set(steps))
        ok &= _check("titles and kinds come from the Architecture components", [
            (steps[i]["title"], steps[i]["kind"]) for i in ("server/routes", "server/controllers", "server/services", "server/models", "web/api", "tools")
        ] == [("API Routes", "gateway"), ("Controllers", "service"), ("Services", "service"), ("server/models", "data"), ("web/api", "frontend"), ("tools", "service")])
        ok &= _check("routes: 3 endpoints, sample apis", steps["server/routes"]["endpoint_count"] == 3
                     and steps["server/routes"]["sample_apis"] == ["GET /health", "GET /users", "POST /users"])
        ok &= _check("file_count/loc are the component's (incl. non-source files)",
                     (steps["server/models"]["file_count"], steps["server/models"]["loc"]) == (3, 51))
        ok &= _check("builds_on = earlier steps imported directly, in path order", {
            i: steps[i]["builds_on"] for i in ("server/services", "server/routes", "server/lib", "web", "tools")
        } == {
            "server/services": ["server/models", "server/lib", "server/utils"],
            "server/routes": ["server/lib", "server/controllers"],
            "server/lib": [], "web": ["web/components"], "tools": [],
        }, str({i: steps[i]["builds_on"] for i in ("server/services", "server/routes")}))
        ok &= _check("in_cycle_with pairs lib and utils, nobody else",
                     steps["server/lib"]["in_cycle_with"] == ["server/utils"] and steps["server/utils"]["in_cycle_with"] == ["server/lib"]
                     and all(not s["in_cycle_with"] for i, s in steps.items() if i not in ("server/lib", "server/utils")))
        ok &= _check("depended_on_by counts distinct components (the test importer does not count)", {
            i: steps[i]["depended_on_by"] for i in EXPECTED_ORDER
        } == {
            "server/models": 1, "server/lib": 3, "server/utils": 2, "server/services": 1, "server/controllers": 1,
            "server/routes": 0, "web/api": 1, "web/components": 1, "web": 0, "tools": 0,
        }, str({i: steps[i]["depended_on_by"] for i in EXPECTED_ORDER}))
        rf = {i: [(r["path"], r["reason"], r["count"]) for r in steps[i]["read_first"]] for i in steps}
        ok &= _check("read_first: routes by endpoints", rf["server/routes"] == [
            ("server/routes/users.ts", "endpoints", 2), ("server/routes/health.ts", "endpoints", 1)], str(rf["server/routes"]))
        ok &= _check("read_first: services (importer, then size; the test importer not counted)", rf["server/services"] == [
            ("server/services/userService.ts", "imported_by", 1), ("server/services/mailService.ts", "largest", 40)], str(rf["server/services"]))
        ok &= _check("read_first: models includes schema.prisma, never the dotfile", rf["server/models"] == [
            ("server/models/user.ts", "imported_by", 1), ("server/models/schema.prisma", "largest", 30)], str(rf["server/models"]))
        ok &= _check("read_first: complexity is called out on a file not already picked", rf["web/components"] == [
            ("web/components/App.tsx", "imported_by", 1), ("web/components/Button.tsx", "complexity", 4)], str(rf["web/components"]))
        ok &= _check("nothing done yet", body["completed_count"] == 0 and all(not s["done"] and s["completed_at"] is None for s in body["steps"]))
        sp = [(p["path"], p["reason"], p["count"]) for p in body["starting_points"]]
        ok &= _check("starting points", sp == [
            ("README.md", "readme", None), ("server/README.md", "readme", None),
            ("server/routes/users.ts", "entry_point", 6), ("server/routes/health.ts", "entry_point", 3),
            ("server/lib/a.ts", "imported_by", 2), ("server/utils/x.ts", "imported_by", 2),
        ], str(sp))
        return ok
    finally:
        db.close()


# --- group 4 ----------------------------------------------------------------------------


def run_progress() -> bool:
    print("\n=== group 4 (gating): progress ===")
    db = SessionLocal()
    try:
        user_a = _get_or_create_user(db, "smoke-onboarding-a@example.com")
        user_b = _get_or_create_user(db, "smoke-onboarding-b@example.com")
        repository = _fresh_repository(db, user_a, "progress")
        _seed_run(db, repository, commit_sha="a" * 40, completed_ago_minutes=30)
        repo_b = _fresh_repository(db, user_b, "progress")  # B's own copy of the same GitHub repository
        _seed_run(db, repo_b, commit_sha="a" * 40, completed_ago_minutes=30)
        client = TestClient(app)
        base = f"/api/v1/repos/{repository.id}/onboarding"

        def tick(step_id: str, done: bool = True, user: User = user_a, repo: Repository = repository):
            return client.post(f"/api/v1/repos/{repo.id}/onboarding/progress", json={"step_id": step_id, "done": done}, headers=_auth(user))

        def get(user: User = user_a, repo: Repository = repository) -> dict:
            return client.get(f"/api/v1/repos/{repo.id}/onboarding", headers=_auth(user)).json()

        ok = True
        r1 = tick("server/models")
        ok &= _check("tick -> 200 done with a timestamp, counts", r1.status_code == 200 and r1.json()["done"] is True
                     and r1.json()["completed_at"] is not None and r1.json()["completed_count"] == 1 and r1.json()["step_count"] == 10, r1.text)
        r2 = tick("server/models")
        ok &= _check("re-tick is idempotent (same timestamp, one row)", r2.status_code == 200 and r2.json()["completed_at"] == r1.json()["completed_at"]
                     and db.execute(select(OnboardingProgress).where(OnboardingProgress.repository_id == repository.id)).scalars().all().__len__() == 1)
        tick("tools")
        body = get()
        ok &= _check("GET reflects ticks", body["completed_count"] == 2 and [s["id"] for s in body["steps"] if s["done"]] == ["server/models", "tools"])
        r3 = tick("tools", done=False)
        ok &= _check("untick -> done:false, count drops", r3.status_code == 200 and r3.json() == {
            "step_id": "tools", "done": False, "completed_at": None, "completed_count": 1, "step_count": 10}, r3.text)
        ok &= _check("untick again is idempotent", tick("tools", done=False).status_code == 200)

        body_b = get(user_b, repo_b)
        ok &= _check("user B sees none of A's ticks on their own copy", body_b["analyzed"] and body_b["completed_count"] == 0)
        ok &= _check("user B cannot read or tick A's repository (404)",
                     client.get(base, headers=_auth(user_b)).status_code == 404 and tick("server/models", user=user_b).status_code == 404)
        db.add(OnboardingProgress(user_id=user_b.id, repository_id=repository.id, step_id="server/routes"))
        db.commit()
        ok &= _check("a row for another user on A's repository is not counted for A", get()["completed_count"] == 1)

        db.add(OnboardingProgress(user_id=user_a.id, repository_id=repository.id, step_id="gone/component"))
        db.commit()
        body = get()
        ok &= _check("a stale step id is ignored (not counted, not listed)",
                     body["completed_count"] == 1 and "gone/component" not in {s["id"] for s in body["steps"]})
        ok &= _check("ticking a step not in the path -> 404", tick("gone/component").status_code == 404 and tick("server/__tests__").status_code == 404)
        bad = client.post(f"{base}/progress", json={"step_id": "tools"}, headers=_auth(user_a))
        ok &= _check("missing `done` -> 422", bad.status_code == 422)
        ok &= _check("unknown repository -> 404", client.get(f"/api/v1/repos/{uuid.uuid4()}/onboarding", headers=_auth(user_a)).status_code == 404)

        tick("tools")
        _seed_run(db, repository, commit_sha="b" * 40, completed_ago_minutes=20)
        body = get()
        ok &= _check("re-analysis keeps ticks", body["completed_count"] == 2 and {s["id"] for s in body["steps"] if s["done"]} == {"server/models", "tools"})
        _seed_run(db, repository, commit_sha="c" * 40, completed_ago_minutes=10, drop={"tools/build.ts"})
        body = get()
        ok &= _check("a run without the component: its tick is ignored, the row is kept",
                     body["completed_count"] == 1 and "tools" not in {s["id"] for s in body["steps"]}
                     and db.execute(select(OnboardingProgress).where(OnboardingProgress.user_id == user_a.id, OnboardingProgress.step_id == "tools")).scalar_one_or_none() is not None)
        _seed_run(db, repository, commit_sha="d" * 40, completed_ago_minutes=0)
        body = get()
        ok &= _check("the tick comes back with the component", body["completed_count"] == 2 and next(s for s in body["steps"] if s["id"] == "tools")["done"])
        return ok
    finally:
        db.close()


# --- group 5 (non-gating) --------------------------------------------------------------


def run_live_bonus() -> bool:
    print("\n=== group 5 (non-gating): HireSight's path vs raw SQL ===")
    db = SessionLocal()
    try:
        best = None
        for r in db.execute(select(Repository).where(Repository.owner == LIVE_TEST_OWNER, Repository.name == LIVE_TEST_REPO)).scalars().all():
            run = analysis_read_service.get_latest_completed_run(db, r.id)
            if run is not None and (best is None or (run.completed_at or run.created_at) > (best[1].completed_at or best[1].created_at)):
                best = (r, run)
        if best is None:
            print("  [SKIP] no HireSight repository with a completed run in this database")
            return True
        repository, run = best
        user = db.get(User, repository.connected_user_id)
        body = TestClient(app).get(f"/api/v1/repos/{repository.id}/onboarding", headers=_auth(user)).json()
        built = architecture_service.build_architecture(db, repository)
        group_of = built.classification.file_group_id
        node_ids = {n.id for n in built.nodes if n.files > 0}
        paths = {p for (p,) in db.execute(text("select path from files where analysis_run_id = :r"), {"r": run.id})}
        pairs = {(s, t) for s, t in db.execute(text(
            "select sf.path, tf.path from dependency_edges e join files sf on sf.id = e.source_file_id "
            "join files tf on tf.id = e.target_file_id where e.analysis_run_id = :r"), {"r": run.id})}
        bad = []
        for step in body["steps"]:
            if step["id"] not in node_ids:
                bad.append(f"{step['id']} is not an Architecture component")
            for rf in step["read_first"]:
                if rf["path"] not in paths or group_of.get(rf["path"]) != step["id"]:
                    bad.append(f"{rf['path']} is not a file of {step['id']}")
                if rf["reason"] == "imported_by":
                    real = len({s for s, t in pairs if t == rf["path"] and "__tests__" not in s and ".test." not in s})
                    if real != rf["count"]:
                        bad.append(f"{rf['path']} importers {rf['count']} != SQL {real}")
            for dep in step["builds_on"]:
                if not any(group_of.get(s) == step["id"] and group_of.get(t) == dep for s, t in pairs):
                    bad.append(f"{step['id']} -> {dep} has no real import")
        for p in body["starting_points"]:
            if p["path"] not in paths:
                bad.append(f"starting point {p['path']} not in the run")
        print(f"  info: {len(body['steps'])} steps of {body['total_components']} components, {len(body['starting_points'])} starting points: "
              + " -> ".join(s["title"] for s in body["steps"]))
        return _check("every step, file, import count and prerequisite matches the raw data", not bad, "; ".join(bad[:5]))
    finally:
        db.close()


def main() -> int:
    gating = [run_pure(), run_not_analyzed(), run_structure(), run_progress()]
    bonus = run_live_bonus()
    print("\n" + ("ALL GATING GROUPS PASS" if all(gating) else "SOME GATING GROUPS FAILED") + ("" if bonus else " (live bonus failed)"))
    return 0 if all(gating) else 1


if __name__ == "__main__":
    sys.exit(main())
