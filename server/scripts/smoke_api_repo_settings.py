"""Smoke test for Phase 10.11 — repository settings, disconnect and purge
(`app/api/v1/repository_settings.py`, `repo_service`'s settings/delete/purge
functions, `ingestion.normalize_ignore_patterns` + the slash-pattern matcher,
and the Celery task's `ignore_patterns` plumbing).

`TestClient(app)` against the real dev database. Fully offline: GitHub
(installation check, branch head, tarball download), Celery's `.delay`, Redis
progress events, RAG indexing and AI insights are all replaced in-process.

Gating groups:

1. `run_patterns()` — pure: the normaliser (trim, blank/`#` dropped, `\\`,
   `./`, trailing `/`, dedupe, every rejection) and the matcher (slash-free =
   any segment; slash = anchored + everything inside a folder; defaults
   unchanged).
2. `run_get_settings()` — every field of `GET .../settings` on a seeded repo:
   last analysis is the newest *completed* run (not a newer failed/queued one),
   run count, in-flight run, defaults; the five installation states from
   stubbed GitHub answers; the installation id never appears in the body;
   a never-analyzed repo has `last_analysis: null`.
3. `run_patch_matrix()` — PATCH validation: valid mode/patterns/clear/no-op;
   every invalid body is a 422 and leaves the DB exactly as it was; the mode
   is stored by member *name* (raw SQL).
4. `run_ownership()` — another user's repo: GET/PATCH/DELETE/purge all 404 and
   change nothing; no token -> 401; unknown id -> 404.
5. `run_delete()` — 409 while a run is in flight (nothing deleted); a stale
   (> window) non-terminal run does not block; then 204 and every child table
   (runs, files, symbols, edges, endpoints, debt, RAG chunks, branches,
   onboarding progress) has zero rows for that repository by SQL.
6. `run_purge()` — 202, `deleted_run_count`, exactly one new `QUEUED`/`MANUAL`
   row at GitHub's head commit, every child row of the old runs gone,
   enqueued once; branches/settings/onboarding ticks kept; a second purge is a
   409 (the new run is in flight); no selected branch -> 400 and GitHub error
   -> 502, both with nothing deleted; broker down -> 503 + the new row FAILED.
7. `run_concurrent_purge()` — two simultaneous purges: one 202, one 409,
   exactly one run row afterwards (the row lock, not only the pre-check).
8. `run_pipeline_ignore()` — a real tarball through the real
   `ingest_repository` and the real `run_repository_analysis` task: with
   `legacy` + `/server/tests` saved via PATCH, those files are absent from the
   run's `File` rows and everything else is present; with the patterns
   cleared, the next run has them back (file-count difference = exactly the
   ignored files).

Run: `uv run python scripts/smoke_api_repo_settings.py` (from `server/`).
"""

from __future__ import annotations

import io
import sys
import tarfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.api.v1.repository_settings as settings_module  # noqa: E402
import app.workers.tasks.analysis_tasks as analysis_tasks  # noqa: E402
from app.analysis.apis import Framework  # noqa: E402
from app.analysis.chunking import RagChunkType  # noqa: E402
from app.analysis.debt import DebtIssueType, DebtSeverity  # noqa: E402
from app.analysis.dependencies import DependencyEdgeType  # noqa: E402
from app.analysis.ingestion import (  # noqa: E402
    DEFAULT_IGNORE_PATTERNS,
    _matches_ignore_patterns,
    normalize_ignore_patterns,
)
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.symbols import SymbolKind  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus, AnalysisRunTriggerSource  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.branch import Branch  # noqa: E402
from app.models.debt_issue import DebtIssue  # noqa: E402
from app.models.dependency import DependencyEdge  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.onboarding_progress import OnboardingProgress  # noqa: E402
from app.models.rag_chunk import RagChunk  # noqa: E402
from app.models.repository import AnalysisMode, Repository, RepositoryAnalysisStatus  # noqa: E402
from app.models.symbol import Symbol  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import github_service, repo_service  # noqa: E402
from app.services.repo_service import create_queued_analysis_run  # noqa: E402

USER_A = "smoke-repo-settings-a@local.test"
USER_B = "smoke-repo-settings-b@local.test"
INSTALLATION_ID = "424242987"  # must never appear in a response body
HEAD_SHA = "c3" * 20
FASTAPI_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fastapi_flask"
FASTAPI_FILES = ["simple_app.py", "app/api/v1/auth.py", "app/api/v1/items.py", "app/api/v1/router.py"]


def _check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _user(db: Session, email: str) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _fresh_repository(db: Session, user: User, name: str, *, branch: str | None = "main") -> Repository:
    """Deletes any leftover copy (cascade) and creates a clean one, with one
    selected branch unless `branch` is None."""
    github_repo_id = f"smoke-repo-settings-{name}"
    db.execute(
        delete(Repository).where(Repository.connected_user_id == user.id, Repository.github_repo_id == github_repo_id)
    )
    db.commit()
    repository = Repository(
        owner="smoke-settings", name=name, full_name=f"smoke-settings/{name}", github_repo_id=github_repo_id,
        default_branch="main", connected_user_id=user.id, installation_id=INSTALLATION_ID,
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)
    if branch is not None:
        db.add(Branch(repository_id=repository.id, name=branch, latest_commit_sha="0" * 40, is_default=True, is_selected=True))
        db.add(Branch(repository_id=repository.id, name="feature/x", latest_commit_sha="0" * 40, is_default=False, is_selected=False))
        db.commit()
    return repository


def _seed_run(
    db: Session, repository: Repository, *, status: AnalysisRunStatus = AnalysisRunStatus.COMPLETED,
    sha: str = "a1" * 20, completed_at: datetime | None = None, created_at: datetime | None = None,
    file_count: int = 0, with_children: bool = False,
) -> AnalysisRun:
    now = datetime.now(timezone.utc)
    run = AnalysisRun(
        id=uuid.uuid4(), repository_id=repository.id, branch="main", commit_sha=sha, status=status,
        trigger_source=AnalysisRunTriggerSource.MANUAL, file_count=file_count,
        completed_at=completed_at if completed_at is not None else (now if status in (AnalysisRunStatus.COMPLETED, AnalysisRunStatus.FAILED) else None),
    )
    if created_at is not None:
        run.created_at = created_at
        run.started_at = created_at
    db.add(run)
    db.flush()
    if with_children:
        file_a = File(id=uuid.uuid4(), repository_id=repository.id, analysis_run_id=run.id, path="src/a.py", size_bytes=10,
                      module="src", loc=3, last_analyzed_commit_sha=sha, language=Language.PYTHON)
        file_b = File(id=uuid.uuid4(), repository_id=repository.id, analysis_run_id=run.id, path="src/b.py", size_bytes=10,
                      module="src", loc=3, last_analyzed_commit_sha=sha, language=Language.PYTHON)
        db.add_all([file_a, file_b])
        db.flush()
        symbol = Symbol(id=uuid.uuid4(), repository_id=repository.id, file_id=file_a.id, analysis_run_id=run.id, name="f",
                        kind=SymbolKind.FUNCTION, line_start=1, line_end=2, module="src", language=Language.PYTHON)
        db.add(symbol)
        db.flush()
        db.add(DependencyEdge(repository_id=repository.id, analysis_run_id=run.id, source_file_id=file_a.id,
                              target_file_id=file_b.id, edge_type=DependencyEdgeType.INTERNAL))
        db.add(ApiEndpoint(repository_id=repository.id, analysis_run_id=run.id, method="GET", path="/x", file_id=file_a.id,
                           module="src", framework=Framework.FASTAPI))
        db.add(DebtIssue(analysis_run_id=run.id, issue_type=DebtIssueType.LARGE_FILE, severity=DebtSeverity.LOW,
                         description="seeded", metric_value=1.0, file_id=file_a.id))
        db.add(RagChunk(repository_id=repository.id, analysis_run_id=run.id, file_id=file_a.id, chunk_type=RagChunkType.SYMBOL,
                        chunk_index=0, chunk_count=1, chunk_text="def f(): pass", line_start=1, line_end=2, module="src",
                        embedding=[0.0] * get_settings().EMBEDDING_DIM))
    db.commit()
    db.refresh(run)
    return run


_CHILD_TABLES = {
    "analysis_runs": "select count(*) from analysis_runs where repository_id = :r",
    "files": "select count(*) from files where repository_id = :r",
    "symbols": "select count(*) from symbols where repository_id = :r",
    "dependency_edges": "select count(*) from dependency_edges where repository_id = :r",
    "api_endpoints": "select count(*) from api_endpoints where repository_id = :r",
    "debt_issues": "select count(*) from debt_issues d join analysis_runs a on a.id = d.analysis_run_id where a.repository_id = :r",
    "rag_chunks": "select count(*) from rag_chunks where repository_id = :r",
    "branches": "select count(*) from branches where repository_id = :r",
    "onboarding_progress": "select count(*) from onboarding_progress where repository_id = :r",
}


def _counts(db: Session, repository_id: uuid.UUID) -> dict[str, int]:
    db.expire_all()
    return {name: db.execute(text(sql), {"r": repository_id}).scalar_one() for name, sql in _CHILD_TABLES.items()}


# Orphaned debt issues can't be found by repository once their run is gone, so
# group 5/6 also count by the exact seeded run ids.
def _debt_for_runs(db: Session, run_ids: list[uuid.UUID]) -> int:
    return db.execute(select(func.count()).select_from(DebtIssue).where(DebtIssue.analysis_run_id.in_(run_ids))).scalar_one()


def _auth(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=email)}"}


class _Patches:
    def __init__(self):
        self._saved: list[tuple[object, str, object]] = []

    def set(self, obj, attr, value) -> None:
        self._saved.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    def restore(self) -> None:
        for obj, attr, value in reversed(self._saved):
            setattr(obj, attr, value)
        self._saved.clear()


# --- group 1 ---------------------------------------------------------------------


def run_patterns() -> bool:
    print("=== group 1: pattern normaliser + matcher (pure) ===")
    ok = _check(
        "trim / blank / # / backslash / ./ / trailing slash / dedupe",
        normalize_ignore_patterns(["  docs/ ", "# a comment", "", "   ", ".\\server\\tests", "./x/", "docs", "/legacy", "*.min.js"])
        == ["docs", "server/tests", "x", "/legacy", "*.min.js"],
        str(normalize_ignore_patterns(["  docs/ ", "# a comment", "", ".\\server\\tests", "./x/", "docs", "/legacy", "*.min.js"])),
    )
    ok &= _check("empty list -> []", normalize_ignore_patterns([]) == [])
    ok &= _check("50 patterns + comments accepted", len(normalize_ignore_patterns([f"p{i}" for i in range(50)] + ["# c"] * 20)) == 50)
    ok &= _check("exactly 200 chars accepted", normalize_ignore_patterns(["a" * 200]) == ["a" * 200])
    for label, raw in {
        "negation '!'": ["!keep.py"],
        "'*' (everything)": ["*"],
        "'/**/' (everything)": ["/**/"],
        "'/' alone": ["/"],
        "201 chars": ["a" * 201],
        "tab inside": ["a\tb"],
        "51 patterns": [f"p{i}" for i in range(51)],
    }.items():
        try:
            normalize_ignore_patterns(raw)
            ok &= _check(f"rejects {label}", False, "no ValueError")
        except ValueError:
            ok &= _check(f"rejects {label}", True)

    m = _matches_ignore_patterns
    ok &= _check("slash-free pattern matches a segment at any depth", m("a/b/legacy/x.py", ("legacy",)))
    ok &= _check("slash-free glob matches a file name", m("web/app.min.js", ("*.min.js",)))
    ok &= _check("slash pattern ignores everything inside the folder", m("server/tests/unit/t.py", ("server/tests",)))
    ok &= _check("slash pattern is anchored at the root", not m("x/server/tests/t.py", ("server/tests",)))
    ok &= _check("leading '/' anchors a single name", m("legacy/x.py", ("/legacy",)) and not m("a/legacy/x.py", ("/legacy",)))
    ok &= _check("slash pattern with a wildcard folder", m("src/a/gen/x.ts", ("src/*/gen",)))
    ok &= _check("slash pattern does not match a sibling prefix", not m("server/testsuite/x.py", ("server/tests",)))
    ok &= _check(
        "defaults unchanged (node_modules deep, *.egg-info, plain source kept)",
        m("a/node_modules/p/i.js", DEFAULT_IGNORE_PATTERNS)
        and m("pkg.egg-info/PKG-INFO", DEFAULT_IGNORE_PATTERNS)
        and not m("src/app.py", DEFAULT_IGNORE_PATTERNS),
    )
    return ok


# --- group 2 ---------------------------------------------------------------------


def run_get_settings() -> bool:
    print("=== group 2: GET /repos/{id}/settings ===")
    db = SessionLocal()
    patches = _Patches()
    try:
        user = _user(db, USER_A)
        repo = _fresh_repository(db, user, "get")
        now = datetime.now(timezone.utc)
        older = _seed_run(db, repo, sha="11" * 20, completed_at=now - timedelta(hours=3), file_count=7)
        newest = _seed_run(db, repo, sha="22" * 20, completed_at=now - timedelta(hours=1), file_count=9)
        _seed_run(db, repo, status=AnalysisRunStatus.FAILED, sha="33" * 20)  # newer, but failed
        repo.ignore_patterns = ["legacy"]
        repo.analysis_mode = AnalysisMode.MANUAL
        db.commit()

        answer: dict = {"value": {"id": int(INSTALLATION_ID), "suspended_at": None}}

        def fake_installation(owner, name):
            value = answer["value"]
            if isinstance(value, Exception):
                raise value
            return value

        patches.set(github_service, "get_repository_installation", fake_installation)
        client = TestClient(app)
        url = f"/api/v1/repos/{repo.id}/settings"
        resp = client.get(url, headers=_auth(USER_A))
        ok = _check("200", resp.status_code == 200, resp.text)
        body = resp.json()
        ok &= _check("identity fields", (body["owner"], body["name"], body["full_name"], body["private"], body["default_branch"])
                     == ("smoke-settings", "get", "smoke-settings/get", False, "main"), str(body))
        ok &= _check("selected_branch", body["selected_branch"] == "main")
        ok &= _check("installation connected", body["installation"] == "connected", body["installation"])
        ok &= _check("installation id never in the body", INSTALLATION_ID not in resp.text)
        last = body["last_analysis"] or {}
        ok &= _check(
            "last_analysis = newest COMPLETED run (not the newer FAILED one, not the older one)",
            last.get("analysis_run_id") == str(newest.id) and last.get("commit_sha") == "22" * 20 and last.get("file_count") == 9,
            f"{last} older={older.id}",
        )
        ok &= _check("analysis_run_count counts every status", body["analysis_run_count"] == 3, str(body["analysis_run_count"]))
        ok &= _check("no run in flight", body["run_in_flight"] is None)
        ok &= _check("mode serialised as lowercase value", body["analysis_mode"] == "manual")
        ok &= _check("ignore_patterns", body["ignore_patterns"] == ["legacy"])
        ok &= _check("defaults listed", body["default_ignore_patterns"] == list(DEFAULT_IGNORE_PATTERNS))
        ok &= _check("limits", (body["max_ignore_patterns"], body["max_ignore_pattern_length"]) == (50, 200))

        queued = _seed_run(db, repo, status=AnalysisRunStatus.PARSING, sha="44" * 20)
        body = client.get(url, headers=_auth(USER_A)).json()
        ok &= _check("in-flight run reported", (body["run_in_flight"] or {}).get("analysis_run_id") == str(queued.id)
                     and body["run_in_flight"]["status"] == "parsing", str(body["run_in_flight"]))

        for label, value, expected in (
            ("suspended", {"id": int(INSTALLATION_ID), "suspended_at": "2026-10-01T00:00:00Z"}, "suspended"),
            ("different installation id", {"id": 1, "suspended_at": None}, "reinstalled"),
            ("GitHub 404", github_service.GitHubServiceError(404, "Not Found"), "not_installed"),
            ("GitHub 500", github_service.GitHubServiceError(500, "boom"), "unknown"),
            ("network error", ConnectionError("offline"), "unknown"),
            ("App key not configured", RuntimeError("GITHUB_PRIVATE_KEY_PATH is not configured"), "unknown"),
            ("body without id", {"message": "?"}, "unknown"),
        ):
            answer["value"] = value
            r = client.get(url, headers=_auth(USER_A))
            ok &= _check(f"installation: {label} -> {expected}", r.status_code == 200 and r.json()["installation"] == expected,
                         f"{r.status_code} {r.text[:200]}")

        empty = _fresh_repository(db, user, "get-empty", branch=None)
        answer["value"] = {"id": int(INSTALLATION_ID)}
        body = client.get(f"/api/v1/repos/{empty.id}/settings", headers=_auth(USER_A)).json()
        ok &= _check(
            "never analyzed: last_analysis null, 0 runs, no selected branch, defaults ON_PUSH / []",
            body["last_analysis"] is None and body["analysis_run_count"] == 0 and body["selected_branch"] is None
            and body["analysis_mode"] == "on_push" and body["ignore_patterns"] == [] and body["analysis_status"] == "not_analyzed",
            str(body),
        )
        return ok
    finally:
        patches.restore()
        db.close()


# --- group 3 ---------------------------------------------------------------------


def run_patch_matrix() -> bool:
    print("=== group 3: PATCH validation matrix ===")
    db = SessionLocal()
    try:
        user = _user(db, USER_A)
        repo = _fresh_repository(db, user, "patch")
        client = TestClient(app)
        url = f"/api/v1/repos/{repo.id}/settings"
        h = _auth(USER_A)

        def stored() -> tuple[str, list]:
            db.expire_all()
            return tuple(db.execute(text("select analysis_mode, ignore_patterns from repositories where id = :r"), {"r": repo.id}).one())

        r = client.patch(url, json={"analysis_mode": "manual"}, headers=h)
        ok = _check("mode -> manual: 200", r.status_code == 200 and r.json() == {"analysis_mode": "manual", "ignore_patterns": []}, r.text)
        ok &= _check("stored by member NAME ('MANUAL')", stored()[0] == "MANUAL", str(stored()))

        r = client.patch(url, json={"ignore_patterns": [" legacy/ ", "# old code", "", "/server/tests", "legacy"]}, headers=h)
        ok &= _check("patterns normalised", r.status_code == 200 and r.json()["ignore_patterns"] == ["legacy", "/server/tests"], r.text)
        ok &= _check("mode untouched by a patterns-only PATCH", r.json()["analysis_mode"] == "manual")
        ok &= _check("stored list", stored() == ("MANUAL", ["legacy", "/server/tests"]), str(stored()))

        for label, payload in (("empty body", {}), ("explicit nulls", {"analysis_mode": None, "ignore_patterns": None})):
            r = client.patch(url, json=payload, headers=h)
            ok &= _check(f"{label} -> 200 no-op", r.status_code == 200 and stored() == ("MANUAL", ["legacy", "/server/tests"]), r.text)

        before = stored()
        for label, payload in {
            "unknown field": {"ignorePatterns": ["x"]},
            "uppercase mode name": {"analysis_mode": "MANUAL"},
            "unknown mode": {"analysis_mode": "daily"},
            "patterns not a list": {"ignore_patterns": "legacy"},
            "non-string pattern": {"ignore_patterns": ["ok", 5]},
            "negation": {"ignore_patterns": ["!keep"]},
            "match-everything": {"ignore_patterns": ["**"]},
            "201-char pattern": {"ignore_patterns": ["a" * 201]},
            "51 patterns": {"ignore_patterns": [f"p{i}" for i in range(51)]},
            "501 raw lines": {"ignore_patterns": ["# c"] * 501},
            "control character": {"ignore_patterns": ["a\nb"]},
            "valid mode + invalid patterns (all-or-nothing)": {"analysis_mode": "on_push", "ignore_patterns": ["!x"]},
        }.items():
            r = client.patch(url, json=payload, headers=h)
            ok &= _check(f"{label} -> 422, nothing saved", r.status_code == 422 and stored() == before, f"{r.status_code} {stored()}")

        r = client.patch(url, json={"analysis_mode": "on_push", "ignore_patterns": []}, headers=h)
        ok &= _check("both at once; [] clears", r.status_code == 200 and stored() == ("ON_PUSH", []), f"{r.text} {stored()}")
        return ok
    finally:
        db.close()


# --- group 4 ---------------------------------------------------------------------


def run_ownership() -> bool:
    print("=== group 4: ownership / auth ===")
    db = SessionLocal()
    patches = _Patches()
    try:
        owner = _user(db, USER_A)
        _user(db, USER_B)
        repo = _fresh_repository(db, owner, "owned")
        _seed_run(db, repo, with_children=True)
        before = _counts(db, repo.id)
        patches.set(settings_module.run_repository_analysis, "delay", lambda run_id: None)
        patches.set(settings_module, "get_installation_token_for_user", lambda user: "fake")
        patches.set(github_service, "get_branch", lambda *a, **k: {"commit": {"sha": HEAD_SHA}})
        patches.set(github_service, "get_repository_installation", lambda owner, name: {"id": int(INSTALLATION_ID)})
        client = TestClient(app)
        base = f"/api/v1/repos/{repo.id}"
        hb = _auth(USER_B)
        ok = True
        for label, resp in (
            ("GET settings", client.get(f"{base}/settings", headers=hb)),
            ("PATCH settings", client.patch(f"{base}/settings", json={"analysis_mode": "manual"}, headers=hb)),
            ("DELETE", client.delete(base, headers=hb)),
            ("purge", client.post(f"{base}/purge", headers=hb)),
        ):
            ok &= _check(f"other user: {label} -> 404", resp.status_code == 404, f"{resp.status_code}")
        db.expire_all()
        ok &= _check("nothing changed", _counts(db, repo.id) == before and db.get(Repository, repo.id).analysis_mode is AnalysisMode.ON_PUSH)
        ok &= _check("no token -> 401", client.get(f"{base}/settings").status_code == 401)
        ok &= _check("unknown id -> 404", client.get(f"/api/v1/repos/{uuid.uuid4()}/settings", headers=_auth(USER_A)).status_code == 404)
        ok &= _check("malformed id -> 422", client.delete("/api/v1/repos/not-a-uuid", headers=_auth(USER_A)).status_code == 422)
        return ok
    finally:
        patches.restore()
        db.close()


# --- group 5 ---------------------------------------------------------------------


def run_delete() -> bool:
    print("=== group 5: DELETE /repos/{id} (disconnect) ===")
    db = SessionLocal()
    try:
        user = _user(db, USER_A)
        repo = _fresh_repository(db, user, "delete")
        repo_id = repo.id
        runs = [_seed_run(db, repo, sha="d1" * 20, with_children=True), _seed_run(db, repo, sha="d2" * 20, with_children=True)]
        run_ids = [r_.id for r_ in runs]  # read now: the objects die with their rows
        db.add(OnboardingProgress(user_id=user.id, repository_id=repo_id, step_id="module:src"))
        db.commit()
        live = _seed_run(db, repo, status=AnalysisRunStatus.QUEUED, sha="d3" * 20)
        client = TestClient(app)
        h = _auth(USER_A)
        before = _counts(db, repo_id)
        ok = _check("seeded every child table", all(v > 0 for v in before.values()), str(before))

        r = client.delete(f"/api/v1/repos/{repo_id}", headers=h)
        ok &= _check("in-flight QUEUED run -> 409", r.status_code == 409 and "queued" in r.json().get("detail", ""), r.text)
        ok &= _check("409 deleted nothing", _counts(db, repo_id) == before)

        stale = datetime.now(timezone.utc) - repo_service.IN_FLIGHT_WINDOW - timedelta(minutes=5)
        db.execute(text("update analysis_runs set created_at = :t, started_at = :t where id = :i"), {"t": stale, "i": live.id})
        db.commit()
        ok &= _check("a stale non-terminal run is not 'in flight'", repo_service.get_in_flight_run(db, repo_id) is None)

        r = client.delete(f"/api/v1/repos/{repo_id}", headers=h)
        ok &= _check("204", r.status_code == 204 and r.content == b"", f"{r.status_code} {r.text}")
        after = _counts(db, repo_id)
        ok &= _check("every child table is empty for the repository", all(v == 0 for v in after.values()), str(after))
        ok &= _check("debt issues of the seeded runs gone", _debt_for_runs(db, run_ids) == 0)
        ok &= _check("repository row gone", db.get(Repository, repo_id) is None)
        ok &= _check("user still exists", db.get(User, user.id) is not None)
        ok &= _check("second DELETE -> 404", client.delete(f"/api/v1/repos/{repo_id}", headers=h).status_code == 404)
        return ok
    finally:
        db.close()


# --- group 6 ---------------------------------------------------------------------


def run_purge() -> bool:
    print("=== group 6: POST /repos/{id}/purge ===")
    db = SessionLocal()
    patches = _Patches()
    try:
        user = _user(db, USER_A)
        repo = _fresh_repository(db, user, "purge")
        old_runs = [
            _seed_run(db, repo, sha="e1" * 20, with_children=True),
            _seed_run(db, repo, sha="e2" * 20, with_children=True),
            _seed_run(db, repo, status=AnalysisRunStatus.FAILED, sha="e3" * 20),
        ]
        old_run_ids = [o.id for o in old_runs]
        repo.last_analyzed_commit_sha = "e2" * 20
        repo.analysis_status = RepositoryAnalysisStatus.COMPLETED
        repo.analysis_mode = AnalysisMode.MANUAL
        repo.ignore_patterns = ["legacy"]
        db.add(OnboardingProgress(user_id=user.id, repository_id=repo.id, step_id="module:src"))
        db.commit()

        delays: list[str] = []
        branch_calls: list[str] = []

        def fake_branch(token, owner, name, branch):
            branch_calls.append(branch)
            return {"commit": {"sha": HEAD_SHA}}

        patches.set(settings_module.run_repository_analysis, "delay", lambda run_id: delays.append(run_id))
        patches.set(settings_module, "get_installation_token_for_user", lambda u: "fake-token")
        patches.set(github_service, "get_branch", fake_branch)
        client = TestClient(app)
        h = _auth(USER_A)
        url = f"/api/v1/repos/{repo.id}/purge"

        r = client.post(url, headers=h)
        ok = _check("202", r.status_code == 202, r.text)
        body = r.json()
        ok &= _check("deleted_run_count == 3", body.get("deleted_run_count") == 3, str(body))
        db.expire_all()
        runs = db.execute(select(AnalysisRun).where(AnalysisRun.repository_id == repo.id)).scalars().all()
        ok &= _check("exactly one run row left", len(runs) == 1, str(len(runs)))
        new = runs[0]
        ok &= _check("it is the new QUEUED MANUAL run at GitHub's head of the selected branch",
                     new.status is AnalysisRunStatus.QUEUED and new.trigger_source is AnalysisRunTriggerSource.MANUAL
                     and new.commit_sha == HEAD_SHA and new.branch == "main" and branch_calls == ["main"],
                     f"{new.status} {new.trigger_source} {new.commit_sha} {branch_calls}")
        ok &= _check("response carries that run", body["analysis_run"]["id"] == str(new.id) and body["status"] == "queued")
        ok &= _check("enqueued once with its id", delays == [str(new.id)], str(delays))
        counts = _counts(db, repo.id)
        ok &= _check("old facts gone (files/symbols/edges/endpoints/chunks = 0)",
                     all(counts[t] == 0 for t in ("files", "symbols", "dependency_edges", "api_endpoints", "rag_chunks", "debt_issues")),
                     str(counts))
        ok &= _check("old debt issues gone", _debt_for_runs(db, old_run_ids) == 0)
        ok &= _check("branches and onboarding ticks kept", counts["branches"] == 2 and counts["onboarding_progress"] == 1, str(counts))
        repo_row = db.get(Repository, repo.id)
        ok &= _check("last_analyzed_commit_sha reset, status QUEUED",
                     repo_row.last_analyzed_commit_sha is None and repo_row.analysis_status is RepositoryAnalysisStatus.QUEUED)
        ok &= _check("settings kept", repo_row.analysis_mode is AnalysisMode.MANUAL and repo_row.ignore_patterns == ["legacy"])

        r = client.post(url, headers=h)
        ok &= _check("second purge while the new run is QUEUED -> 409", r.status_code == 409, r.text)
        ok &= _check("409 changed nothing", _counts(db, repo.id)["analysis_runs"] == 1 and len(delays) == 1)

        # Mark it done, then exercise the failure paths that must delete nothing.
        db.execute(text("update analysis_runs set status = 'COMPLETED', completed_at = now() where repository_id = :r"), {"r": repo.id})
        db.commit()

        def gh_error(*a, **k):
            raise github_service.GitHubServiceError(500, "boom")

        patches.set(github_service, "get_branch", gh_error)
        r = client.post(url, headers=h)
        ok &= _check("GitHub error -> 502, nothing deleted", r.status_code == 502 and _counts(db, repo.id)["analysis_runs"] == 1, r.text)

        patches.set(github_service, "get_branch", fake_branch)
        db.execute(text("update branches set is_selected = false where repository_id = :r"), {"r": repo.id})
        db.commit()
        r = client.post(url, headers=h)
        ok &= _check("no selected branch -> 400, nothing deleted", r.status_code == 400 and _counts(db, repo.id)["analysis_runs"] == 1, r.text)
        db.execute(text("update branches set is_selected = true where repository_id = :r and name = 'main'"), {"r": repo.id})
        db.commit()

        def broker_down(run_id):
            raise ConnectionError("redis down")

        patches.set(settings_module.run_repository_analysis, "delay", broker_down)
        r = client.post(url, headers=h)
        ok &= _check("broker down -> 503 saying the old runs are gone", r.status_code == 503 and r.json().get("deleted_run_count") == 1, r.text)
        db.expire_all()
        runs = db.execute(select(AnalysisRun).where(AnalysisRun.repository_id == repo.id)).scalars().all()
        ok &= _check("the new row is FAILED with a message, not stuck QUEUED",
                     len(runs) == 1 and runs[0].status is AnalysisRunStatus.FAILED and "enqueue" in (runs[0].error_message or ""),
                     str([(x.status, x.error_message) for x in runs]))
        return ok
    finally:
        patches.restore()
        db.close()


# --- group 7 ---------------------------------------------------------------------


def run_concurrent_purge() -> bool:
    print("=== group 7: two simultaneous purges -> one 202, one 409 ===")
    db = SessionLocal()
    patches = _Patches()
    try:
        user = _user(db, USER_A)
        repo = _fresh_repository(db, user, "purge-race")
        _seed_run(db, repo, with_children=True)
        delays: list[str] = []
        barrier = threading.Barrier(2)

        def slow_branch(*a, **k):
            barrier.wait()  # both requests pass the pre-check before either takes the lock
            return {"commit": {"sha": HEAD_SHA}}

        patches.set(settings_module.run_repository_analysis, "delay", lambda run_id: delays.append(run_id))
        patches.set(settings_module, "get_installation_token_for_user", lambda u: "fake-token")
        patches.set(github_service, "get_branch", slow_branch)
        statuses: list[int] = []
        errors: list[str] = []

        def worker():
            try:
                statuses.append(TestClient(app).post(f"/api/v1/repos/{repo.id}/purge", headers=_auth(USER_A)).status_code)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        ok = _check("no request crashed", not errors, str(errors))
        ok &= _check("one 202 and one 409", sorted(statuses) == [202, 409], str(statuses))
        ok &= _check("exactly one run row afterwards", _counts(db, repo.id)["analysis_runs"] == 1)
        ok &= _check("exactly one enqueue", len(delays) == 1, str(delays))
        return ok
    finally:
        patches.restore()
        db.close()


# --- group 8 ---------------------------------------------------------------------

_EXTRA_FILES = {
    "legacy/old_api.py": b"def old():\n    return 1\n",
    "legacy/deep/util.py": b"X = 1\n",
    "server/tests/test_items.py": b"def test_x():\n    assert True\n",
    "server/main.py": b"print('kept')\n",
    "lib/server/tests/helper.py": b"Y = 2\n",  # not under the *root* server/tests
    "docs/notes.md": b"# notes\n",
}
_IGNORED = {"legacy/old_api.py", "legacy/deep/util.py", "server/tests/test_items.py"}


def _fixture_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        entries = {rel: (FASTAPI_FIXTURES / rel).read_bytes() for rel in FASTAPI_FILES} | _EXTRA_FILES
        for rel, data in entries.items():
            info = tarfile.TarInfo(name=f"smoke-settings-pipeline-abc123/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def run_pipeline_ignore() -> bool:
    print("=== group 8: saved ignore patterns reach the real ingestion via the real task ===")
    db = SessionLocal()
    patches = _Patches()
    try:
        user = _user(db, USER_A)
        repo = _fresh_repository(db, user, "pipeline")
        archive = _fixture_archive()

        def fake_download(*, dest_path: Path, **_kwargs) -> int:
            dest_path.write_bytes(archive)
            return len(archive)

        patches.set(github_service, "download_repository_archive", fake_download)
        patches.set(github_service, "get_installation_access_token", lambda installation_id: {"token": "fake"})
        patches.set(analysis_tasks, "publish_progress_event", lambda *a, **k: None)
        patches.set(analysis_tasks, "index_analysis_run", lambda *a, **k: 0)
        patches.set(analysis_tasks.insight_service, "generate_insights", lambda *a, **k: [])
        client = TestClient(app)

        def analyze() -> tuple[str, set[str], int]:
            run = create_queued_analysis_run(db, repository_id=repo.id, branch="main", commit_sha="f0" * 20,
                                             trigger_source=AnalysisRunTriggerSource.MANUAL)
            result = analysis_tasks.run_repository_analysis(str(run.id))
            db.expire_all()
            paths = set(db.execute(select(File.path).where(File.analysis_run_id == run.id)).scalars())
            return result["status"], paths, db.get(AnalysisRun, run.id).file_count

        r = client.patch(f"/api/v1/repos/{repo.id}/settings", json={"ignore_patterns": ["legacy/", "/server/tests"]}, headers=_auth(USER_A))
        ok = _check("patterns saved via PATCH", r.status_code == 200 and r.json()["ignore_patterns"] == ["legacy", "/server/tests"], r.text)
        status_1, with_patterns, count_1 = analyze()
        ok &= _check("run completed", status_1 == "completed", status_1)
        ok &= _check("ignored files absent from the run's files", not (with_patterns & _IGNORED), str(sorted(with_patterns & _IGNORED)))
        expected_kept = set(FASTAPI_FILES) | (set(_EXTRA_FILES) - _IGNORED)
        ok &= _check("every other file present (incl. lib/server/tests — not under the root folder)",
                     with_patterns == expected_kept, f"missing={sorted(expected_kept - with_patterns)} extra={sorted(with_patterns - expected_kept)}")

        client.patch(f"/api/v1/repos/{repo.id}/settings", json={"ignore_patterns": []}, headers=_auth(USER_A))
        status_2, without, count_2 = analyze()
        ok &= _check("after clearing: the next run has them back", status_2 == "completed" and _IGNORED <= without, str(sorted(without)))
        ok &= _check("file_count difference == number of ignored files", count_2 - count_1 == len(_IGNORED), f"{count_1} -> {count_2}")
        return ok
    finally:
        patches.restore()
        db.close()


def main() -> int:
    results = {
        "patterns (pure)": run_patterns(),
        "GET settings": run_get_settings(),
        "PATCH matrix": run_patch_matrix(),
        "ownership": run_ownership(),
        "DELETE (disconnect)": run_delete(),
        "purge": run_purge(),
        "concurrent purge": run_concurrent_purge(),
        "pipeline ignore": run_pipeline_ignore(),
    }
    print("\n=== summary ===")
    for name, passed in results.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
