"""Manual smoke test for Phase 7.5's RAG indexing orchestration
(`app/rag/indexing.py::index_analysis_run`) and its wiring into
`app/workers/tasks/analysis_tasks.py::run_repository_analysis`.

`app.rag.indexing.embed_documents` (the name `index_analysis_run` calls
bare, imported via `from app.rag.embeddings import embed_documents`) is
what every group below monkeypatches — patching `app.rag.embeddings.embed_documents`
instead would silently do nothing, since `indexing.py` already bound its
own module-level name to the original function object at import time. Same
technique `smoke_analysis_tasks.py`/`smoke_rag_providers.py` already use for
their own imported-by-name targets.

Five groups:

1. `run_fk_resolution_and_fidelity()` (gating) — real `fastapi_flask`
   fixture (SYMBOL/API_ENDPOINT/FILE_OVERVIEW chunk-type coverage) through
   the real pipeline + `persist_analysis_run`, then `index_analysis_run`
   with a fast random-vector stub in place of the real model. Asserts row
   count, and for one known SYMBOL chunk (`register`, reused from
   `smoke_chunking.py`'s own hand-verified fixture knowledge) exact
   `file_id`/`symbol_id`/`chunk_type`/`chunk_metadata`/`line_start`/
   `line_end` fidelity against its draft; asserts FILE_OVERVIEW rows never
   get a `symbol_id`.
2. `run_pruning_correctness()` (gating) — indexes two successive
   `AnalysisRun`s for the same repository. Asserts the first run's
   `RagChunk` rows are gone after the second run indexes, and the
   repository's total row count equals exactly the second run's own count.
3. `run_never_zero_on_forced_failure()` (gating) — indexes a first run
   successfully, then forces `embed_documents` to raise for a second run.
   Asserts the exception propagates out of `index_analysis_run` (it doesn't
   swallow its own failures — only the Celery task does) and the
   repository's `RagChunk` rows are byte-for-byte unchanged: nothing
   partially inserted, and critically, nothing pruned either (the whole
   point of embedding *before* touching the DB).
4. `run_task_level_non_fatal_indexing_failure()` (gating) — the same forced
   failure, but through the real `run_repository_analysis` Celery task
   (fixture ingestion + fake GitHub token, same monkeypatch shape
   `smoke_analysis_tasks.py` already uses). Asserts the task still reaches
   `COMPLETED`, `File`/`Symbol` rows are unaffected, the recorded event
   order includes `indexing` between `building_graph` and `completed`, and
   zero `RagChunk` rows exist for that run (design decision #8: indexing
   failure must never cost the core analysis, but it also shouldn't
   silently fabricate partial chunk coverage).
5. `run_live_bonus()` (non-gating) — real embedding model, a real
   freshly-triggered run via `run_repository_analysis.delay()` in Celery
   eager mode against `Nirmit1705/HireSight` (this codebase's established
   Phase 6+ sample repo, also 7.2's own chunking live-bonus target).
   Asserts real `RagChunk` rows exist, each embedding is really 1024-dim,
   and prints the `chunk_type` distribution for a human to eyeball against
   7.2's own live-bonus counts (not hard-asserted — real repo content
   drifts over time).

Run: `uv run python scripts/smoke_rag_indexing.py` (from `server/`). Groups
1-4 are offline and fast. Group 5 downloads the embedding model on first run
(~1GB, cached afterward) and needs a live GitHub App installation for
`Nirmit1705/HireSight` — skips cleanly if that installation isn't found.
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.workers.tasks.analysis_tasks as analysis_tasks  # noqa: E402
from app.analysis import ingestion as ingestion_module  # noqa: E402
from app.analysis import pipeline  # noqa: E402
from app.analysis.chunking import RagChunkType  # noqa: E402
from app.analysis.ingestion import IngestedFile, IngestionResult  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRunStatus, AnalysisRunTriggerSource  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.rag_chunk import EMBEDDING_DIM, RagChunk  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.rag import indexing  # noqa: E402
from app.services import github_service, insight_service  # noqa: E402
from app.services.repo_service import (  # noqa: E402
    create_queued_analysis_run,
    finalize_analysis_run_completed,
    persist_analysis_run,
)
from app.workers.celery_app import celery_app  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

SERVER_ROOT = Path(__file__).resolve().parent.parent  # server/
FASTAPI_FIXTURES_DIR = SERVER_ROOT / "scripts" / "fixtures" / "fastapi_flask"
FASTAPI_FIXTURE_REL_PATHS = ["simple_app.py", "app/api/v1/auth.py", "app/api/v1/items.py", "app/api/v1/router.py"]

DOGFOOD_USER_EMAIL = "smoke-rag-indexing@local.test"

# This codebase's established Phase 6+ sample repo (server/CLAUDE.md) — also
# Phase 7.2's own smoke_chunking.py live-bonus target.
LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


class _Patch:
    """Tiny monkeypatch-and-restore helper — same shape as every other
    smoke script's own local copy (`smoke_analysis_tasks.py`,
    `smoke_rag_providers.py`); duplicated rather than imported, matching
    this codebase's established small-helper convention."""

    def __init__(self, obj, attr: str, replacement):
        self._obj = obj
        self._attr = attr
        self._original = getattr(obj, attr)
        setattr(obj, attr, replacement)

    def restore(self) -> None:
        setattr(self._obj, self._attr, self._original)


def _stub_insight_provider_class(response_text: str):
    """Phase 8.4: group 4 below now reaches `GENERATING_INSIGHTS` too —
    patches `insight_service.GroqProvider` to this stub so it stays
    offline (same rationale as `smoke_analysis_tasks.py`'s own copy of
    this helper: a real Groq call would break this group's documented
    "offline and fast" classification)."""

    class _Stub:
        def complete(self, *, system, messages, response_schema=None):
            return response_text

    return _Stub


def _random_vector() -> list[float]:
    return [random.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)]


def _stub_embed_documents(texts: list[str]) -> list[list[float]]:
    """Fast stand-in for the real embedding model — one random 1024-dim
    vector per text, no model load. Dimension has to be exactly right:
    `RagChunk.embedding` is a fixed-width `vector(1024)` column."""
    return [_random_vector() for _ in texts]


def _fastapi_ingested_file(rel_path: str) -> IngestedFile:
    absolute = FASTAPI_FIXTURES_DIR / rel_path
    return IngestedFile(path=rel_path, absolute_path=absolute, size_bytes=absolute.stat().st_size)


def _fastapi_ingestion_result() -> IngestionResult:
    files = [_fastapi_ingested_file(rel) for rel in FASTAPI_FIXTURE_REL_PATHS]
    return IngestionResult(root_dir=FASTAPI_FIXTURES_DIR, files=files, ignored_paths=[])


@contextmanager
def _fake_ingest_repository(**kwargs):
    yield _fastapi_ingestion_result()


# --- group 1: FK resolution + row/type/metadata fidelity --------------------------


def run_fk_resolution_and_fidelity(db: Session, repository: Repository) -> bool:
    print("=== group 1 (gating): FK resolution + row/type/metadata fidelity (stub embedding provider) ===")
    all_passed = True

    ingestion_result = _fastapi_ingestion_result()
    commit_sha = "ragindex10000000000000000000000000000000"[:40]
    result = pipeline.analyze_ingested(ingestion_result, commit_sha=commit_sha)

    _clear_previous_runs(db, repository)
    queued = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha, trigger_source=AnalysisRunTriggerSource.MANUAL)
    run, file_id_by_path, symbol_id_by_symbol = persist_analysis_run(db, analysis_run=queued, analysis_result=result)
    run = finalize_analysis_run_completed(db, analysis_run=run, repository=repository)

    patch = _Patch(indexing, "embed_documents", _stub_embed_documents)
    try:
        chunk_count = indexing.index_analysis_run(
            db,
            analysis_run=run,
            analysis_result=result,
            file_id_by_path=file_id_by_path,
            symbol_id_by_symbol=symbol_id_by_symbol,
        )
    finally:
        patch.restore()

    all_passed &= _check(
        "index_analysis_run returns len(result.rag_chunks)",
        chunk_count == len(result.rag_chunks), f"{chunk_count} vs {len(result.rag_chunks)}",
    )

    rows = db.execute(select(RagChunk).where(RagChunk.analysis_run_id == run.id)).scalars().all()
    all_passed &= _check(
        "RagChunk row count matches result.rag_chunks",
        len(rows) == len(result.rag_chunks), f"{len(rows)} vs {len(result.rag_chunks)}",
    )
    all_passed &= _check(
        "every persisted embedding is 1024-dim",
        all(len(r.embedding) == EMBEDDING_DIM for r in rows),
        str({len(r.embedding) for r in rows}),
    )

    rows_by_text = {r.chunk_text: r for r in rows}
    register_draft = next(
        (d for d in result.rag_chunks if d.chunk_type is RagChunkType.SYMBOL and d.symbol is not None and d.symbol.name == "register"),
        None,
    )
    all_passed &= _check("register() SYMBOL draft exists in this fixture", register_draft is not None)
    if register_draft is not None:
        register_row = rows_by_text.get(register_draft.chunk_text)
        all_passed &= _check("register SYMBOL row persisted with matching chunk_text", register_row is not None)
        if register_row is not None:
            all_passed &= _check(
                "register row file_id resolves to auth.py's real File row",
                register_row.file_id == file_id_by_path["app/api/v1/auth.py"],
            )
            all_passed &= _check(
                "register row symbol_id resolves to the real Symbol row",
                register_row.symbol_id == symbol_id_by_symbol[register_draft.symbol],
            )
            all_passed &= _check("register row chunk_type == SYMBOL", register_row.chunk_type is RagChunkType.SYMBOL)
            all_passed &= _check(
                "register row chunk_metadata matches its draft",
                register_row.chunk_metadata == register_draft.metadata,
                f"{register_row.chunk_metadata!r} vs {register_draft.metadata!r}",
            )
            all_passed &= _check(
                "register row line_start/line_end match its draft",
                (register_row.line_start, register_row.line_end) == (register_draft.line_start, register_draft.line_end),
            )

    file_overview_rows = [r for r in rows if r.chunk_type is RagChunkType.FILE_OVERVIEW]
    all_passed &= _check("at least one FILE_OVERVIEW row exists", len(file_overview_rows) > 0, str(len(file_overview_rows)))
    all_passed &= _check(
        "FILE_OVERVIEW rows never get a symbol_id",
        all(r.symbol_id is None for r in file_overview_rows),
        str([str(r.symbol_id) for r in file_overview_rows]),
    )

    return all_passed


# --- group 2: pruning correctness --------------------------------------------------


def run_pruning_correctness(db: Session, repository: Repository) -> bool:
    print("\n=== group 2 (gating): pruning a repository's old-run RagChunk rows on re-analysis ===")
    all_passed = True

    ingestion_result = _fastapi_ingestion_result()

    commit_sha_1 = "ragindex20000000000000000000000000000001"[:40]
    result_1 = pipeline.analyze_ingested(ingestion_result, commit_sha=commit_sha_1)
    _clear_previous_runs(db, repository)
    queued_1 = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha_1, trigger_source=AnalysisRunTriggerSource.MANUAL)
    run_1, file_id_by_path_1, symbol_id_by_symbol_1 = persist_analysis_run(
        db, analysis_run=queued_1, analysis_result=result_1
    )
    run_1 = finalize_analysis_run_completed(db, analysis_run=run_1, repository=repository)

    patch = _Patch(indexing, "embed_documents", _stub_embed_documents)
    try:
        indexing.index_analysis_run(
            db, analysis_run=run_1, analysis_result=result_1,
            file_id_by_path=file_id_by_path_1, symbol_id_by_symbol=symbol_id_by_symbol_1,
        )
        run_1_chunk_count = db.execute(
            select(func.count()).select_from(RagChunk).where(RagChunk.analysis_run_id == run_1.id)
        ).scalar_one()
        all_passed &= _check("first run indexed at least one chunk", run_1_chunk_count > 0, str(run_1_chunk_count))

        commit_sha_2 = "ragindex20000000000000000000000000000002"[:40]
        result_2 = pipeline.analyze_ingested(ingestion_result, commit_sha=commit_sha_2)
        queued_2 = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha_2, trigger_source=AnalysisRunTriggerSource.MANUAL)
        run_2, file_id_by_path_2, symbol_id_by_symbol_2 = persist_analysis_run(
            db, analysis_run=queued_2, analysis_result=result_2
        )
        run_2 = finalize_analysis_run_completed(db, analysis_run=run_2, repository=repository)

        indexing.index_analysis_run(
            db, analysis_run=run_2, analysis_result=result_2,
            file_id_by_path=file_id_by_path_2, symbol_id_by_symbol=symbol_id_by_symbol_2,
        )
    finally:
        patch.restore()

    run_1_remaining = db.execute(
        select(func.count()).select_from(RagChunk).where(RagChunk.analysis_run_id == run_1.id)
    ).scalar_one()
    all_passed &= _check(
        "first run's RagChunk rows are pruned once the second run indexes", run_1_remaining == 0, str(run_1_remaining)
    )

    total_for_repo = db.execute(
        select(func.count()).select_from(RagChunk).where(RagChunk.repository_id == repository.id)
    ).scalar_one()
    run_2_count = db.execute(
        select(func.count()).select_from(RagChunk).where(RagChunk.analysis_run_id == run_2.id)
    ).scalar_one()
    all_passed &= _check(
        "repository's total RagChunk count == the second run's own count",
        total_for_repo == run_2_count and run_2_count > 0, f"{total_for_repo} vs {run_2_count}",
    )

    return all_passed


# --- group 3: never end up with fewer chunks than the last successful run ---------


def run_never_zero_on_forced_failure(db: Session, repository: Repository) -> bool:
    print("\n=== group 3 (gating): forced mid-embedding failure leaves the prior run's chunks untouched ===")
    all_passed = True

    ingestion_result = _fastapi_ingestion_result()

    commit_sha_1 = "ragindex30000000000000000000000000000001"[:40]
    result_1 = pipeline.analyze_ingested(ingestion_result, commit_sha=commit_sha_1)
    _clear_previous_runs(db, repository)
    queued_1 = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha_1, trigger_source=AnalysisRunTriggerSource.MANUAL)
    run_1, file_id_by_path_1, symbol_id_by_symbol_1 = persist_analysis_run(
        db, analysis_run=queued_1, analysis_result=result_1
    )
    run_1 = finalize_analysis_run_completed(db, analysis_run=run_1, repository=repository)

    stub_patch = _Patch(indexing, "embed_documents", _stub_embed_documents)
    try:
        indexing.index_analysis_run(
            db, analysis_run=run_1, analysis_result=result_1,
            file_id_by_path=file_id_by_path_1, symbol_id_by_symbol=symbol_id_by_symbol_1,
        )
    finally:
        stub_patch.restore()

    before_ids = set(db.execute(select(RagChunk.id).where(RagChunk.repository_id == repository.id)).scalars().all())
    all_passed &= _check("first run left at least one chunk before the forced failure", len(before_ids) > 0, str(len(before_ids)))

    commit_sha_2 = "ragindex30000000000000000000000000000002"[:40]
    result_2 = pipeline.analyze_ingested(ingestion_result, commit_sha=commit_sha_2)
    queued_2 = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha_2, trigger_source=AnalysisRunTriggerSource.MANUAL)
    run_2, file_id_by_path_2, symbol_id_by_symbol_2 = persist_analysis_run(
        db, analysis_run=queued_2, analysis_result=result_2
    )
    run_2 = finalize_analysis_run_completed(db, analysis_run=run_2, repository=repository)

    def _raising_embed(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("simulated embedding failure")

    raise_patch = _Patch(indexing, "embed_documents", _raising_embed)
    raised = False
    try:
        indexing.index_analysis_run(
            db, analysis_run=run_2, analysis_result=result_2,
            file_id_by_path=file_id_by_path_2, symbol_id_by_symbol=symbol_id_by_symbol_2,
        )
    except RuntimeError:
        raised = True
    finally:
        raise_patch.restore()
    all_passed &= _check("index_analysis_run propagates the embedding failure (doesn't swallow it)", raised)

    after_ids = set(db.execute(select(RagChunk.id).where(RagChunk.repository_id == repository.id)).scalars().all())
    all_passed &= _check(
        "repository's RagChunk rows are byte-for-byte unchanged (nothing inserted, nothing pruned)",
        after_ids == before_ids, f"before={len(before_ids)} after={len(after_ids)}",
    )

    return all_passed


# --- group 4: task-level non-fatal indexing failure ---------------------------------


def run_task_level_non_fatal_indexing_failure() -> bool:
    print("\n=== group 4 (gating): a forced indexing failure at the task level never fails the run ===")
    recorded_events: list[str] = []

    def fake_publish(analysis_run_id, event):
        recorded_events.append(event.status)

    def _raising_embed(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("simulated embedding failure (task-level)")

    patches = [
        _Patch(github_service, "get_installation_access_token", lambda installation_id: {"token": "fake-token"}),
        _Patch(ingestion_module, "ingest_repository", _fake_ingest_repository),
        _Patch(analysis_tasks, "publish_progress_event", fake_publish),
        _Patch(indexing, "embed_documents", _raising_embed),
        _Patch(insight_service, "GroqProvider", _stub_insight_provider_class('{"insights": []}')),
    ]

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-rag-indexing-task-level", owner="smoke-rag-indexing", name="task-level"
        )
        _clear_previous_runs(db, repository)
        queued = create_queued_analysis_run(
            db, repository_id=repository.id, branch="main",
            commit_sha="ragindex40000000000000000000000000000000"[:40],
            trigger_source=AnalysisRunTriggerSource.MANUAL,
        )

        result = analysis_tasks.run_repository_analysis(str(queued.id))

        all_passed = True
        all_passed &= _check(
            "task still returns status completed despite the forced indexing failure",
            result.get("status") == "completed", str(result),
        )

        db.refresh(queued)
        all_passed &= _check("AnalysisRun.status == COMPLETED", queued.status is AnalysisRunStatus.COMPLETED)

        file_count = db.execute(
            select(func.count()).select_from(File).where(File.analysis_run_id == queued.id)
        ).scalar_one()
        all_passed &= _check("File rows persisted despite the indexing failure", file_count > 0, str(file_count))

        all_passed &= _check(
            "event order == cloning/parsing/analyzing/building_graph/indexing/generating_insights/completed",
            recorded_events
            == ["cloning", "parsing", "analyzing", "building_graph", "indexing", "generating_insights", "completed"],
            str(recorded_events),
        )

        chunk_count = db.execute(
            select(func.count()).select_from(RagChunk).where(RagChunk.analysis_run_id == queued.id)
        ).scalar_one()
        all_passed &= _check(
            "zero RagChunk rows persisted for this run (embedding failed before any insert)",
            chunk_count == 0, str(chunk_count),
        )

        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 5 (non-gating): live HireSight bonus, real embedding model --------------


def run_live_bonus() -> bool:
    print(f"\n=== group 5 (non-gating): live run_repository_analysis.delay() against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} — real embedding model ===")
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
                github_repo_id=f"smoke-rag-indexing-live-{LIVE_TEST_OWNER}-{LIVE_TEST_REPO}".lower(),
                owner=LIVE_TEST_OWNER, name=LIVE_TEST_REPO,
            )
            repository.installation_id = str(installation["id"])
            db.add(repository)
            db.commit()

            _clear_previous_runs(db, repository)
            queued = create_queued_analysis_run(
                db, repository_id=repository.id, branch="main", commit_sha=commit_sha,
                trigger_source=AnalysisRunTriggerSource.MANUAL,
            )

            celery_app.conf.task_always_eager = True
            async_result = analysis_tasks.run_repository_analysis.delay(str(queued.id))
            # Generous timeout: first run on a cold machine downloads the
            # ~1GB embedding model from the Hugging Face Hub.
            result = async_result.get(timeout=600)

            db.refresh(queued)
            rows = db.execute(select(RagChunk).where(RagChunk.analysis_run_id == queued.id)).scalars().all()
            type_counts = Counter(r.chunk_type.value for r in rows)
            print(
                f"  persisted: status={queued.status.value} rag_chunks={len(rows)} "
                f"by_type={dict(type_counts)} task_result={result}"
            )

            all_passed = True
            all_passed &= _check("task returns status completed", result.get("status") == "completed", str(result))
            all_passed &= _check("AnalysisRun.status == COMPLETED", queued.status is AnalysisRunStatus.COMPLETED)
            all_passed &= _check("at least one RagChunk row persisted", len(rows) > 0, str(len(rows)))
            all_passed &= _check(
                "every persisted embedding is really 1024-dim",
                all(len(r.embedding) == EMBEDDING_DIM for r in rows),
                str({len(r.embedding) for r in rows}),
            )
            return all_passed
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [ERROR] live bonus failed: {exc!r}")
        return False
    finally:
        celery_app.conf.task_always_eager = original_eager


def main() -> int:
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-rag-indexing-fastapi-flask", owner="smoke-rag-indexing", name="fastapi-flask-fixture"
        )

        r1 = run_fk_resolution_and_fidelity(db, repository)
        r2 = run_pruning_correctness(db, repository)
        r3 = run_never_zero_on_forced_failure(db, repository)

        _clear_previous_runs(db, repository)
    finally:
        db.close()

    r4 = run_task_level_non_fatal_indexing_failure()
    r5 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. FK resolution + fidelity:            {'PASS' if r1 else 'FAIL'}")
    print(f"2. Pruning correctness:                 {'PASS' if r2 else 'FAIL'}")
    print(f"3. Never-zero on forced failure:        {'PASS' if r3 else 'FAIL'}")
    print(f"4. Task-level non-fatal integration:    {'PASS' if r4 else 'FAIL'}")
    print(f"5. Live bonus (non-gating):              {'PASS' if r5 else 'FAIL (see above — may be a network/credentials issue)'}")
    return 0 if (r1 and r2 and r3 and r4) else 1


if __name__ == "__main__":
    sys.exit(main())
