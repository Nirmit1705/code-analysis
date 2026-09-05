"""Celery tasks for the analysis pipeline.

`run_repository_analysis` (Phase 5.3) orchestrates one already-`QUEUED`
`AnalysisRun`'s full lifecycle: `QUEUED -> CLONING -> PARSING -> ANALYZING ->
BUILDING_GRAPH -> COMPLETED`, each transition committed to the DB and
published as a Redis progress event (`services/progress_service.py`).
`INDEXING`/`GENERATING_INSIGHTS` are reserved for Phase 7/8 — not touched
here, even though the enum already defines them (Phase 4.1's own design,
so the state machine wouldn't need a later migration).

Creates and closes its own DB session (`SessionLocal`) — there is no
request/User context in a background task, and FastAPI's `get_db` generator
dependency isn't usable outside a request. Resolves its own GitHub
installation token directly from `Repository.installation_id` (plaintext —
see Known Issue flagged in `server/CLAUDE.md`'s Phase 5.1 write-up — no
`User` needed at all, unlike `github_auth_service.get_installation_token_for_user`,
which decrypts a *different* column off a `User` row).

Does not create the `AnalysisRun` row itself (Phase 5.1's
`create_queued_analysis_run`, normally called from Phase 5.4's
`POST /repos/{id}/analysis-runs`, does that) and does not duplicate Phase
5.1's persistence logic — `persist_analysis_run`/`mark_analysis_run_failed`
are called as-is, unmodified, at the two points this task's own job ends.

On any pipeline-level failure (token resolution, ingestion, parsing/
analysis, or persistence), the run is marked `FAILED`
(`mark_analysis_run_failed`) with a real `error_message` and a terminal
failure event is published — the task itself never re-raises. No Celery
autoretry is configured (Phase 5.2), so letting an exception escape here
would just look like a silently stuck job to anything watching Celery's own
result backend instead of this app's own `AnalysisRun.status`, which is the
actual source of truth.
"""

import logging
import uuid
from datetime import datetime, timezone

from app.analysis import ingestion, pipeline
from app.core.database import SessionLocal
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus
from app.models.repository import Repository
from app.schemas.analysis import AnalysisProgressEvent
from app.services import github_service
from app.services.progress_service import publish_progress_event
from app.services.repo_service import mark_analysis_run_failed, persist_analysis_run
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Maps pipeline.py's own on_stage(...) stage names (plain strings — that
# module has no idea AnalysisRunStatus exists) to the matching status. Only
# "parsing"/"analyzing" are real transitions driven by the callback; CLONING/
# BUILDING_GRAPH/COMPLETED are set directly by this task's own code below.
_STAGE_NAME_TO_STATUS = {
    "parsing": AnalysisRunStatus.PARSING,
    "analyzing": AnalysisRunStatus.ANALYZING,
}


@celery_app.task(name="run_repository_analysis")
def run_repository_analysis(analysis_run_id: str) -> dict:
    """Runs the Phase 3 analysis pipeline for `analysis_run_id` and persists
    it, driving `AnalysisRun.status` through its real lifecycle as it goes.

    `analysis_run_id` is a `str`, not a `uuid.UUID` — Celery's default JSON
    serializer can't round-trip a bare `UUID` object as a task argument.
    Expects the row to already exist and be `QUEUED` (created by Phase 5.1's
    `create_queued_analysis_run`); no concurrent-run guard or re-invocation
    check is performed (matches Phase 5.4's own "no concurrent-run
    prevention" scope note — not this task's job either).
    """
    run_uuid = uuid.UUID(analysis_run_id)
    db = SessionLocal()
    try:
        analysis_run = db.get(AnalysisRun, run_uuid)
        if analysis_run is None:
            # A bad/stale task argument, not a pipeline failure — there is no
            # valid row to mark FAILED against, so this is a distinct, purely
            # logged edge case rather than routed through mark_analysis_run_failed.
            logger.error("run_repository_analysis: no AnalysisRun with id=%s — nothing to do", analysis_run_id)
            return {"analysis_run_id": analysis_run_id, "status": "not_found"}

        repository = db.get(Repository, analysis_run.repository_id)
        if repository is None:
            logger.error(
                "run_repository_analysis: AnalysisRun %s references a missing Repository %s — nothing to do",
                analysis_run_id, analysis_run.repository_id,
            )
            return {"analysis_run_id": analysis_run_id, "status": "not_found"}

        def _transition(status: AnalysisRunStatus) -> None:
            """Commit-per-stage-transition, not one long transaction — the
            actual slow work (network download, Tree-sitter parsing) happens
            *between* these calls, never inside an open transaction."""
            analysis_run.status = status
            db.add(analysis_run)
            db.commit()
            publish_progress_event(
                analysis_run_id,
                AnalysisProgressEvent(
                    analysis_run_id=analysis_run_id, status=status.value, timestamp=datetime.now(timezone.utc)
                ),
            )

        def _on_stage(stage_name: str) -> None:
            status = _STAGE_NAME_TO_STATUS.get(stage_name)
            if status is not None:
                _transition(status)

        try:
            _transition(AnalysisRunStatus.CLONING)

            token_info = github_service.get_installation_access_token(repository.installation_id)
            token = token_info["token"]

            # The `with` block must wrap analyze_ingested too, not just
            # ingestion — the temp directory (and every file analyze_ingested
            # reads from disk) is deleted the moment this context manager
            # exits. PARSING/ANALYZING transition from *inside* this block,
            # via on_stage, while the files are still there.
            with ingestion.ingest_repository(
                installation_token=token,
                owner=repository.owner,
                repo=repository.name,
                commit_sha=analysis_run.commit_sha,
            ) as ingestion_result:
                result = pipeline.analyze_ingested(
                    ingestion_result, commit_sha=analysis_run.commit_sha, on_stage=_on_stage
                )

            _transition(AnalysisRunStatus.BUILDING_GRAPH)

            # Phase 5.1's own function — finalizes analysis_run to COMPLETED
            # and commits internally; not duplicated here.
            persist_analysis_run(db, analysis_run=analysis_run, analysis_result=result)

            publish_progress_event(
                analysis_run_id,
                AnalysisProgressEvent(
                    analysis_run_id=analysis_run_id,
                    status=AnalysisRunStatus.COMPLETED.value,
                    timestamp=datetime.now(timezone.utc),
                ),
            )
            return {"analysis_run_id": analysis_run_id, "status": analysis_run.status.value}

        except Exception as exc:  # noqa: BLE001 — a pipeline-level failure must never crash the worker
            logger.exception("run_repository_analysis: %s failed", analysis_run_id)
            error_message = str(exc)
            try:
                mark_analysis_run_failed(db, analysis_run=analysis_run, error_message=error_message)
            except Exception:  # noqa: BLE001 — marking the failure must never itself raise out of this task
                logger.exception(
                    "run_repository_analysis: %s failed AND mark_analysis_run_failed itself failed",
                    analysis_run_id,
                )
            else:
                try:
                    publish_progress_event(
                        analysis_run_id,
                        AnalysisProgressEvent(
                            analysis_run_id=analysis_run_id,
                            status=AnalysisRunStatus.FAILED.value,
                            timestamp=datetime.now(timezone.utc),
                            error_message=error_message,
                        ),
                    )
                except Exception:  # noqa: BLE001 — publishing the terminal event must not raise either
                    logger.exception(
                        "run_repository_analysis: %s marked FAILED but publishing the terminal event failed",
                        analysis_run_id,
                    )
            return {"analysis_run_id": analysis_run_id, "status": "failed"}
    finally:
        db.close()
