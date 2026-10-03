"""`GET /repos/{id}/onboarding` and `POST /repos/{id}/onboarding/progress`
(Phase 10.10): the onboarding path for the latest completed run and the
current user's ticks on it. Deterministic — no LLM here (a step's prose is the
existing `GET .../architecture/insight`, fetched by the screen on demand).

No shared prefix on `router` — same per-file `APIRouter()` convention as
`debt.py`/`tracer.py` (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.repository import Repository
from app.models.user import User
from app.schemas.onboarding import OnboardingProgressRead, OnboardingProgressUpdate, OnboardingRead
from app.services import onboarding_service, repo_service

router = APIRouter(tags=["onboarding"])


# Duplicated per-file, matching `debt.py`/`tracer.py` (this codebase does not
# import another module's underscore-prefixed helpers).
def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


@router.get("/repos/{repository_id}/onboarding", response_model=OnboardingRead)
def get_onboarding(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OnboardingRead:
    """`analyzed=False` (nothing else populated) when the repository has never
    completed an analysis."""
    repository = _repository_or_404(db, repository_id, current_user)
    return onboarding_service.get_onboarding(db, repository, current_user)


@router.post("/repos/{repository_id}/onboarding/progress", response_model=OnboardingProgressRead)
def update_onboarding_progress(
    repository_id: uuid.UUID,
    body: OnboardingProgressUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OnboardingProgressRead:
    """Marks one step done (`done: true`, idempotent) or not done (`done:
    false`, idempotent). 409 when the repository has no completed analysis (no
    path exists yet); 404 for a step id that is not in the current path."""
    repository = _repository_or_404(db, repository_id, current_user)
    try:
        return onboarding_service.set_step_done(db, repository, current_user, body.step_id, body.done)
    except onboarding_service.NotAnalyzedError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Repository has not been analyzed yet") from None
    except onboarding_service.StepNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found") from None
