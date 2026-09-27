"""Manual smoke test for Phase 9.3's REST compare surface
(`app/api/v1/compare.py`): `GET /repos/{id}/compare?base_run_id=...&head_run_id=...`.

Uses `fastapi.testclient.TestClient(app)` against hand-inserted `File`/
`DependencyEdge`/`ApiEndpoint` rows across two real `AnalysisRun`s — same
"insert directly, don't run the real pipeline" precedent
`smoke_impact_service.py`/`smoke_rag_schema.py` already established, since
this sub-phase needs precise control over the diff shape, not real parsed
source. This script's job is the HTTP layer only: routing, ownership
scoping, status-code conventions, and response-schema field mapping —
`smoke_change.py`/`smoke_impact_service.py` already cover `diff_analysis_
runs`/`compute_impact`'s own business logic.

Four gating groups, all offline (real DB, no Celery/LLM):

1. `run_happy_path()` — a base/head run pair with a deliberate diff: one
   added file + one removed file (each with an API), one added inter-module
   edge with no coupling/circular signal (expect `risk=low`), one removed
   edge (also `risk=low`), and a fresh 2-file cycle introduced only in the
   head run (two added edges, both `risk=high` since they touch a real
   circular file) — exercises every `CompareRead` field, including the
   count/debt-score deltas computed straight off the two `AnalysisRun` rows.
2. `run_incomplete_run_409()` — `base_run_id` pointing at a `QUEUED` (not
   `COMPLETED`) run -> `409`.
3. `run_nonexistent_run_404()` — a random UUID for `head_run_id` -> `404`
   (not `409` — matches `analysis.py::_analysis_run_or_404`'s own
   404-not-403-or-409 convention for "this id doesn't resolve at all").
4. `run_ownership_404()` — a second user's repository id -> `404`.

Run: `uv run python scripts/smoke_api_compare.py` (from `server/`).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
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
from app.models.repository import Repository  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_run(
    db: Session,
    repository: Repository,
    *,
    commit_sha: str,
    status: AnalysisRunStatus,
    file_paths: list[str],
    edges: list[tuple[str, str]],
    apis: list[tuple[str, str, str]],
    debt_score: float,
) -> AnalysisRun:
    run = AnalysisRun(
        repository_id=repository.id,
        branch="main",
        commit_sha=commit_sha,
        status=status,
        file_count=len(file_paths),
        module_count=len({str(Path(p).parent) for p in file_paths}),
        api_count=len(apis),
        dependency_count=len(edges),
        debt_score=debt_score,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    file_id_by_path: dict[str, uuid.UUID] = {}
    for path in file_paths:
        file_row = File(
            repository_id=repository.id,
            analysis_run_id=run.id,
            path=path,
            language=Language.PYTHON,
            size_bytes=100,
            module=str(Path(path).parent),
            loc=10,
            last_analyzed_commit_sha=commit_sha,
        )
        db.add(file_row)
        db.flush()
        file_id_by_path[path] = file_row.id

    for source_path, target_path in edges:
        db.add(
            DependencyEdgeRow(
                repository_id=repository.id,
                analysis_run_id=run.id,
                source_file_id=file_id_by_path[source_path],
                target_file_id=file_id_by_path[target_path],
                edge_type=DependencyEdgeType.INTERNAL,
            )
        )

    for method, path, file_path in apis:
        db.add(
            ApiEndpoint(
                repository_id=repository.id,
                analysis_run_id=run.id,
                method=method,
                path=path,
                file_id=file_id_by_path[file_path],
                module=str(Path(file_path).parent),
                framework=Framework.FASTAPI,
            )
        )

    db.commit()
    db.refresh(run)
    return run


# --- group 1: happy path -----------------------------------------------------


def run_happy_path() -> bool:
    print("=== group 1 (gating): happy path — every CompareRead field ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-compare-happy@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-compare-happy", owner="smoke-api", name="compare-happy"
        )
        _clear_previous_runs(db, repository)

        base_run = _seed_run(
            db,
            repository,
            commit_sha="a" * 40,
            status=AnalysisRunStatus.COMPLETED,
            file_paths=["pkgA/a.py", "pkgA/old_removed.py", "pkgB/shared.py"],
            edges=[("pkgA/a.py", "pkgB/shared.py"), ("pkgA/old_removed.py", "pkgB/shared.py")],
            apis=[("GET", "/old", "pkgA/old_removed.py")],
            debt_score=10.0,
        )
        head_run = _seed_run(
            db,
            repository,
            commit_sha="b" * 40,
            status=AnalysisRunStatus.COMPLETED,
            file_paths=["pkgA/a.py", "pkgA/new_added.py", "pkgB/shared.py", "pkgD/x.py", "pkgE/y.py"],
            edges=[
                ("pkgA/a.py", "pkgB/shared.py"),
                ("pkgA/new_added.py", "pkgB/shared.py"),
                ("pkgD/x.py", "pkgE/y.py"),
                ("pkgE/y.py", "pkgD/x.py"),
            ],
            apis=[("GET", "/new", "pkgA/new_added.py")],
            debt_score=15.0,
        )

        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/repos/{repository.id}/compare",
            headers=_auth(token),
            params={"base_run_id": str(base_run.id), "head_run_id": str(head_run.id)},
        )
        all_passed = _check("GET -> 200", resp.status_code == 200, resp.text)
        if not all_passed:
            return False
        body = resp.json()

        all_passed &= _check("base_run_id/head_run_id echoed", body["base_run_id"] == str(base_run.id) and body["head_run_id"] == str(head_run.id))
        all_passed &= _check(
            "base/head branch+commit_sha echoed",
            body["base_branch"] == "main" and body["base_commit_sha"] == "a" * 40
            and body["head_branch"] == "main" and body["head_commit_sha"] == "b" * 40,
            str(body),
        )

        added_paths = {f["path"] for f in body["added_files"]}
        all_passed &= _check(
            "added_files == {pkgA/new_added.py, pkgD/x.py, pkgE/y.py}, all noted 'new file'",
            added_paths == {"pkgA/new_added.py", "pkgD/x.py", "pkgE/y.py"}
            and all(f["note"] == "new file" for f in body["added_files"]),
            str(body["added_files"]),
        )
        removed_paths = {f["path"] for f in body["removed_files"]}
        all_passed &= _check(
            "removed_files == {pkgA/old_removed.py}, noted 'removed file'",
            removed_paths == {"pkgA/old_removed.py"} and body["removed_files"][0]["note"] == "removed file",
            str(body["removed_files"]),
        )

        all_passed &= _check("added_apis == ['GET /new']", body["added_apis"] == ["GET /new"], str(body["added_apis"]))
        all_passed &= _check("removed_apis == ['GET /old']", body["removed_apis"] == ["GET /old"], str(body["removed_apis"]))

        deps = body["changed_dependencies"]
        all_passed &= _check("4 changed_dependencies (3 added + 1 removed)", len(deps) == 4, str(deps))
        risk_by_change = {(d["edge"], d["change"]): d["risk"] for d in deps}
        expected = {
            ("pkgA -> pkgB", "new import of `pkgB/shared.py`"): "low",
            ("pkgD -> pkgE", "new import of `pkgE/y.py`"): "high",
            ("pkgE -> pkgD", "new import of `pkgD/x.py`"): "high",
            ("pkgA -> pkgB", "removed import of `pkgB/shared.py`"): "low",
        }
        all_passed &= _check(
            "each changed_dependency has the expected edge/change/risk (low for the isolated edges, "
            "high for the two edges forming the fresh pkgD<->pkgE cycle)",
            risk_by_change == expected, str(risk_by_change),
        )

        all_passed &= _check("file_count_delta == 2 (5 - 3)", body["file_count_delta"] == 2, str(body))
        all_passed &= _check("module_count_delta == 2 (4 - 2)", body["module_count_delta"] == 2, str(body))
        all_passed &= _check("api_count_delta == 0 (1 - 1)", body["api_count_delta"] == 0, str(body))
        all_passed &= _check("dependency_count_delta == 2 (4 - 2)", body["dependency_count_delta"] == 2, str(body))
        all_passed &= _check("debt_score_delta == 5.0 (15.0 - 10.0)", body["debt_score_delta"] == 5.0, str(body))
        all_passed &= _check("explanation is None (9.7's job)", body["explanation"] is None, str(body))
        return all_passed
    finally:
        db.close()


# --- group 2: incomplete run -> 409 ------------------------------------------


def run_incomplete_run_409() -> bool:
    print("\n=== group 2 (gating): base_run not COMPLETED -> 409 ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-compare-incomplete@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-compare-incomplete", owner="smoke-api", name="compare-incomplete"
        )
        _clear_previous_runs(db, repository)
        queued_run = _seed_run(
            db, repository, commit_sha="c" * 40, status=AnalysisRunStatus.QUEUED,
            file_paths=[], edges=[], apis=[], debt_score=0.0,
        )
        completed_run = _seed_run(
            db, repository, commit_sha="d" * 40, status=AnalysisRunStatus.COMPLETED,
            file_paths=[], edges=[], apis=[], debt_score=0.0,
        )
        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/repos/{repository.id}/compare",
            headers=_auth(token),
            params={"base_run_id": str(queued_run.id), "head_run_id": str(completed_run.id)},
        )
        return _check("GET -> 409 (not 500, not a fabricated 200)", resp.status_code == 409, resp.text)
    finally:
        db.close()


# --- group 3: nonexistent run id -> 404 --------------------------------------


def run_nonexistent_run_404() -> bool:
    print("\n=== group 3 (gating): head_run_id that doesn't exist -> 404 ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-compare-missing@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-compare-missing", owner="smoke-api", name="compare-missing"
        )
        _clear_previous_runs(db, repository)
        base_run = _seed_run(
            db, repository, commit_sha="e" * 40, status=AnalysisRunStatus.COMPLETED,
            file_paths=[], edges=[], apis=[], debt_score=0.0,
        )
        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/repos/{repository.id}/compare",
            headers=_auth(token),
            params={"base_run_id": str(base_run.id), "head_run_id": str(uuid.uuid4())},
        )
        return _check("GET -> 404 (nonexistent run id, not 409)", resp.status_code == 404, resp.text)
    finally:
        db.close()


# --- group 4: ownership scoping -----------------------------------------------


def run_ownership_404() -> bool:
    print("\n=== group 4 (gating): a second user gets 404 ===")
    db = SessionLocal()
    try:
        owner_user = _get_or_create_user(db, "smoke-api-compare-owner@local.test")
        other_user = _get_or_create_user(db, "smoke-api-compare-other@local.test")
        repository = _get_or_create_repository(
            db, owner_user, github_repo_id="smoke-api-compare-ownership", owner="smoke-api", name="compare-ownership"
        )
        _clear_previous_runs(db, repository)
        base_run = _seed_run(
            db, repository, commit_sha="f" * 40, status=AnalysisRunStatus.COMPLETED,
            file_paths=[], edges=[], apis=[], debt_score=0.0,
        )
        head_run = _seed_run(
            db, repository, commit_sha="0" * 40, status=AnalysisRunStatus.COMPLETED,
            file_paths=[], edges=[], apis=[], debt_score=0.0,
        )
        other_token = create_access_token(subject=other_user.email)
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/repos/{repository.id}/compare",
            headers=_auth(other_token),
            params={"base_run_id": str(base_run.id), "head_run_id": str(head_run.id)},
        )
        return _check("other user's GET -> 404", resp.status_code == 404, resp.text)
    finally:
        db.close()


def main() -> int:
    r1 = run_happy_path()
    r2 = run_incomplete_run_409()
    r3 = run_nonexistent_run_404()
    r4 = run_ownership_404()

    print("\n=== Summary ===")
    print(f"1. Happy path (gating):            {'PASS' if r1 else 'FAIL'}")
    print(f"2. Incomplete run -> 409 (gating): {'PASS' if r2 else 'FAIL'}")
    print(f"3. Nonexistent run -> 404 (gating):{'PASS' if r3 else 'FAIL'}")
    print(f"4. Ownership scoping -> 404:       {'PASS' if r4 else 'FAIL'}")
    return 0 if (r1 and r2 and r3 and r4) else 1


if __name__ == "__main__":
    sys.exit(main())
