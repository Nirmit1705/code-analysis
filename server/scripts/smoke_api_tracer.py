"""Manual smoke test for Phase 10.6's Flow Tracer REST surface
(`app/api/v1/tracer.py`): `GET /repos/{id}/tracer` (deterministic structure)
and `GET /repos/{id}/tracer/notes` (batched AI notes).

`fastapi.testclient.TestClient(app)` against one hand-inserted completed run
(same "insert directly, don't run the real pipeline" precedent as
`smoke_api_compare.py` — this script needs exact control over the import graph;
`smoke_layers.py`/`smoke_trace.py` already cover the pure engine). The LLM is
always a stub here, so nothing touches the real Groq API.

Seeded graph (all TypeScript), expected hops worked out by hand:

    routes/users.ts      (GET /users handler listUsers, POST /users <inline>)
        -> controllers/userController.ts, middleware/auth.ts
    middleware/auth.ts   (other) -> config/database.ts  (other, imports @prisma/client)
    controllers/userController.ts -> services/userService.ts   (+ express: generic, hidden)
    services/userService.ts -> repositories/userRepository.ts, repositories/auditRepository.ts,
                               config/database.ts, controllers/userController.ts (BACKWARDS),
                               __tests__/userService.test.ts (TEST)
    repositories/userRepository.ts -> models/user.ts
    repositories/auditRepository.ts   imports ioredis
    routes/health.ts     (GET /health, no imports)       lib/free.ts (other) -> services/userService.ts

Gating groups (offline, real DB):
1. `run_not_analyzed()` — no completed run (and a QUEUED-only run) -> `analyzed:false`
   on both endpoints, before any id is looked at.
2. `run_structure()` — the endpoint start (every hop, layer, parent, fn, via, external
   service incl. the shortest pass-through chain, symbols + cap, loc, layers_reached,
   reaches_data), the `<inline>` handler, a file start, the single-hop endpoint, and
   that the structure endpoint never calls the LLM.
3. `run_errors()` — 422 (neither/both/bad uuid), 404 (unknown ids, an id from a superseded
   run, another repository's id, another user's repository) on both endpoints.
4. `run_notes()` — notes happy path (prompt grounding: only traced facts), per-process cache,
   partial/garbled answers, every failure mode -> `notes: null` with 200 and nothing cached,
   both start kinds.

Group 5 (non-gating) checks HireSight's real traces against raw SQL edges.

Run: `uv run python scripts/smoke_api_tracer.py` (from `server/`).
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.ai.provider import LLMProviderError  # noqa: E402
from app.analysis.apis import Framework  # noqa: E402
from app.analysis.dependencies import DependencyEdgeType  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.symbols import SymbolKind  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.dependency import DependencyEdge as DependencyEdgeRow  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.symbol import Symbol  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import analysis_read_service, trace_narrative_service  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

# Same test-file heuristic the tracer excludes by (kept local: this check should not import the rule it checks).
_TEST_PATH = re.compile(r"(^|/)(__tests__|tests?|specs?|e2e)/|\.(test|spec)\.[^./]+$|(^|/)test_[^/]*\.py$|_test\.py$|Tests?\.java$", re.I)

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"


class _StubProvider:
    """A fake `LLMProvider`: returns `response` (a string, or a callable of the
    user message), or raises `error`; records every user message."""

    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[str] = []

    def complete(self, *, system: str, messages: list[dict], response_schema: dict | None = None) -> str:
        self.calls.append(messages[0]["content"])
        if self.error is not None:
            raise self.error
        return self.response(messages[0]["content"]) if callable(self.response) else self.response


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(subject=user.email)}"}


def _use_provider(provider) -> None:
    trace_narrative_service.GroqProvider = lambda: provider
    trace_narrative_service.clear_cache()


# --- seeding -----------------------------------------------------------------------


def _seed_run(
    db: Session,
    repository: Repository,
    *,
    commit_sha: str,
    status: AnalysisRunStatus = AnalysisRunStatus.COMPLETED,
    completed_ago_days: int = 0,
    files: dict[str, int],  # path -> loc
    edges: list[tuple[str, str]],
    external: list[tuple[str, str]],  # (source path, package)
    endpoints: list[tuple[str, str, str, str | None]],  # (method, path, file path, handler_name)
    symbols: dict[str, list[tuple[str, SymbolKind]]] | None = None,
) -> tuple[AnalysisRun, dict[str, File], dict[str, ApiEndpoint]]:
    run = AnalysisRun(
        repository_id=repository.id,
        branch="main",
        commit_sha=commit_sha,
        status=status,
        file_count=len(files),
        module_count=1,
        api_count=len(endpoints),
        dependency_count=len(edges),
        debt_score=0.0,
        database_provider="postgresql",
        completed_at=datetime.now(timezone.utc) - timedelta(days=completed_ago_days)
        if status is AnalysisRunStatus.COMPLETED
        else None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    file_by_path: dict[str, File] = {}
    for path, loc in files.items():
        row = File(
            repository_id=repository.id,
            analysis_run_id=run.id,
            path=path,
            language=Language.TYPESCRIPT,
            size_bytes=100,
            module=str(Path(path).parent).replace("\\", "/"),
            loc=loc,
            last_analyzed_commit_sha=commit_sha,
        )
        db.add(row)
        db.flush()
        file_by_path[path] = row

    for source, target in edges:
        db.add(DependencyEdgeRow(
            repository_id=repository.id, analysis_run_id=run.id,
            source_file_id=file_by_path[source].id, target_file_id=file_by_path[target].id,
            edge_type=DependencyEdgeType.INTERNAL,
        ))
    for source, package in external:
        db.add(DependencyEdgeRow(
            repository_id=repository.id, analysis_run_id=run.id,
            source_file_id=file_by_path[source].id, target_package_name=package,
            edge_type=DependencyEdgeType.EXTERNAL,
        ))

    endpoint_by_key: dict[str, ApiEndpoint] = {}
    for method, path, file_path, handler in endpoints:
        row = ApiEndpoint(
            repository_id=repository.id, analysis_run_id=run.id, method=method, path=path,
            file_id=file_by_path[file_path].id, module=file_by_path[file_path].module,
            framework=Framework.EXPRESS, handler_name=handler,
        )
        db.add(row)
        db.flush()
        endpoint_by_key[f"{method} {path}"] = row

    for file_path, items in (symbols or {}).items():
        for line, (name, kind) in enumerate(items, start=1):
            db.add(Symbol(
                repository_id=repository.id, file_id=file_by_path[file_path].id, analysis_run_id=run.id,
                name=name, kind=kind, line_start=line, line_end=line, module=file_by_path[file_path].module,
                language=Language.TYPESCRIPT,
            ))
    db.commit()
    db.refresh(run)
    return run, file_by_path, endpoint_by_key


FILES = {
    "routes/users.ts": 40,
    "routes/health.ts": 8,
    "controllers/userController.ts": 70,
    "services/userService.ts": 120,
    "repositories/userRepository.ts": 55,
    "repositories/auditRepository.ts": 30,
    "models/user.ts": 20,
    "config/database.ts": 12,
    "middleware/auth.ts": 25,
    "utils/log.ts": 9,
    "__tests__/userService.test.ts": 60,
    "lib/free.ts": 15,
}
EDGES = [
    ("routes/users.ts", "controllers/userController.ts"),
    ("routes/users.ts", "middleware/auth.ts"),
    ("middleware/auth.ts", "config/database.ts"),
    ("controllers/userController.ts", "services/userService.ts"),
    ("services/userService.ts", "repositories/userRepository.ts"),
    ("services/userService.ts", "repositories/auditRepository.ts"),
    ("services/userService.ts", "config/database.ts"),
    ("services/userService.ts", "controllers/userController.ts"),  # backwards
    ("services/userService.ts", "__tests__/userService.test.ts"),  # test file
    ("repositories/userRepository.ts", "models/user.ts"),
    ("lib/free.ts", "services/userService.ts"),
]
EXTERNAL = [
    ("config/database.ts", "@prisma/client"),
    ("controllers/userController.ts", "express"),  # generic: hidden
    ("repositories/auditRepository.ts", "ioredis"),
    ("utils/log.ts", "pino"),
]
ENDPOINTS = [
    ("GET", "/users", "routes/users.ts", "listUsers"),
    ("POST", "/users", "routes/users.ts", "<inline>"),
    ("GET", "/health", "routes/health.ts", "health"),
]
SYMBOLS = {
    "controllers/userController.ts": [("UserController", SymbolKind.CLASS), ("list", SymbolKind.METHOD)],
    "services/userService.ts": [(f"fn{i:02d}", SymbolKind.FUNCTION) for i in range(15)],
}


def _seed_main(db: Session, user: User, name: str):
    repository = _get_or_create_repository(db, user, github_repo_id=f"smoke-tracer-{name}", owner="smoke-api", name=f"tracer-{name}")
    _clear_previous_runs(db, repository)
    seeded = _seed_run(
        db, repository, commit_sha="t" * 40, files=FILES, edges=EDGES, external=EXTERNAL,
        endpoints=ENDPOINTS, symbols=SYMBOLS,
    )
    return repository, seeded


# --- group 1 -------------------------------------------------------------------------


def run_not_analyzed() -> bool:
    print("=== group 1 (gating): no completed run -> analyzed:false ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-tracer-none@local.test")
        repository = _get_or_create_repository(db, user, github_repo_id="smoke-tracer-none", owner="smoke-api", name="tracer-none")
        _clear_previous_runs(db, repository)
        client = TestClient(app)
        ok = True
        for label in ("no runs", "only a QUEUED run"):
            if label != "no runs":
                _seed_run(db, repository, commit_sha="q" * 40, status=AnalysisRunStatus.QUEUED, files={"a.ts": 1}, edges=[], external=[], endpoints=[])
            for path in ("tracer", "tracer/notes"):
                resp = client.get(f"/api/v1/repos/{repository.id}/{path}?file_id={uuid.uuid4()}", headers=_auth(user))
                body = resp.json()
                ok &= _check(
                    f"{label}: GET /{path} -> 200 analyzed:false, nothing else populated",
                    resp.status_code == 200 and body["analyzed"] is False and body["analysis_run_id"] is None
                    and body.get("hops", []) == [] and body.get("notes") is None,
                    f"{resp.status_code} {resp.text[:200]}",
                )
        resp = client.get(f"/api/v1/repos/{repository.id}/tracer", headers=_auth(user))
        ok &= _check("no start param is still 422 before the analyzed check", resp.status_code == 422, str(resp.status_code))
        return ok
    finally:
        db.close()


# --- group 2 -------------------------------------------------------------------------


def run_structure() -> bool:
    print("=== group 2 (gating): trace structure over HTTP ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-tracer-structure@local.test")
        repository, (run, files, endpoints) = _seed_main(db, user, "structure")
        client = TestClient(app)
        stub = _StubProvider(response=json.dumps({"notes": []}))
        _use_provider(stub)

        def trace(**params):
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return client.get(f"/api/v1/repos/{repository.id}/tracer?{qs}", headers=_auth(user))

        resp = trace(endpoint_id=endpoints["GET /users"].id)
        ok = _check("GET /users -> 200", resp.status_code == 200, resp.text[:300])
        if not ok:
            return False
        b = resp.json()
        ok &= _check(
            "envelope: analyzed + run + branch + commit",
            b["analyzed"] is True and b["analysis_run_id"] == str(run.id) and b["branch"] == "main" and b["commit_sha"] == "t" * 40,
        )
        s = b["start"]
        ok &= _check(
            "start: endpoint kind, label, file, method/path, endpoints_in_file=2",
            s["kind"] == "endpoint" and s["label"] == "GET /users" and s["file_path"] == "routes/users.ts"
            and s["method"] == "GET" and s["path"] == "/users" and s["endpoint_id"] == str(endpoints["GET /users"].id)
            and s["endpoints_in_file"] == 2,
            str(s),
        )
        hops = b["hops"]
        shape = [(h["index"], h["depth"], h["parent_index"], h["layer"], h["file_path"]) for h in hops]
        expected = [
            (0, 0, None, "route", "routes/users.ts"),
            (1, 1, 0, "controller", "controllers/userController.ts"),
            (2, 2, 1, "service", "services/userService.ts"),
            (3, 3, 2, "repository", "repositories/auditRepository.ts"),
            (4, 3, 2, "repository", "repositories/userRepository.ts"),
            (5, 4, 4, "model", "models/user.ts"),
        ]
        ok &= _check(
            "chain + branch: route -> controller -> service -> two repositories (path order) -> model under userRepository",
            shape == expected, str(shape),
        )
        ok &= _check(
            "the backwards service->controller import and the test file are not hops",
            "__tests__/userService.test.ts" not in [h["file_path"] for h in hops] and len(hops) == 6,
        )
        ok &= _check("start hop fn = the route's handler; every other hop fn null",
                     hops[0]["fn"] == "listUsers" and all(h["fn"] is None for h in hops[1:]), str([h["fn"] for h in hops]))
        ext0 = {e["service_id"]: e for e in hops[0]["external_services"]}
        ok &= _check(
            "pass-through: the route reaches PostgreSQL through middleware/auth.ts > config/database.ts (kind data)",
            list(ext0) == ["data:database"] and ext0["data:database"]["label"] == "PostgreSQL"
            and ext0["data:database"]["kind"] == "data" and ext0["data:database"]["packages"] == ["@prisma/client"]
            and ext0["data:database"]["via"] == ["middleware/auth.ts", "config/database.ts"],
            str(ext0),
        )
        ok &= _check("the controller's generic `express` import is hidden (no recognised service)", hops[1]["external_services"] == [])
        ext2 = hops[2]["external_services"]
        ok &= _check(
            "the service reaches PostgreSQL via config/database.ts only",
            len(ext2) == 1 and ext2[0]["label"] == "PostgreSQL" and ext2[0]["via"] == ["config/database.ts"], str(ext2),
        )
        ext3 = hops[3]["external_services"]
        ok &= _check(
            "a direct import has empty via (auditRepository -> Redis, kind cache)",
            len(ext3) == 1 and ext3[0]["label"] == "Redis" and ext3[0]["kind"] == "cache" and ext3[0]["via"] == [] and ext3[0]["packages"] == ["ioredis"],
            str(ext3),
        )
        ok &= _check(
            "deps = direct internal imports, test file excluded, backwards one included",
            hops[2]["deps"] == [
                "config/database.ts", "controllers/userController.ts",
                "repositories/auditRepository.ts", "repositories/userRepository.ts",
            ], str(hops[2]["deps"]),
        )
        ok &= _check("loc comes from the file row", [h["loc"] for h in hops] == [40, 70, 120, 30, 55, 20], str([h["loc"] for h in hops]))
        ok &= _check("symbols in source order for the controller", hops[1]["symbols"] == ["UserController", "list"] and hops[1]["symbol_count"] == 2, str(hops[1]["symbols"]))
        ok &= _check(
            "symbols capped at 12 with the real total in symbol_count",
            len(hops[2]["symbols"]) == 12 and hops[2]["symbol_count"] == 15 and hops[2]["symbols"][0] == "fn00",
            f"{len(hops[2]['symbols'])}/{hops[2]['symbol_count']}",
        )
        ok &= _check("a file with no symbols has none", hops[5]["symbols"] == [] and hops[5]["symbol_count"] == 0)
        ok &= _check(
            "summary: layers_reached outermost first, reaches_data, not truncated, caps reported",
            b["layers_reached"] == ["route", "controller", "service", "repository", "model"]
            and b["reaches_data"] is True and b["truncated"] is False and b["max_depth"] == 8 and b["max_hops"] == 25,
            str((b["layers_reached"], b["reaches_data"], b["truncated"], b["max_depth"], b["max_hops"])),
        )

        # The other endpoint in the same file: the identical file-grain trace, but no fn for <inline>.
        post = trace(endpoint_id=endpoints["POST /users"].id).json()
        ok &= _check(
            "POST /users (<inline> handler): same hops, fn null, label differs",
            [h["file_path"] for h in post["hops"]] == [h["file_path"] for h in hops]
            and post["hops"][0]["fn"] is None and post["start"]["label"] == "POST /users",
        )

        # Single-hop endpoint.
        health = trace(endpoint_id=endpoints["GET /health"].id).json()
        ok &= _check(
            "GET /health: a clean single route hop, no data",
            len(health["hops"]) == 1 and health["hops"][0]["fn"] == "health" and health["layers_reached"] == ["route"]
            and health["reaches_data"] is False and health["truncated"] is False and health["start"]["endpoints_in_file"] == 1
            and health["hops"][0]["deps"] == [] and health["hops"][0]["external_services"] == [],
            str(health["hops"]),
        )

        # File start.
        by_file = trace(file_id=files["services/userService.ts"].id).json()
        ok &= _check(
            "file start (a service): kind file, label = path, no endpoint fields, fn null, hops service -> repos -> model",
            by_file["start"]["kind"] == "file" and by_file["start"]["label"] == "services/userService.ts"
            and by_file["start"]["endpoint_id"] is None and by_file["start"]["method"] is None
            and by_file["hops"][0]["fn"] is None
            and [h["file_path"] for h in by_file["hops"]] == [
                "services/userService.ts", "repositories/auditRepository.ts", "repositories/userRepository.ts", "models/user.ts"],
            str([h["file_path"] for h in by_file["hops"]]),
        )
        other_start = trace(file_id=files["lib/free.ts"].id).json()
        ok &= _check(
            "file start on an `other` file reaches every layer below it",
            other_start["hops"][0]["layer"] == "other" and other_start["hops"][1]["file_path"] == "services/userService.ts"
            and other_start["layers_reached"] == ["service", "repository", "model", "other"],
            str((other_start["hops"][0]["layer"], other_start["layers_reached"])),
        )

        ok &= _check("the structure endpoint never called the LLM", stub.calls == [], str(len(stub.calls)))
        return ok
    finally:
        db.close()


# --- group 3 -------------------------------------------------------------------------


def run_errors() -> bool:
    print("=== group 3 (gating): 422 / 404 on both endpoints ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-tracer-errors@local.test")
        other = _get_or_create_user(db, "smoke-api-tracer-errors-other@local.test")
        repository = _get_or_create_repository(db, user, github_repo_id="smoke-tracer-errors", owner="smoke-api", name="tracer-errors")
        _clear_previous_runs(db, repository)
        old_run, old_files, old_eps = _seed_run(
            db, repository, commit_sha="o" * 40, completed_ago_days=2, files={"routes/old.ts": 5},
            edges=[], external=[], endpoints=[("GET", "/old", "routes/old.ts", "old")],
        )
        _run, files, eps = _seed_run(
            db, repository, commit_sha="n" * 40, files={"routes/new.ts": 5}, edges=[], external=[],
            endpoints=[("GET", "/new", "routes/new.ts", "fresh")],
        )
        other_repo = _get_or_create_repository(db, other, github_repo_id="smoke-tracer-errors-other", owner="smoke-api", name="tracer-errors-other")
        _clear_previous_runs(db, other_repo)
        _o_run, other_files, other_eps = _seed_run(
            db, other_repo, commit_sha="x" * 40, files={"routes/theirs.ts": 5}, edges=[], external=[],
            endpoints=[("GET", "/theirs", "routes/theirs.ts", "t")],
        )
        _use_provider(_StubProvider(response=json.dumps({"notes": []})))
        client = TestClient(app)
        ok = True
        ok &= _check("sanity: the new run is the latest", analysis_read_service.get_latest_completed_run(db, repository.id).id == _run.id)

        for path in ("tracer", "tracer/notes"):
            base = f"/api/v1/repos/{repository.id}/{path}"
            h = _auth(user)
            real_ep, real_file = eps["GET /new"].id, files["routes/new.ts"].id
            cases = [
                ("neither param -> 422", f"{base}", h, 422),
                ("both params -> 422", f"{base}?endpoint_id={real_ep}&file_id={real_file}", h, 422),
                ("malformed uuid -> 422", f"{base}?endpoint_id=not-a-uuid", h, 422),
                ("unknown endpoint id -> 404", f"{base}?endpoint_id={uuid.uuid4()}", h, 404),
                ("unknown file id -> 404", f"{base}?file_id={uuid.uuid4()}", h, 404),
                ("endpoint id from a superseded run -> 404", f"{base}?endpoint_id={old_eps['GET /old'].id}", h, 404),
                ("file id from a superseded run -> 404", f"{base}?file_id={old_files['routes/old.ts'].id}", h, 404),
                ("another repository's endpoint id -> 404", f"{base}?endpoint_id={other_eps['GET /theirs'].id}", h, 404),
                ("another repository's file id -> 404", f"{base}?file_id={other_files['routes/theirs.ts'].id}", h, 404),
                ("another user's repository id -> 404", f"/api/v1/repos/{other_repo.id}/{path}?endpoint_id={other_eps['GET /theirs'].id}", h, 404),
                ("unknown repository id -> 404", f"/api/v1/repos/{uuid.uuid4()}/{path}?endpoint_id={real_ep}", h, 404),
                ("no token -> 401", f"{base}?endpoint_id={real_ep}", {}, 401),
                ("valid endpoint -> 200", f"{base}?endpoint_id={real_ep}", h, 200),
                ("valid file -> 200", f"{base}?file_id={real_file}", h, 200),
            ]
            for label, url, headers, expected in cases:
                resp = client.get(url, headers=headers)
                ok &= _check(f"/{path}: {label}", resp.status_code == expected, f"got {resp.status_code} {resp.text[:120]}")
        return ok
    finally:
        db.close()


# --- group 4 -------------------------------------------------------------------------


def _notes_for(indices, text="Role sentence"):
    return json.dumps({"notes": [{"index": i, "note": f"{text} {i}."} for i in indices]})


def run_notes() -> bool:
    print("=== group 4 (gating): AI notes — grounding, cache, degradation ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-tracer-notes@local.test")
        repository, (run, files, endpoints) = _seed_main(db, user, "notes")
        client = TestClient(app)
        url = f"/api/v1/repos/{repository.id}/tracer/notes?endpoint_id={endpoints['GET /users'].id}"
        structure_url = f"/api/v1/repos/{repository.id}/tracer?endpoint_id={endpoints['GET /users'].id}"
        original = trace_narrative_service.GroqProvider
        ok = True
        try:
            # --- happy path + grounding
            stub = _StubProvider(response=_notes_for(range(6)))
            _use_provider(stub)
            resp = client.get(url, headers=_auth(user))
            body = resp.json()
            ok &= _check("happy path -> 200 analyzed:true with the run id", resp.status_code == 200 and body["analyzed"] is True and body["analysis_run_id"] == str(run.id), resp.text[:200])
            ok &= _check(
                "6 notes, one per hop, in index order, text passed through",
                [n["index"] for n in (body["notes"] or [])] == [0, 1, 2, 3, 4, 5] and body["notes"][2]["note"] == "Role sentence 2.",
                str(body["notes"]),
            )
            ok &= _check("exactly one model call for the whole trace", len(stub.calls) == 1, str(len(stub.calls)))
            prompt = stub.calls[0]
            traced = ["routes/users.ts", "controllers/userController.ts", "services/userService.ts",
                      "repositories/auditRepository.ts", "repositories/userRepository.ts", "models/user.ts"]
            ok &= _check("the prompt lists every traced file", all(p in prompt for p in traced))
            ok &= _check("it names the start and the route's handler", "GET /users" in prompt and "listUsers" in prompt)
            ok &= _check("it carries declared names and the layer labels", "UserController" in prompt and "layer label: repository" in prompt)
            ok &= _check(
                "it carries the pass-through chain for the external service",
                "PostgreSQL (via middleware/auth.ts > config/database.ts)" in prompt and "Redis" in prompt,
            )
            ok &= _check(
                "it shows only traced facts: not the test file, the generic `express`, other routes, or the unrelated file start",
                "userService.test.ts" not in prompt and "express" not in prompt.lower().replace("expresssion", "")
                and "routes/health.ts" not in prompt and "lib/free.ts" not in prompt and "pino" not in prompt,
            )
            ok &= _check("symbols are capped at 10 in the prompt (fn09 in, fn10 out)", "fn09" in prompt and "fn10" not in prompt)

            # --- cache
            again = client.get(url, headers=_auth(user)).json()
            ok &= _check("a repeat request is served from the cache: still 1 call, same notes", len(stub.calls) == 1 and again["notes"] == body["notes"], str(len(stub.calls)))
            ok &= _check("the structure endpoint still makes no model call", client.get(structure_url, headers=_auth(user)).status_code == 200 and len(stub.calls) == 1)

            # --- both start kinds
            file_url = f"/api/v1/repos/{repository.id}/tracer/notes?file_id={files['services/userService.ts'].id}"
            stub_file = _StubProvider(response=_notes_for(range(4)))
            _use_provider(stub_file)
            fb = client.get(file_url, headers=_auth(user)).json()
            ok &= _check("file start: 4 hops, 4 notes, prompt starts from the file path and has no handler line",
                         [n["index"] for n in fb["notes"]] == [0, 1, 2, 3] and "Trace start: services/userService.ts" in stub_file.calls[0]
                         and "handler" not in stub_file.calls[0].split("\n\n")[0], str(fb))

            # --- partial / garbled answers: per-entry salvage
            long_note = "x" * 400
            messy = json.dumps({"notes": [
                {"index": 0, "note": "Entry   point here."},   # narrow no-break space normalized
                {"index": 0, "note": "duplicate ignored"},
                {"index": 99, "note": "no such hop"},
                {"index": True, "note": "bool is not an index"},
                {"index": 1, "note": "   "},
                {"index": 2, "note": 7},
                {"index": 3, "note": long_note},
                "not an object",
                {"index": 5, "note": "Model file."},
            ]})
            _use_provider(_StubProvider(response=messy))
            nb = client.get(url, headers=_auth(user)).json()["notes"]
            ok &= _check("only valid entries survive, sorted by index", [n["index"] for n in nb] == [0, 3, 5], str(nb))
            ok &= _check("odd spaces (U+202F) become a normal space, first duplicate wins", nb[0]["note"] == "Entry   point here.", repr(nb[0]["note"]))
            ok &= _check("a long note is cut to 300 chars", len(nb[1]["note"]) == 300, str(len(nb[1]["note"])))

            # --- every failure mode degrades to notes:null + 200, and nothing is cached
            failures = {
                "provider error (LLMProviderError)": _StubProvider(error=LLMProviderError("boom")),
                "misconfigured provider (RuntimeError)": _StubProvider(error=RuntimeError("LLM_PROVIDER not supported")),
                "unexpected exception": _StubProvider(error=ValueError("weird")),
                "not JSON": _StubProvider(response="Sure! Here are your notes"),
                "JSON but not an object": _StubProvider(response="[1, 2]"),
                "no `notes` key": _StubProvider(response=json.dumps({"explanation": "x"})),
                "`notes` not a list": _StubProvider(response=json.dumps({"notes": "text"})),
                "empty notes list": _StubProvider(response=json.dumps({"notes": []})),
                "every index invalid": _StubProvider(response=_notes_for([50, 51])),
                "every note blank": _StubProvider(response=json.dumps({"notes": [{"index": 0, "note": " "}]})),
            }
            for label, provider in failures.items():
                _use_provider(provider)
                resp = client.get(url, headers=_auth(user))
                ok &= _check(
                    f"{label} -> 200 analyzed:true notes:null",
                    resp.status_code == 200 and resp.json()["analyzed"] is True and resp.json()["notes"] is None,
                    f"{resp.status_code} {resp.text[:120]}",
                )
                s = client.get(structure_url, headers=_auth(user))
                ok &= _check(f"{label}: the structure still loads", s.status_code == 200 and len(s.json()["hops"]) == 6)

            # failures were not cached: a healthy provider now gets called on the same trace.
            recovered = _StubProvider(response=_notes_for([0, 1]))
            trace_narrative_service.GroqProvider = lambda: recovered  # keep the cache the failures ran against
            rb = client.get(url, headers=_auth(user)).json()
            ok &= _check("after failures, a healthy provider is asked (nothing was cached) and its notes returned",
                         len(recovered.calls) == 1 and [n["index"] for n in rb["notes"]] == [0, 1], str(rb))
        finally:
            trace_narrative_service.GroqProvider = original
            trace_narrative_service.clear_cache()
        return ok
    finally:
        db.close()


# --- group 5 (non-gating) --------------------------------------------------------------


def run_live_bonus() -> bool:
    print("\n=== group 5 (non-gating): HireSight traces vs raw SQL edges ===")
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
        rows = db.execute(text(
            "select sf.path, tf.path, e.target_package_name, e.edge_type from dependency_edges e "
            "join files sf on sf.id = e.source_file_id left join files tf on tf.id = e.target_file_id "
            "where e.analysis_run_id = :r"), {"r": run.id}).all()
        internal = {(s, t) for s, t, _p, et in rows if et.upper() == "INTERNAL" and t}
        packages = {}
        for s, _t, p, et in rows:
            if et.upper() == "EXTERNAL" and p:
                packages.setdefault(s, set()).add(p)
        eps = db.execute(select(ApiEndpoint.id, ApiEndpoint.method, ApiEndpoint.path).where(ApiEndpoint.analysis_run_id == run.id)).all()
        client = TestClient(app)
        ok, bad, checked_hops = True, [], 0
        for ep_id, method, path in eps:
            resp = client.get(f"/api/v1/repos/{repository.id}/tracer?endpoint_id={ep_id}", headers=_auth(user))
            if resp.status_code != 200:
                bad.append(f"{method} {path}: HTTP {resp.status_code}")
                continue
            b = resp.json()
            hops = b["hops"]
            if hops[0]["file_path"] != b["start"]["file_path"] or hops[0]["parent_index"] is not None:
                bad.append(f"{method} {path}: wrong start hop")
            if len({h["file_path"] for h in hops}) != len(hops) or len(hops) > b["max_hops"]:
                bad.append(f"{method} {path}: duplicate hop or over the cap")
            for h in hops:
                checked_hops += 1
                if _TEST_PATH.search(h["file_path"]):
                    bad.append(f"{method} {path}: test file {h['file_path']} is a hop")
                if h["parent_index"] is not None:
                    chain = [hops[h["parent_index"]]["file_path"], *h["via"], h["file_path"]]
                    for a, c in zip(chain, chain[1:]):
                        if (a, c) not in internal:
                            bad.append(f"{method} {path}: no real import {a} -> {c}")
                for ext in h["external_services"]:
                    importer = ext["via"][-1] if ext["via"] else h["file_path"]
                    chain = [h["file_path"], *ext["via"]]
                    if any((a, c) not in internal for a, c in zip(chain, chain[1:])):
                        bad.append(f"{method} {path}: external via-chain for {ext['label']} is not real imports")
                    if not (set(ext["packages"]) & packages.get(importer, set())):
                        bad.append(f"{method} {path}: {importer} does not import {ext['packages']}")
                if set(h["deps"]) != {t for s, t in internal if s == h["file_path"] and "__tests__" not in t and ".test." not in t}:
                    bad.append(f"{method} {path}: deps of {h['file_path']} differ from raw edges")
        print(f"  info: {len(eps)} endpoints traced, {checked_hops} hops checked against raw SQL edges")
        ok &= _check("every hop's parent link, pass-through chain, external package and deps match the raw edges", not bad, "; ".join(bad[:5]))
        return ok
    finally:
        db.close()


def main() -> int:
    gating = [run_not_analyzed(), run_structure(), run_errors(), run_notes()]
    bonus = run_live_bonus()
    print("\n" + ("ALL GATING GROUPS PASS" if all(gating) else "SOME GATING GROUPS FAILED") + ("" if bonus else " (live bonus failed)"))
    return 0 if all(gating) else 1


if __name__ == "__main__":
    sys.exit(main())
