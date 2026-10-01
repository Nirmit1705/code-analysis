"""Manual smoke test for Phase 10.1's API Reference surface:
`GET /repos/{id}/apis` (`app/api/v1/api_reference.py`),
`app/services/api_reference_service.py::load_api_endpoints`, and the new
`ApiEndpoint.handler_name`/`handler_line_start` columns written by
`repo_service.persist_analysis_run`.

Same `_check`/PASS-FAIL/exit-code template as the other `smoke_api_*.py`
scripts; seeding helpers imported from `smoke_storage` like
`smoke_api_compare.py` does.

Five gating groups (real DB, no Celery/LLM):

1. `run_not_analyzed()` — no completed run -> `{analyzed: false, endpoints: []}`.
2. `run_ownership_404()` — a second user's token -> 404.
3. `run_seeded_run()` — hand-inserted rows covering every handler shape:
   a resolved handler (Symbol link + new columns), an inline handler
   (`<inline>`, no Symbol), an *old-run* row (new columns NULL, Symbol link
   only -> falls back to the Symbol), a row with neither (-> `handler: null`),
   and two methods on one path (distinct ids, both present). Also the
   service label (a file owning an endpoint is "API Routes"), the Python
   sort order, a handler whose Symbol lives in a different file than the
   route, and that a *different* run's endpoints never leak in.
4. `run_persist_roundtrip()` — a real fixture through the real pipeline +
   `persist_analysis_run`: every persisted row's `handler_name`/
   `handler_line_start` equals what the pure `ApiRoute` carried, and the
   endpoint's count equals `COUNT(*)` for that run.
5. `run_live_bonus()` (non-gating) — `Nirmit1705/HireSight`'s latest run: the
   endpoint count equals psql's `COUNT(*)`. Skips if absent.

Run: `uv run python scripts/smoke_api_apis.py` (from `server/`).
"""

from __future__ import annotations

import base64
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.analysis.apis import Framework  # noqa: E402
from app.analysis.debt import DebtIssueType, DebtSeverity  # noqa: E402
from app.analysis.dependencies import DependencyEdgeType  # noqa: E402
from app.analysis.ingestion import IngestedFile, IngestionResult  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.pipeline import analyze_ingested  # noqa: E402
from app.analysis.symbols import SymbolKind  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus, AnalysisRunTriggerSource  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.debt_issue import DebtIssue  # noqa: E402
from app.models.dependency import DependencyEdge as DependencyEdgeRow  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.symbol import Symbol as SymbolRow  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import analysis_read_service, api_reference_service, github_service  # noqa: E402
from app.services.github_auth_service import GitHubAuthError  # noqa: E402
from app.services.repo_service import (  # noqa: E402
    create_queued_analysis_run,
    finalize_analysis_run_completed,
    persist_analysis_run,
)
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_file(db: Session, repository: Repository, run: AnalysisRun, path: str) -> File:
    file_row = File(
        repository_id=repository.id,
        analysis_run_id=run.id,
        path=path,
        language=Language.PYTHON,
        size_bytes=100,
        module=str(Path(path).parent).replace("\\", "/"),
        loc=10,
        last_analyzed_commit_sha=run.commit_sha,
    )
    db.add(file_row)
    db.flush()
    return file_row


def _add_symbol(db: Session, repository: Repository, run: AnalysisRun, file_row: File, name: str, a: int, b: int) -> SymbolRow:
    symbol = SymbolRow(
        repository_id=repository.id,
        file_id=file_row.id,
        analysis_run_id=run.id,
        name=name,
        kind=SymbolKind.FUNCTION,
        line_start=a,
        line_end=b,
        module=file_row.module,
        language=Language.PYTHON,
    )
    db.add(symbol)
    db.flush()
    return symbol


def _new_completed_run(db: Session, repository: Repository, commit_sha: str) -> AnalysisRun:
    run = AnalysisRun(
        repository_id=repository.id, branch="main", commit_sha=commit_sha, status=AnalysisRunStatus.COMPLETED
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# --- group 1/2 ----------------------------------------------------------------------


def run_not_analyzed() -> bool:
    print("=== group 1: repository with no completed run -> analyzed: false ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-apis-none@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-apis-none", owner="smoke-api", name="apis-none"
        )
        _clear_previous_runs(db, repository)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/apis", headers=_auth(create_access_token(subject=user.email)))
        ok = _check("GET -> 200", resp.status_code == 200, resp.text)
        body = resp.json()
        ok &= _check("analyzed == false", body.get("analyzed") is False, str(body))
        ok &= _check("endpoints == []", body.get("endpoints") == [], str(body))
        return ok
    finally:
        db.close()


def run_ownership_404() -> bool:
    print("\n=== group 2: a second user's token -> 404 ===")
    db = SessionLocal()
    try:
        owner = _get_or_create_user(db, "smoke-api-apis-owner@local.test")
        other = _get_or_create_user(db, "smoke-api-apis-other@local.test")
        repository = _get_or_create_repository(
            db, owner, github_repo_id="smoke-api-apis-owner", owner="smoke-api", name="apis-owner"
        )
        _clear_previous_runs(db, repository)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/apis", headers=_auth(create_access_token(subject=other.email)))
        ok = _check("other user's GET -> 404", resp.status_code == 404, resp.text)
        resp = client.get(f"/api/v1/repos/{repository.id}/apis")
        ok &= _check("no token -> 401/403", resp.status_code in (401, 403), str(resp.status_code))
        return ok
    finally:
        db.close()


# --- group 3: seeded handler shapes --------------------------------------------------


def run_seeded_run() -> bool:
    print("\n=== group 3: every handler shape, service label, sorting, run scoping ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-apis-seeded@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-apis-seeded", owner="smoke-api", name="apis-seeded"
        )
        _clear_previous_runs(db, repository)

        # A superseded run whose endpoint must never leak into the latest run's response.
        old_run = _new_completed_run(db, repository, "1" * 40)
        old_file = _add_file(db, repository, old_run, "app/routes/legacy.py")
        db.add(ApiEndpoint(
            repository_id=repository.id, analysis_run_id=old_run.id, method="GET", path="/legacy",
            file_id=old_file.id, module=old_file.module, framework=Framework.FASTAPI,
        ))
        db.commit()

        run = _new_completed_run(db, repository, "2" * 40)
        routes_file = _add_file(db, repository, run, "app/routes/users.py")
        handlers_file = _add_file(db, repository, run, "app/handlers/user_handlers.py")
        s_resolved = _add_symbol(db, repository, run, routes_file, "list_users", 10, 18)
        s_cross_file = _add_symbol(db, repository, run, handlers_file, "create_user", 40, 55)
        s_old = _add_symbol(db, repository, run, routes_file, "get_user", 22, 30)

        def endpoint(method: str, path: str, **kw) -> ApiEndpoint:
            row = ApiEndpoint(
                repository_id=repository.id, analysis_run_id=run.id, method=method, path=path,
                file_id=routes_file.id, module=routes_file.module, framework=Framework.FASTAPI, **kw,
            )
            db.add(row)
            return row

        # Resolved handler, new columns populated.
        e_list = endpoint("GET", "/users", handler_symbol_id=s_resolved.id, handler_name="list_users", handler_line_start=9)
        # Same path, a second method -> must stay a distinct row.
        e_post = endpoint("POST", "/users", handler_symbol_id=s_cross_file.id, handler_name="create_user", handler_line_start=20)
        # Inline handler: name only, no Symbol.
        e_inline = endpoint("DELETE", "/users/{id}", handler_name="<inline>", handler_line_start=33)
        # Old-run shape: new columns NULL, Symbol link only.
        e_old = endpoint("GET", "/users/{id}", handler_symbol_id=s_old.id)
        # Neither a Symbol nor a name.
        e_bare = endpoint("PUT", "/ping")
        db.commit()
        for e in (e_list, e_post, e_inline, e_old, e_bare):
            db.refresh(e)

        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/apis", headers=_auth(create_access_token(subject=user.email)))
        ok = _check("GET -> 200", resp.status_code == 200, resp.text)
        if not ok:
            return False
        body = resp.json()
        ok &= _check("analyzed + analysis_run_id == latest run", body["analyzed"] is True and body["analysis_run_id"] == str(run.id), str(body)[:200])
        eps = body["endpoints"]
        ok &= _check("5 endpoints, none from the superseded run", len(eps) == 5 and all(e["path"] != "/legacy" for e in eps), str([(e["method"], e["path"]) for e in eps]))
        ok &= _check("ids are unique", len({e["id"] for e in eps}) == 5)
        ok &= _check(
            "sorted by (path, method)",
            [(e["path"], e["method"]) for e in eps]
            == [("/ping", "PUT"), ("/users", "GET"), ("/users", "POST"), ("/users/{id}", "DELETE"), ("/users/{id}", "GET")],
            str([(e["path"], e["method"]) for e in eps]),
        )
        by_id = {e["id"]: e for e in eps}

        got = by_id[str(e_list.id)]
        ok &= _check(
            "resolved handler: Symbol name/file/lines, route declaration line from the new column",
            got["handler"] == {"name": "list_users", "resolved": True, "file_path": "app/routes/users.py", "line_start": 10, "line_end": 18}
            and got["line"] == 9 and got["file_path"] == "app/routes/users.py" and got["framework"] == "fastapi",
            str(got),
        )
        ok &= _check("service label for a file owning an endpoint == 'API Routes'", got["service"] == "API Routes", got["service"])

        got = by_id[str(e_post.id)]
        ok &= _check(
            "handler whose Symbol is in a different file -> that file's path",
            got["handler"]["file_path"] == "app/handlers/user_handlers.py" and got["handler"]["line_start"] == 40 and got["file_path"] == "app/routes/users.py",
            str(got),
        )

        got = by_id[str(e_inline.id)]
        ok &= _check(
            "inline handler: name only, resolved=false, no fabricated location",
            got["handler"] == {"name": "<inline>", "resolved": False, "file_path": None, "line_start": None, "line_end": None} and got["line"] == 33,
            str(got),
        )

        got = by_id[str(e_old.id)]
        ok &= _check(
            "old-run row (new columns NULL) falls back to the Symbol row; line is null",
            got["handler"]["name"] == "get_user" and got["handler"]["resolved"] is True and got["handler"]["line_start"] == 22 and got["line"] is None,
            str(got),
        )

        got = by_id[str(e_bare.id)]
        ok &= _check("no Symbol and no name -> handler is null", got["handler"] is None, str(got))
        return ok
    finally:
        db.close()


# --- group 4: persist round trip -----------------------------------------------------


def run_persist_roundtrip() -> bool:
    print("\n=== group 4: real pipeline + persist_analysis_run writes handler_name/handler_line_start ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-apis-persist@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-apis-persist", owner="smoke-api", name="apis-persist"
        )
        _clear_previous_runs(db, repository)

        root = FIXTURES_DIR / "fastapi_flask"
        files = [
            IngestedFile(path=p.relative_to(root).as_posix(), absolute_path=p, size_bytes=p.stat().st_size)
            for p in sorted(root.rglob("*.py"))
        ]
        commit_sha = "persist".ljust(40, "0")[:40]
        result = analyze_ingested(IngestionResult(root_dir=root, files=files, ignored_paths=[]), commit_sha=commit_sha)
        ok = _check("fixture yields routes to persist", len(result.api_routes) > 0, str(len(result.api_routes)))

        queued = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha, trigger_source=AnalysisRunTriggerSource.MANUAL)
        run, _f, _s = persist_analysis_run(db, analysis_run=queued, analysis_result=result)
        run = finalize_analysis_run_completed(db, analysis_run=run, repository=repository)

        rows = db.execute(select(ApiEndpoint).where(ApiEndpoint.analysis_run_id == run.id)).scalars().all()
        ok &= _check("row count == len(api_routes)", len(rows) == len(result.api_routes), f"{len(rows)} vs {len(result.api_routes)}")
        expected = sorted((r.method, r.path, r.handler_name, r.line_start) for r in result.api_routes)
        actual = sorted((r.method, r.path, r.handler_name, r.handler_line_start) for r in rows)
        ok &= _check("every row's handler_name/handler_line_start equals the pure ApiRoute's", expected == actual, f"{expected[:3]} vs {actual[:3]}")

        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/apis", headers=_auth(create_access_token(subject=user.email)))
        ok &= _check("GET -> 200 with the same count", resp.status_code == 200 and len(resp.json()["endpoints"]) == len(rows), resp.text[:200])
        resolved = [r for r in result.api_routes if r.handler_symbol is not None]
        got_resolved = [e for e in resp.json()["endpoints"] if e["handler"] and e["handler"]["resolved"]]
        ok &= _check("resolved-handler count over HTTP == pure routes with a handler_symbol", len(got_resolved) == len(resolved), f"{len(got_resolved)} vs {len(resolved)}")
        return ok
    finally:
        db.close()


# --- group 5: pure helpers -------------------------------------------------------------


def run_pure_helpers() -> bool:
    print("\n=== group 5: path parameters + resource groups (pure) ===")
    params = api_reference_service.extract_path_params
    names = lambda p: [(x.name, x.type) for x in params(p)]  # noqa: E731
    ok = _check("express :id params, in order", names("/api/interviews/:interviewId/questions/:questionIndex") == [("interviewId", None), ("questionIndex", None)])
    ok &= _check("fastapi/spring {id} and {id:path}", names("/items/{item_id}/files/{rest:path}") == [("item_id", None), ("rest", None)])
    ok &= _check("flask <int:id> keeps its converter, <slug> has none", names("/u/<int:id>/p/<slug>") == [("id", "int"), ("slug", None)])
    ok &= _check("no params -> []; a repeated name appears once", names("/health") == [] and names("/a/:x/b/:x") == [("x", None)])
    ok &= _check("a scheme-like colon is not a param", names("/redirect/http://x") == [])
    g = api_reference_service.resource_group
    cases = {
        "/api/auth/google/callback": "/api/auth", "/api/v1/items/:id": "/api/v1/items", "/health": "/health",
        "/": "/", "/:id": "/", "/api/ai-interview/session/:sessionId": "/api/ai-interview", "/api": "/api", "/v2/users": "/v2/users",
    }
    bad = {k: g(k) for k, v in cases.items() if g(k) != v}
    ok &= _check("resource_group over 8 shapes", not bad, str(bad))
    return ok


# --- group 6: list extras (group/params/middleware/baseline) ----------------------------


def run_list_extras() -> bool:
    print("\n=== group 6: middleware passthrough, baseline skips a same-commit re-analysis, new/removed ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-apis-extras@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-apis-extras", owner="smoke-api", name="apis-extras"
        )
        _clear_previous_runs(db, repository)
        t0 = datetime.now(timezone.utc) - timedelta(hours=3)

        def make_run(commit: str, minutes: int, endpoints: list[tuple[str, str, list[str] | None]]) -> AnalysisRun:
            run = AnalysisRun(
                repository_id=repository.id, branch="main", commit_sha=commit * 40, status=AnalysisRunStatus.COMPLETED,
                completed_at=t0 + timedelta(minutes=minutes),
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            f = _add_file(db, repository, run, "app/routes/r.py")
            for method, path, mw in endpoints:
                db.add(ApiEndpoint(
                    repository_id=repository.id, analysis_run_id=run.id, method=method, path=path,
                    file_id=f.id, module=f.module, framework=Framework.EXPRESS, middleware=mw,
                ))
            db.commit()
            return run

        make_run("1", 0, [("GET", "/keep", None), ("GET", "/gone", None)])
        make_run("2", 10, [("GET", "/keep", None), ("POST", "/fresh", ["authenticateToken", "rateLimit"])])
        latest = make_run("2", 20, [("GET", "/keep", []), ("POST", "/fresh", ["authenticateToken", "rateLimit"])])

        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/apis", headers=_auth(create_access_token(subject=user.email)))
        ok = _check("GET -> 200", resp.status_code == 200, resp.text)
        if not ok:
            return False
        body = resp.json()
        ok &= _check("latest run + branch/commit echoed", body["analysis_run_id"] == str(latest.id) and body["branch"] == "main" and body["commit_sha"] == "2" * 40)
        ok &= _check(
            "baseline is the commit-1 run (the same-commit re-analysis is skipped)",
            body["baseline"] is not None and body["baseline"]["commit_sha"] == "1" * 40,
            str(body["baseline"]),
        )
        by_path = {e["path"]: e for e in body["endpoints"]}
        ok &= _check("POST /fresh is new, GET /keep is not", by_path["/fresh"]["change"] == "new" and by_path["/keep"]["change"] is None, str(by_path))
        ok &= _check("new_count == 1", body["new_count"] == 1)
        ok &= _check("removed == [GET /gone from app/routes/r.py]", body["removed"] == [{"method": "GET", "path": "/gone", "file_path": "app/routes/r.py"}], str(body["removed"]))
        ok &= _check("middleware: list passes through, [] stays []", by_path["/fresh"]["middleware"] == ["authenticateToken", "rateLimit"] and by_path["/keep"]["middleware"] == [])
        ok &= _check("group + path_params fields present", by_path["/fresh"]["group"] == "/fresh" and by_path["/fresh"]["path_params"] == [])

        # No earlier different-commit run -> no baseline, nothing marked new.
        _clear_previous_runs(db, repository)
        make_run("9", 0, [("GET", "/solo", None)])
        resp = client.get(f"/api/v1/repos/{repository.id}/apis", headers=_auth(create_access_token(subject=user.email)))
        body = resp.json()
        ok &= _check(
            "single run: baseline null, new_count 0, no removed, change null, middleware null stays null",
            body["baseline"] is None and body["new_count"] == 0 and body["removed"] == []
            and body["endpoints"][0]["change"] is None and body["endpoints"][0]["middleware"] is None,
            str(body)[:300],
        )
        return ok
    finally:
        db.close()


# --- group 7: detail (snippets + touches) -----------------------------------------------

_FAKE_FILE = "\n".join(f"line {i}" for i in range(1, 301))


def _fake_contents(*args, **kwargs):
    return {"encoding": "base64", "content": base64.b64encode(_FAKE_FILE.encode()).decode()}


def run_detail() -> bool:
    print("\n=== group 7: detail — snippets (fetch stubbed), truncation, degrade paths, touches ===")
    db = SessionLocal()
    original = (api_reference_service.get_installation_token_for_user, github_service.get_file_contents)
    try:
        user = _get_or_create_user(db, "smoke-api-apis-detail@local.test")
        other = _get_or_create_user(db, "smoke-api-apis-detail-other@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-apis-detail", owner="smoke-api", name="apis-detail"
        )
        _clear_previous_runs(db, repository)

        run = _new_completed_run(db, repository, "3" * 40)
        run.database_provider = "postgresql"
        db.commit()
        routes_file = _add_file(db, repository, run, "server/routes/user.py")
        ctl_file = _add_file(db, repository, run, "server/controllers/user_ctl.py")
        index_file = _add_file(db, repository, run, "server/index.py")
        routes_file.loc, routes_file.complexity_score = 120, 7
        sym_inside = _add_symbol(db, repository, run, routes_file, "inner", 21, 24)
        sym_outside = _add_symbol(db, repository, run, ctl_file, "list_users", 5, 8)

        def ep(path: str, **kw) -> ApiEndpoint:
            row = ApiEndpoint(
                repository_id=repository.id, analysis_run_id=run.id, method="GET", path=path,
                file_id=routes_file.id, module=routes_file.module, framework=Framework.FASTAPI, **kw,
            )
            db.add(row)
            return row

        e_inline = ep("/inline", handler_name="<inline>", handler_line_start=10, handler_line_end=14)
        e_inside = ep("/inside", handler_symbol_id=sym_inside.id, handler_name="inner", handler_line_start=20, handler_line_end=25)
        e_outside = ep("/outside", handler_symbol_id=sym_outside.id, handler_name="list_users", handler_line_start=30, handler_line_end=31)
        e_long = ep("/long", handler_name="<inline>", handler_line_start=50, handler_line_end=250)
        e_old = ep("/old")
        db.commit()
        for e in (e_inline, e_inside, e_outside, e_long, e_old):
            db.refresh(e)

        # routes_file <-> ctl_file (a 2-file cycle), routes_file -> prisma/express/fs, index -> routes_file
        def edge(src: File, dst: File | None, pkg: str | None = None) -> None:
            db.add(DependencyEdgeRow(
                repository_id=repository.id, analysis_run_id=run.id, source_file_id=src.id,
                target_file_id=dst.id if dst else None, target_package_name=pkg,
                edge_type=DependencyEdgeType.INTERNAL if dst else DependencyEdgeType.EXTERNAL,
            ))
        edge(routes_file, ctl_file)
        edge(ctl_file, routes_file)
        edge(index_file, routes_file)
        edge(routes_file, None, "@prisma/client")
        edge(routes_file, None, "express")
        edge(routes_file, None, "fs")  # a builtin -> must be excluded
        for sev, desc in ((DebtSeverity.MEDIUM, "m-issue"), (DebtSeverity.CRITICAL, "c-issue")):
            db.add(DebtIssue(analysis_run_id=run.id, issue_type=DebtIssueType.HIGH_COMPLEXITY, severity=sev,
                             file_id=routes_file.id, module=routes_file.module, description=desc, metric_value=1.0))
        db.commit()

        client = TestClient(app)
        headers = _auth(create_access_token(subject=user.email))
        url = lambda e: f"/api/v1/repos/{repository.id}/apis/{e.id}"  # noqa: E731

        api_reference_service.get_installation_token_for_user = lambda u: "tok"
        github_service.get_file_contents = _fake_contents

        body = client.get(url(e_inline), headers=headers).json()
        d = body["declaration"]
        ok = _check(
            "inline: declaration is exactly lines 10..14 of the route file",
            d is not None and d["file_path"] == "server/routes/user.py" and d["start_line"] == 10 and d["end_line"] == 14
            and d["code"] == "\n".join(f"line {i}" for i in range(10, 15)) and d["truncated"] is False and body["handler_code"] is None,
            str(d),
        )
        t = body["touches"]
        ok &= _check("touches: route_file/loc/complexity/routes_in_file", t["route_file"] == "server/routes/user.py" and t["loc"] == 120 and t["complexity_score"] == 7 and t["routes_in_file"] == 5, str(t)[:300])
        ok &= _check("touches: in_cycle with cycle_size 2", t["in_cycle"] is True and t["cycle_size"] == 2, str(t["in_cycle"]) + str(t["cycle_size"]))
        ok &= _check("touches: imports == [controller]; used_by == [controller (cycle edge), index], total 2",
                     [i["file_path"] for i in t["imports"]] == ["server/controllers/user_ctl.py"]
                     and [u["file_path"] for u in t["used_by"]] == ["server/controllers/user_ctl.py", "server/index.py"]
                     and t["used_by_total"] == 2, str(t["imports"]) + str(t["used_by"]))
        ext = {(x["label"], x["kind"]): x["packages"] for x in t["external"]}
        ok &= _check(
            "touches: @prisma/client -> PostgreSQL (data), express -> External Dependencies, builtin 'fs' excluded",
            ext == {("PostgreSQL", "data"): ["@prisma/client"], ("External Dependencies", "external"): ["express"]}, str(ext),
        )
        ok &= _check("touches: debt sorted critical first", [x["description"] for x in t["debt"]] == ["c-issue", "m-issue"], str(t["debt"]))

        body = client.get(url(e_inside), headers=headers).json()
        ok &= _check("handler inside the declaration span -> no separate handler_code", body["declaration"] is not None and body["handler_code"] is None)

        body = client.get(url(e_outside), headers=headers).json()
        h = body["handler_code"]
        ok &= _check(
            "handler in another file -> handler_code is that file's lines 5..8",
            h is not None and h["file_path"] == "server/controllers/user_ctl.py" and h["start_line"] == 5 and h["end_line"] == 8, str(h),
        )

        body = client.get(url(e_long), headers=headers).json()
        d = body["declaration"]
        ok &= _check("200-line declaration is cut to 60 lines and flagged", d is not None and d["truncated"] is True and d["end_line"] - d["start_line"] + 1 == 60, str(d and (d["start_line"], d["end_line"], d["truncated"])))

        body = client.get(url(e_old), headers=headers).json()
        ok &= _check("old-run row (no lines, no symbol): no code, note says re-analyze, touches still there",
                     body["declaration"] is None and "re-analyze" in (body["source_note"] or "") and body["touches"]["route_file"] == "server/routes/user.py", str(body)[:300])

        # GitHub failures degrade, never 5xx
        def boom(*a, **k):
            raise github_service.GitHubServiceError(502, "down")
        github_service.get_file_contents = boom
        resp = client.get(url(e_inline), headers=headers)
        b = resp.json()
        ok &= _check("GitHub error -> 200, no code, note, touches intact", resp.status_code == 200 and b["declaration"] is None and b["source_note"] and b["touches"]["routes_in_file"] == 5, resp.text[:200])

        def no_auth(u):
            raise GitHubAuthError("no installation")
        api_reference_service.get_installation_token_for_user = no_auth
        resp = client.get(url(e_inline), headers=headers)
        ok &= _check("GitHub auth error -> 200 with a note", resp.status_code == 200 and "not connected" in (resp.json()["source_note"] or ""), resp.text[:200])

        # ownership + unknown id
        resp = client.get(url(e_inline), headers=_auth(create_access_token(subject=other.email)))
        ok &= _check("other user -> 404", resp.status_code == 404, str(resp.status_code))
        resp = client.get(f"/api/v1/repos/{repository.id}/apis/{uuid.uuid4()}", headers=headers)
        ok &= _check("unknown endpoint id -> 404", resp.status_code == 404, str(resp.status_code))
        return ok
    finally:
        api_reference_service.get_installation_token_for_user, github_service.get_file_contents = original
        db.close()


# --- group 8: live bonus ---------------------------------------------------------------


def run_live_bonus() -> bool:
    print("\n=== group 8 (non-gating): HireSight latest run, endpoint count == COUNT(*) ===")
    db = SessionLocal()
    try:
        repository = db.execute(
            select(Repository).where(Repository.owner == LIVE_TEST_OWNER, Repository.name == LIVE_TEST_REPO)
        ).scalars().first()
        if repository is None:
            print("  [SKIP] no HireSight repository in this database")
            return True
        run = analysis_read_service.get_latest_completed_run(db, repository.id)
        if run is None:
            print("  [SKIP] HireSight has no completed run")
            return True
        user = db.get(User, repository.connected_user_id)
        count = db.execute(select(func.count()).select_from(ApiEndpoint).where(ApiEndpoint.analysis_run_id == run.id)).scalar_one()
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/apis", headers=_auth(create_access_token(subject=user.email)))
        body = resp.json()
        with_handler = sum(1 for e in body["endpoints"] if e["handler"])
        print(f"  info: {len(body['endpoints'])} endpoints over HTTP, COUNT(*)={count}, {with_handler} with a handler")
        return _check("HTTP count == COUNT(*)", resp.status_code == 200 and len(body["endpoints"]) == count)
    finally:
        db.close()


def main() -> int:
    gating = [run_not_analyzed(), run_ownership_404(), run_seeded_run(), run_persist_roundtrip(), run_pure_helpers(), run_list_extras(), run_detail()]
    bonus = run_live_bonus()
    print("\n" + ("ALL GATING GROUPS PASS" if all(gating) else "SOME GATING GROUPS FAILED") + ("" if bonus else " (live bonus failed)"))
    return 0 if all(gating) else 1


if __name__ == "__main__":
    sys.exit(main())
