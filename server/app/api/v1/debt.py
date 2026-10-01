"""`GET /repos/{id}/debt` (Phase 10.2): the Debt screen's data from the latest
completed run's persisted `DebtIssue` rows. Deterministic — no LLM here (the
per-issue AI recommendation is a separate lazy endpoint, 10.4).

No shared prefix on `router` — same per-file `APIRouter()` convention as
`dependencies.py`/`api_reference.py` (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.analysis_run import AnalysisRun
from app.models.debt_issue import DebtIssue
from app.models.repository import Repository
from app.models.user import User
from app.schemas.debt import DebtRead, DebtRecommendationRead
from app.services import analysis_read_service, debt_insight_service, debt_service, repo_service

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


@router.get("/repos/{repository_id}/debt/issues/{issue_id}/recommendation", response_model=DebtRecommendationRead)
def get_debt_recommendation(
    repository_id: uuid.UUID,
    issue_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DebtRecommendationRead:
    """An AI-written next step for one finding, generated on first view and then
    cached on the row (see `debt_insight_service`). The second GET-triggered
    `db.commit()` in this codebase, after `file_insight_service`'s. `recommendation`
    is `None` when generation failed — never a 5xx. 404 only when the repository
    is not the caller's or the finding does not belong to it (scoped through the
    finding's analysis run, so an id from an older run still resolves)."""
    repository = _repository_or_404(db, repository_id, current_user)
    issue = db.execute(
        select(DebtIssue)
        .join(AnalysisRun, AnalysisRun.id == DebtIssue.analysis_run_id)
        .where(DebtIssue.id == issue_id, AnalysisRun.repository_id == repository.id)
    ).scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found")

    return DebtRecommendationRead(
        issue_id=issue.id,
        recommendation=debt_insight_service.get_or_generate_recommendation(db, issue=issue),
    )
