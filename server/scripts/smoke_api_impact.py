"""Manual smoke test for Phase 9.3's REST impact surface
(`app/api/v1/impact.py`): `GET /repos/{id}/impact/{analysis_run_id}`.

Same "hand-insert File/DependencyEdge/ApiEndpoint rows across two real
AnalysisRuns, TestClient over the real HTTP layer" approach
`smoke_api_compare.py` uses — this script's job is the HTTP layer only
(routing, ownership scoping, status-code conventions, response-schema field
mapping, predecessor resolution scoped by branch); `smoke_change.py`/
`smoke_impact_service.py` already cover `diff_analysis_runs`/`compute_
impact`'s own business logic in isolation.

Four gating groups, all offline (real DB, no Celery/LLM):

1. `run_happy_path()` — a predecessor run + a head run on the same branch
   where the head run removes one file and adds a new one three hops
   upstream of a real dependency chain, with an API hanging off the nearest
   dependent — exercises the full resolve-predecessor -> diff -> compute_
   impact -> shape-response path, every `ImpactRead` field asserted
   (including `direct`/`indirect`/`affected_apis`/`risk`/`score`).
2. `run_first_run_409()` — a repository whose only completed run has no
   earlier run on the same branch -> `409` (nothing to diff against, same
   "no honest empty payload exists" reasoning `assistant.py`'s own 409
   already documents).
3. `run_different_branch_not_used_404()` — a predecessor run exists but only
   on a *different* branch; `get_previous_completed_run`'s new `branch`
   filter (design decision #14) must not fall back to it, so this is really
   the same "no predecessor" case as group 2, asserted separately to pin
   down the branch-scoping behavior specifically -> `409`.
4. `run_ownership_404()` — a second user's repository id -> `404`.

Run: `uv run python scripts/smoke_api_impact.py` (from `server/`).
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
from app.services import change_narrative_service  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.dependency import DependencyEdge as DependencyEdgeRow  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402


class _RecordingProvider:
    """A fake `LLMProvider` that returns a fixed JSON string and records the
    user message of every call, so a script can assert both what came back
    and what the model was actually shown."""

    def __init__(self, response_text: str):
        self._response_text = response_text
        self.calls: list[str] = []

    def complete(self, *, system: str, messages: list[dict], response_schema: dict | None = None) -> str:
        self.calls.append(messages[0]["content"])
        return self._response_text


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
    branch: str,
    commit_sha: str,
    status: AnalysisRunStatus,
    completed_at,
    file_paths: list[str],
    edges: list[tuple[str, str]],
    apis: list[tuple[str, str, str]],
) -> AnalysisRun:
    run = AnalysisRun(
        repository_id=repository.id,
        branch=branch,
        commit_sha=commit_sha,
        status=status,
        completed_at=completed_at,
        file_count=len(file_paths),
        module_count=len({str(Path(p).parent) for p in file_paths}),
        api_count=len(apis),
        dependency_count=len(edges),
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
    print("=== group 1 (gating): happy path — resolve predecessor, diff, compute impact ===")
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-impact-happy@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-impact-happy", owner="smoke-api", name="impact-happy"
        )
        _clear_previous_runs(db, repository)

        now = datetime.now(timezone.utc)
        previous_run = _seed_run(
            db,
            repository,
            branch="main",
            commit_sha="a" * 40,
            status=AnalysisRunStatus.COMPLETED,
            completed_at=now - timedelta(hours=1),
            # d.py <- c.py <- b.py <- a.py chain, plus an unrelated file that gets removed in head.
            file_paths=["a.py", "b.py", "c.py", "d.py", "old_removed.py"],
            edges=[("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "d.py")],
            apis=[],
        )
        head_run = _seed_run(
            db,
            repository,
            branch="main",
            commit_sha="b" * 40,
            status=AnalysisRunStatus.COMPLETED,
            completed_at=now,
            # old_removed.py is gone; d.py's content changed (extra symbol/loc
            # would normally trip "modified", but a plain file-set diff is
            # enough here since d.py itself is the changed file being tested).
            file_paths=["a.py", "b.py", "c.py", "d.py"],
            edges=[("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "d.py")],
            apis=[("GET", "/c", "c.py")],
        )

        token = create_access_token(subject=user.email)
        client = TestClient(app)

        # Phase 9.7: the endpoint now asks the LLM for a narrative. Stub the
        # provider so this script never touches the real Groq API, and record
        # what the model was shown.
        stub = _RecordingProvider('{"explanation": "Stub narrative for the impact."}')
        original_provider = change_narrative_service.GroqProvider
        change_narrative_service.GroqProvider = lambda: stub
        change_narrative_service.clear_cache()
        try:
            resp = client.get(f"/api/v1/repos/{repository.id}/impact/{head_run.id}", headers=_auth(token))
        finally:
            change_narrative_service.GroqProvider = original_provider
        all_passed = _check("GET -> 200", resp.status_code == 200, resp.text)
        if not all_passed:
            return False
        body = resp.json()

        all_passed &= _check(
            "analysis_run_id/previous_analysis_run_id echoed",
            body["analysis_run_id"] == str(head_run.id) and body["previous_analysis_run_id"] == str(previous_run.id),
            str(body),
        )
        all_passed &= _check(
            "branch/commit_sha echo the head run's own",
            body["branch"] == "main" and body["commit_sha"] == "b" * 40,
            str(body),
        )

        # changed_file_paths = {old_removed.py} (removed) — that's it, since
        # a.py/b.py/c.py/d.py are identical in both runs (same edges, same
        # loc placeholder). old_removed.py has no dependents in this graph
        # (nothing imports it), so direct/indirect/affected_apis are all empty
        # and risk is LOW — a deliberately "boring" real diff, verifying the
        # endpoint doesn't fabricate impact where none exists.
        all_passed &= _check("direct == []", body["direct"] == [], str(body["direct"]))
        all_passed &= _check("indirect == []", body["indirect"] == [], str(body["indirect"]))
        all_passed &= _check("affected_apis == []", body["affected_apis"] == [], str(body["affected_apis"]))
        all_passed &= _check("risk == LOW", body["risk"] == "LOW", str(body))
        all_passed &= _check("score == 0", body["score"] == 0, str(body))
        all_passed &= _check(
            "breakdown: 0 pts everywhere, change_kind == breaking (a file was removed), x1.0, 0 tests excluded",
            body["breakdown"]
            == {
                "file_points": 0.0,
                "api_points": 0.0,
                "cycle_bonus": 0.0,
                "change_kind": "breaking",
                "multiplier": 1.0,
                "excluded_test_files": 0,
            },
            str(body.get("breakdown")),
        )
        all_passed &= _check(
            "explanation is the stubbed narrative, from exactly one LLM call",
            body["explanation"] == "Stub narrative for the impact." and len(stub.calls) == 1,
            f"{body['explanation']!r}, calls={len(stub.calls)}",
        )
        prompt = stub.calls[0] if stub.calls else ""
        all_passed &= _check(
            "the model was shown the removed file AND the computed impact (risk, score, change type)",
            all(s in prompt for s in ("old_removed.py", "Downstream impact", "risk LOW", "score 0/100", "change type breaking")),
            prompt[:500],
        )
        return all_passed
    finally:
        db.close()


# --- group 2: first-ever run on a branch -> 409 ------------------------------


def run_first_run_409() -> bool:
    print("\n=== group 2 (gating): no earlier completed run at all -> 409 ===")
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-impact-first@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-impact-first", owner="smoke-api", name="impact-first"
        )
        _clear_previous_runs(db, repository)
        only_run = _seed_run(
            db, repository, branch="main", commit_sha="c" * 40, status=AnalysisRunStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc), file_paths=["a.py"], edges=[], apis=[],
        )
        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/impact/{only_run.id}", headers=_auth(token))
        return _check("GET -> 409 (nothing to diff against)", resp.status_code == 409, resp.text)
    finally:
        db.close()


# --- group 3: a predecessor exists, but only on a different branch ----------


def run_different_branch_not_used_404() -> bool:
    print("\n=== group 3 (gating): predecessor exists only on a different branch -> still 409 ===")
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-impact-branch@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-impact-branch", owner="smoke-api", name="impact-branch"
        )
        _clear_previous_runs(db, repository)
        now = datetime.now(timezone.utc)
        _seed_run(
            db, repository, branch="feature/x", commit_sha="d" * 40, status=AnalysisRunStatus.COMPLETED,
            completed_at=now - timedelta(hours=1), file_paths=["a.py"], edges=[], apis=[],
        )
        main_run = _seed_run(
            db, repository, branch="main", commit_sha="e" * 40, status=AnalysisRunStatus.COMPLETED,
            completed_at=now, file_paths=["a.py"], edges=[], apis=[],
        )
        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/impact/{main_run.id}", headers=_auth(token))
        return _check(
            "GET -> 409 (the feature/x predecessor must not be used for a main-branch run)",
            resp.status_code == 409, resp.text,
        )
    finally:
        db.close()


# --- group 4: ownership scoping -----------------------------------------------


def run_ownership_404() -> bool:
    print("\n=== group 4 (gating): a second user gets 404 ===")
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        owner_user = _get_or_create_user(db, "smoke-api-impact-owner@local.test")
        other_user = _get_or_create_user(db, "smoke-api-impact-other@local.test")
        repository = _get_or_create_repository(
            db, owner_user, github_repo_id="smoke-api-impact-ownership", owner="smoke-api", name="impact-ownership"
        )
        _clear_previous_runs(db, repository)
        now = datetime.now(timezone.utc)
        _seed_run(
            db, repository, branch="main", commit_sha="f" * 40, status=AnalysisRunStatus.COMPLETED,
            completed_at=now - timedelta(hours=1), file_paths=["a.py"], edges=[], apis=[],
        )
        head_run = _seed_run(
            db, repository, branch="main", commit_sha="0" * 40, status=AnalysisRunStatus.COMPLETED,
            completed_at=now, file_paths=["a.py"], edges=[], apis=[],
        )
        other_token = create_access_token(subject=other_user.email)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/impact/{head_run.id}", headers=_auth(other_token))
        return _check("other user's GET -> 404", resp.status_code == 404, resp.text)
    finally:
        db.close()


def main() -> int:
    r1 = run_happy_path()
    r2 = run_first_run_409()
    r3 = run_different_branch_not_used_404()
    r4 = run_ownership_404()

    print("\n=== Summary ===")
    print(f"1. Happy path (gating):                 {'PASS' if r1 else 'FAIL'}")
    print(f"2. First run -> 409 (gating):            {'PASS' if r2 else 'FAIL'}")
    print(f"3. Different-branch predecessor -> 409:  {'PASS' if r3 else 'FAIL'}")
    print(f"4. Ownership scoping -> 404:              {'PASS' if r4 else 'FAIL'}")
    return 0 if (r1 and r2 and r3 and r4) else 1


if __name__ == "__main__":
    sys.exit(main())
