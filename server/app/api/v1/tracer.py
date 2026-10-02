"""Flow Tracer endpoints (Phase 10.6):

* `GET /repos/{id}/tracer?endpoint_id=` **or** `?file_id=` — the deterministic
  trace of the latest completed run (`services/trace_service.py`). Instant; no LLM.
* `GET /repos/{id}/tracer/notes?<same params>` — one AI sentence per traced file
  (`services/trace_narrative_service.py`), a separate call so the structure never
  waits on a model. `notes` is `None` when generation fails — never a 5xx.

Exactly one of the two start params, else `422`. An id that is not part of the
latest completed run (unknown, another repository's, or from a superseded run)
is `404`. A repository that has never completed an analysis returns
`analyzed=False` before any id is looked at.

No shared prefix on `router` — same per-file `APIRouter()` convention as
`debt.py`/`api_reference.py` (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.repository import Repository
from app.models.user import User
from app.schemas.tracer import TraceNotesRead, TracerRead
from app.services import analysis_read_service, repo_service, trace_narrative_service, trace_service

router = APIRouter(tags=["tracer"])


# Duplicated per-file, matching `debt.py`/`api_reference.py`.
def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


def _require_exactly_one(endpoint_id: uuid.UUID | None, file_id: uuid.UUID | None) -> None:
    if (endpoint_id is None) == (file_id is None):
        raise HTTPException(
            status_code=422,
            detail="Pass exactly one of endpoint_id or file_id",
        )


@router.get("/repos/{repository_id}/tracer", response_model=TracerRead)
def get_tracer(
    repository_id: uuid.UUID,
    endpoint_id: uuid.UUID | None = Query(default=None),
    file_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TracerRead:
    repository = _repository_or_404(db, repository_id, current_user)
    _require_exactly_one(endpoint_id, file_id)

    run = analysis_read_service.get_latest_completed_run(db, repository.id)
    if run is None:
        return TracerRead(analyzed=False)

    try:
        bundle = trace_service.build_trace(db, run, endpoint_id=endpoint_id, file_id=file_id)
    except trace_service.TraceTargetNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Trace start ({exc}) not found") from exc
    return trace_service.to_read(bundle)


@router.get("/repos/{repository_id}/tracer/notes", response_model=TraceNotesRead)
def get_tracer_notes(
    repository_id: uuid.UUID,
    endpoint_id: uuid.UUID | None = Query(default=None),
    file_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TraceNotesRead:
    repository = _repository_or_404(db, repository_id, current_user)
    _require_exactly_one(endpoint_id, file_id)

    run = analysis_read_service.get_latest_completed_run(db, repository.id)
    if run is None:
        return TraceNotesRead(analyzed=False)

    try:
        bundle = trace_service.build_trace(db, run, endpoint_id=endpoint_id, file_id=file_id)
    except trace_service.TraceTargetNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Trace start ({exc}) not found") from exc

    notes = trace_narrative_service.generate_trace_notes(trace_narrative_service.build_notes_facts(bundle))
    return TraceNotesRead(analyzed=True, analysis_run_id=run.id, notes=notes)
