"""Manual smoke test for Phase 5.3's `run_repository_analysis`
(`app/workers/tasks/analysis_tasks.py`) — the Celery task orchestrating an
`AnalysisRun`'s QUEUED -> CLONING -> PARSING -> ANALYZING -> BUILDING_GRAPH
-> COMPLETED/FAILED lifecycle over Phase 5.1's `create_queued_analysis_run`
/ `persist_analysis_run` / `mark_analysis_run_failed` and Phase 5.2's
Celery/Redis infrastructure.

Three gating groups, all offline (no live GitHub, no running Celery worker
— the task function is called directly, exactly as `@celery_app.task`
still allows):

1. `run_happy_path()` — `github_service.get_installation_access_token` and
   `ingestion.ingest_repository` monkeypatched (the latter yielding
   `smoke_analysis.py`'s own reused `fastapi_flask` fixture — same 4-file,
   8-route, 5-edge, 7-complexity-entry shape already independently verified
   there and in `smoke_apis_fastapi_flask.py`), `publish_progress_event`
   monkeypatched to record rather than actually hit Redis. Asserts the run
   reaches `COMPLETED`, the exact stage-event order, and persisted row
   counts matching the in-memory fixture.
2. `run_per_file_failure()` — same fixture, but the Python symbol extractor
   is monkeypatched to raise for one specific file (reusing Chunk 1's own
   per-file-resilience technique from `analysis/pipeline.py`, now exercised
   at the full task level rather than the bare pipeline level). Asserts the
   run still reaches `COMPLETED` — that one file simply has no symbols/
   complexity persisted, every other file unaffected.
3. `run_forced_failure()` — `ingestion.ingest_repository` monkeypatched to
   raise outright. Asserts the task returns `{"status": "failed"}` without
   raising, the row reaches `FAILED` with a real `error_message` and
   `completed_at`, zero child rows exist, and a terminal `failed` event was
   published.

A fourth, non-gating group:

4. `run_live_bonus()` — real GitHub, real Redis, no monkeypatching. Sets
   `celery_app.conf.task_always_eager = True` (scoped to this script's own
   process) so `run_repository_analysis.delay(...).get(...)` — the real
   Celery task-submission API, not a bare direct call — executes
   synchronously without needing a separately-running worker. Reuses the
   `Nirmit1705/SubTrack` live installation already established by
   `smoke_storage.py`/`smoke_analysis.py`'s own precedent (SubTrack is
   Phase 5's sample repo for live verification only — not a claim about any
   later phase's representative repo). Skips cleanly (and non-fatally to
   the suite) if that installation isn't available in this environment.

Run: `uv run python scripts/smoke_analysis_tasks.py` (from `server/`).
"""

from __future__ import annotations

import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.workers.tasks.analysis_tasks as analysis_tasks  # noqa: E402
from app.analysis import ingestion as ingestion_module  # noqa: E402
from app.analysis import pipeline  # noqa: E402
from app.analysis.ingestion import IngestedFile, IngestionResult  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository, RepositoryAnalysisStatus  # noqa: E402
from app.models.symbol import Symbol as SymbolRow  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import github_service, insight_service  # noqa: E402
from app.services.repo_service import create_queued_analysis_run  # noqa: E402
from app.workers.celery_app import celery_app  # noqa: E402

SERVER_ROOT = Path(__file__).resolve().parent.parent  # server/
FASTAPI_FIXTURES_DIR = SERVER_ROOT / "scripts" / "fixtures" / "fastapi_flask"
FASTAPI_FIXTURE_REL_PATHS = ["simple_app.py", "app/api/v1/auth.py", "app/api/v1/items.py", "app/api/v1/router.py"]

DOGFOOD_USER_EMAIL = "smoke-analysis-tasks@local.test"

# Phase 5's sample repo for live verification only (matches
# smoke_storage.py's/smoke_analysis.py's own precedent) — not a claim about
# any later phase's representative repo.
LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "SubTrack"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


# --- seeding helpers (same pattern as smoke_storage.py) -------------------------


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
            owner=owner,
            name=name,
            full_name=f"{owner}/{name}",
            github_repo_id=github_repo_id,
            default_branch="main",
            connected_user_id=user.id,
            # A placeholder — every scenario below monkeypatches
            # github_service.get_installation_access_token, so this value is
            # never actually sent to GitHub.
            installation_id="fake-installation-id",
        )
        db.add(repository)
        db.commit()
        db.refresh(repository)
    return repository


def _clear_previous_runs(db: Session, repository: Repository) -> None:
    db.execute(delete(AnalysisRun).where(AnalysisRun.repository_id == repository.id))
    db.commit()


def _ingested_file(rel_path: str) -> IngestedFile:
    absolute = FASTAPI_FIXTURES_DIR / rel_path
    return IngestedFile(path=rel_path, absolute_path=absolute, size_bytes=absolute.stat().st_size)


def _fixture_ingestion_result() -> IngestionResult:
    files = [_ingested_file(rel) for rel in FASTAPI_FIXTURE_REL_PATHS]
    return IngestionResult(root_dir=FASTAPI_FIXTURES_DIR, files=files, ignored_paths=[])


@contextmanager
def _fake_ingest_repository(**kwargs):
    yield _fixture_ingestion_result()


class _Patch:
    """Tiny monkeypatch-and-restore helper — every group below patches the
    same three module-level attributes (`github_service.get_installation_access_token`,
    `ingestion.ingest_repository`, `analysis_tasks.publish_progress_event`),
    always restored in a `finally`, so this avoids repeating that
    boilerplate three times."""

    def __init__(self, obj, attr: str, replacement):
        self._obj = obj
        self._attr = attr
        self._original = getattr(obj, attr)
        setattr(obj, attr, replacement)

    def restore(self) -> None:
        setattr(self._obj, self._attr, self._original)


def _stub_insight_provider_class(response_text: str):
    """Phase 8.4: every group below that now reaches `GENERATING_INSIGHTS`
    also patches `insight_service.GroqProvider` to this stub — without it,
    a "gating, offline" group here would silently start making a real Groq
    API call, breaking this script's own documented "no live GitHub, no
    reachable Celery broker" offline contract (a real network dependency is
    a materially different kind of "live" than Phase 7.5's local-model
    embedding calls, which stayed offline-compatible)."""

    class _Stub:
        def complete(self, *, system, messages, response_schema=None):
            return response_text

    return _Stub


# --- group 1: happy path ---------------------------------------------------------


def run_happy_path() -> bool:
    print("=== group 1: happy path — fixture ingestion, monkeypatched GitHub, full run to COMPLETED ===")
    recorded_events: list[str] = []

    def fake_publish(analysis_run_id, event):
        recorded_events.append(event.status)

    stub_insights_response = '{"insights": [{"tone": "info", "title": "Stub insight", "body": "A fixed, deterministic insight for offline verification."}]}'
    patches = [
        _Patch(github_service, "get_installation_access_token", lambda installation_id: {"token": "fake-token"}),
        _Patch(ingestion_module, "ingest_repository", _fake_ingest_repository),
        _Patch(analysis_tasks, "publish_progress_event", fake_publish),
        _Patch(insight_service, "GroqProvider", _stub_insight_provider_class(stub_insights_response)),
    ]

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-analysis-tasks-happy", owner="smoke-tasks", name="happy-path"
        )
        _clear_previous_runs(db, repository)
        queued = create_queued_analysis_run(
            db, repository_id=repository.id, branch="main",
            commit_sha="happy0000000000000000000000000000000000"[:40],
        )

        result = analysis_tasks.run_repository_analysis(str(queued.id))

        all_passed = True
        all_passed &= _check("task returns status completed", result.get("status") == "completed", str(result))
        all_passed &= _check(
            "event order == cloning/parsing/analyzing/building_graph/indexing/generating_insights/completed",
            recorded_events
            == ["cloning", "parsing", "analyzing", "building_graph", "indexing", "generating_insights", "completed"],
            str(recorded_events),
        )

        db.refresh(queued)
        all_passed &= _check("AnalysisRun.status == COMPLETED", queued.status is AnalysisRunStatus.COMPLETED)
        all_passed &= _check(
            "ai_insights persisted from the stub provider's response (Phase 8.4)",
            queued.ai_insights == [{"tone": "info", "title": "Stub insight", "body": "A fixed, deterministic insight for offline verification."}],
            str(queued.ai_insights),
        )

        file_count = db.execute(
            select(func.count()).select_from(File).where(File.analysis_run_id == queued.id)
        ).scalar_one()
        all_passed &= _check("File rows persisted: 4", file_count == 4, str(file_count))
        symbol_count = db.execute(
            select(func.count()).select_from(SymbolRow).where(SymbolRow.analysis_run_id == queued.id)
        ).scalar_one()
        all_passed &= _check("Symbol rows persisted: 7", symbol_count == 7, str(symbol_count))

        db.refresh(repository)
        all_passed &= _check(
            "Repository.analysis_status == COMPLETED",
            repository.analysis_status is RepositoryAnalysisStatus.COMPLETED,
        )
        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 2: per-file failure (symbol extraction) — resilience at the task level ----


def run_per_file_failure() -> bool:
    print("\n=== group 2: per-file symbol-extraction failure — task still reaches COMPLETED ===")
    bad_file = "app/api/v1/items.py"
    original_extractor = pipeline._SYMBOL_EXTRACTORS[Language.PYTHON]

    def flaky_extractor(source, *, file_path, language):
        if file_path == bad_file:
            raise RuntimeError("simulated symbol-extraction bug")
        return original_extractor(source, file_path=file_path, language=language)

    patches = [
        _Patch(github_service, "get_installation_access_token", lambda installation_id: {"token": "fake-token"}),
        _Patch(ingestion_module, "ingest_repository", _fake_ingest_repository),
        _Patch(analysis_tasks, "publish_progress_event", lambda analysis_run_id, event: None),
        _Patch(insight_service, "GroqProvider", _stub_insight_provider_class('{"insights": []}')),
    ]
    # _SYMBOL_EXTRACTORS is a dict, not an object _Patch's getattr/setattr can
    # target — patched and restored by hand instead.
    pipeline._SYMBOL_EXTRACTORS[Language.PYTHON] = flaky_extractor

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-analysis-tasks-per-file-failure", owner="smoke-tasks", name="per-file-failure"
        )
        _clear_previous_runs(db, repository)
        queued = create_queued_analysis_run(
            db, repository_id=repository.id, branch="main",
            commit_sha="perfile00000000000000000000000000000000"[:40],
        )

        result = analysis_tasks.run_repository_analysis(str(queued.id))

        all_passed = True
        all_passed &= _check(
            "task still returns status completed (one file's extractor failing doesn't abort the run)",
            result.get("status") == "completed", str(result),
        )
        db.refresh(queued)
        all_passed &= _check("AnalysisRun.status == COMPLETED", queued.status is AnalysisRunStatus.COMPLETED)

        file_count = db.execute(
            select(func.count()).select_from(File).where(File.analysis_run_id == queued.id)
        ).scalar_one()
        all_passed &= _check("File rows persisted: still 4 (the bad file still gets a File row)", file_count == 4, str(file_count))

        symbol_count = db.execute(
            select(func.count()).select_from(SymbolRow).where(SymbolRow.analysis_run_id == queued.id)
        ).scalar_one()
        all_passed &= _check(
            "Symbol rows persisted: 5 (7 minus items.py's 2 — list_items/get_item — skipped)",
            symbol_count == 5, str(symbol_count),
        )

        bad_file_row = db.execute(select(File).where(File.analysis_run_id == queued.id, File.path == bad_file)).scalar_one_or_none()
        bad_file_symbol_count = 0
        if bad_file_row is not None:
            bad_file_symbol_count = db.execute(
                select(func.count()).select_from(SymbolRow).where(SymbolRow.file_id == bad_file_row.id)
            ).scalar_one()
        all_passed &= _check(f"{bad_file}: zero Symbol rows", bad_file_symbol_count == 0, str(bad_file_symbol_count))

        return all_passed
    finally:
        db.close()
        pipeline._SYMBOL_EXTRACTORS[Language.PYTHON] = original_extractor
        for patch in patches:
            patch.restore()


# --- group 3: forced pipeline-level failure ---------------------------------------


def run_forced_failure() -> bool:
    print("\n=== group 3: forced failure (ingestion raises) — FAILED, no exception escapes, zero child rows ===")
    recorded_events: list[tuple] = []

    def fake_publish(analysis_run_id, event):
        recorded_events.append((event.status, event.error_message))

    @contextmanager
    def _raising_ingest(**kwargs):
        raise RuntimeError("simulated ingestion failure")
        yield  # pragma: no cover — unreachable, keeps this a generator function for @contextmanager

    patches = [
        _Patch(github_service, "get_installation_access_token", lambda installation_id: {"token": "fake-token"}),
        _Patch(ingestion_module, "ingest_repository", _raising_ingest),
        _Patch(analysis_tasks, "publish_progress_event", fake_publish),
    ]

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-analysis-tasks-forced-failure", owner="smoke-tasks", name="forced-failure"
        )
        _clear_previous_runs(db, repository)
        queued = create_queued_analysis_run(
            db, repository_id=repository.id, branch="main",
            commit_sha="failure00000000000000000000000000000000"[:40],
        )

        result = analysis_tasks.run_repository_analysis(str(queued.id))  # must not raise

        all_passed = True
        all_passed &= _check(
            "task returns status failed (did not raise)", result.get("status") == "failed", str(result)
        )

        db.refresh(queued)
        all_passed &= _check(
            "AnalysisRun.status == FAILED", queued.status is AnalysisRunStatus.FAILED, str(queued.status)
        )
        all_passed &= _check(
            "error_message set and mentions the underlying failure",
            queued.error_message is not None and "simulated ingestion failure" in queued.error_message,
            str(queued.error_message),
        )
        all_passed &= _check("completed_at set", queued.completed_at is not None)

        file_count = db.execute(
            select(func.count()).select_from(File).where(File.analysis_run_id == queued.id)
        ).scalar_one()
        all_passed &= _check("zero File rows persisted", file_count == 0, str(file_count))

        db.refresh(repository)
        all_passed &= _check(
            "Repository.analysis_status == FAILED", repository.analysis_status is RepositoryAnalysisStatus.FAILED
        )

        all_passed &= _check(
            "terminal event published: ('failed', <message>)",
            len(recorded_events) >= 1 and recorded_events[-1][0] == "failed" and recorded_events[-1][1] is not None,
            str(recorded_events),
        )
        all_passed &= _check(
            "a 'cloning' event was published before the failure (proves it was mid-flight, not immediate)",
            "cloning" in [e[0] for e in recorded_events],
            str(recorded_events),
        )
        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


def run_unknown_run_id() -> bool:
    print("\n=== bonus check: unknown analysis_run_id — logged + returned, no crash ===")
    result = analysis_tasks.run_repository_analysis(str(uuid.uuid4()))
    return _check("returns status not_found without raising", result.get("status") == "not_found", str(result))


# --- group 4 (non-gating): live SubTrack bonus, real Celery task-submission path ----


def run_live_bonus() -> bool:
    print(f"\n=== group 4 (non-gating): live run_repository_analysis.delay() against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
    original_eager = celery_app.conf.task_always_eager
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
        branch_info = github_service.get_branch(token, LIVE_TEST_OWNER, LIVE_TEST_REPO, "main")
        commit_sha = branch_info["commit"]["sha"]

        db = SessionLocal()
        try:
            user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
            repository = _get_or_create_repository(
                db, user,
                github_repo_id=f"smoke-analysis-tasks-live-{LIVE_TEST_OWNER}-{LIVE_TEST_REPO}".lower(),
                owner=LIVE_TEST_OWNER, name=LIVE_TEST_REPO,
            )
            # This one repository row needs its *real* installation_id (the
            # task resolves its own token from it) — overriding the fixture
            # groups' shared placeholder value.
            repository.installation_id = str(installation["id"])
            db.add(repository)
            db.commit()

            _clear_previous_runs(db, repository)
            queued = create_queued_analysis_run(
                db, repository_id=repository.id, branch="main", commit_sha=commit_sha
            )

            # Celery eager mode: .delay() executes synchronously in this
            # process via the real task-submission API, not a bare direct
            # call — no separately-running worker needed.
            celery_app.conf.task_always_eager = True
            async_result = analysis_tasks.run_repository_analysis.delay(str(queued.id))
            result = async_result.get(timeout=180)

            db.refresh(queued)
            file_count = db.execute(
                select(func.count()).select_from(File).where(File.analysis_run_id == queued.id)
            ).scalar_one()
            print(
                f"  persisted: files={file_count} status={queued.status.value} "
                f"debt_score={queued.debt_score} task_result={result}"
            )

            db.refresh(repository)
            all_passed = True
            all_passed &= _check("task returns status completed", result.get("status") == "completed", str(result))
            all_passed &= _check("AnalysisRun.status == COMPLETED", queued.status is AnalysisRunStatus.COMPLETED)
            all_passed &= _check("at least one File row persisted", file_count > 0, str(file_count))
            all_passed &= _check(
                "Repository.last_analyzed_commit_sha updated", repository.last_analyzed_commit_sha == commit_sha
            )
            all_passed &= _check(
                "Repository.analysis_status == COMPLETED",
                repository.analysis_status is RepositoryAnalysisStatus.COMPLETED,
            )
            return all_passed
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [ERROR] live bonus test failed: {exc!r}")
        return False
    finally:
        celery_app.conf.task_always_eager = original_eager


def main() -> int:
    r1 = run_happy_path()
    r2 = run_per_file_failure()
    r3 = run_forced_failure()
    r4 = run_unknown_run_id()
    r5 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. Happy path:                    {'PASS' if r1 else 'FAIL'}")
    print(f"2. Per-file failure (resilience): {'PASS' if r2 else 'FAIL'}")
    print(f"3. Forced failure:                {'PASS' if r3 else 'FAIL'}")
    print(f"4. Unknown analysis_run_id:       {'PASS' if r4 else 'FAIL'}")
    print(f"5. Live bonus (non-gating):       {'PASS' if r5 else 'FAIL (see above — may be a network/credentials issue)'}")
    # Matches smoke_storage.py's/smoke_analysis.py's own precedent: the live
    # network/credentials check doesn't gate the suite's exit code.
    return 0 if (r1 and r2 and r3 and r4) else 1


if __name__ == "__main__":
    sys.exit(main())
