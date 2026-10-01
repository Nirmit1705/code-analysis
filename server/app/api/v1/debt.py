"""`GET /repos/{id}/debt` (Phase 10.2): the Debt screen's data from the latest
completed run's persisted `DebtIssue` rows. Deterministic — no LLM here (the
per-issue AI recommendation is a separate lazy endpoint, 10.4).

No shared prefix on `router` — same per-file `APIRouter()` convention as
`dependencies.py`/`api_reference.py` (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.repository import Repository
from app.models.user import User
from app.schemas.debt import DebtRead
from app.services import analysis_read_service, debt_service, repo_service

router = APIRouter(tags=["debt"])


# Duplicated per-file, matching `dependencies.py`/`api_reference.py` (this
# codebase does not import another module's underscore-prefixed helpers).
def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


@router.get("/repos/{repository_id}/debt", response_model=DebtRead)
def get_debt(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DebtRead:
    """Returns `analyzed=False` (no other field populated) when the repository
    has never completed an analysis."""
    repository = _repository_or_404(db, repository_id, current_user)

    current_run = analysis_read_service.get_latest_completed_run(db, repository.id)
    if current_run is None:
        return DebtRead(analyzed=False)

    return debt_service.build_debt_read(db, repository, current_run)
