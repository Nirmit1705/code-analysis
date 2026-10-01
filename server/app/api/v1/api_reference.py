"""`GET /repos/{id}/apis` and `GET /repos/{id}/apis/{endpoint_id}` (Phase
10.1): the API Reference screen's list (latest completed run's persisted
`ApiEndpoint` rows, with resource groups, path parameters, middleware and
"new since" markers) and one endpoint's detail (real source lines + the route
file's structural facts).

No shared prefix on `router` — same per-file `APIRouter()` convention as
`dependencies.py`/`architecture.py` (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.api_endpoint import ApiEndpoint
from app.models.repository import Repository
from app.models.user import User
from app.schemas.api_reference import ApiEndpointDetailRead, ApiReferenceRead
from app.services import analysis_read_service, api_reference_service, repo_service

router = APIRouter(tags=["api-reference"])


# Duplicated per-file, matching `dependencies.py`/`architecture.py` (this
# codebase does not import another module's underscore-prefixed helpers).
def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


@router.get("/repos/{repository_id}/apis", response_model=ApiReferenceRead)
def get_api_reference(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiReferenceRead:
    """Returns `analyzed=False` (no other field populated) when the
    repository has never completed an analysis."""
    repository = _repository_or_404(db, repository_id, current_user)

    current_run = analysis_read_service.get_latest_completed_run(db, repository.id)
    if current_run is None:
        return ApiReferenceRead(analyzed=False)

    return api_reference_service.build_api_reference(db, current_run)


@router.get("/repos/{repository_id}/apis/{endpoint_id}", response_model=ApiEndpointDetailRead)
def get_api_endpoint_detail(
    repository_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEndpointDetailRead:
    """Source lines + structural facts for one endpoint. Scoped by
    `ApiEndpoint.repository_id` (not just the latest run), so an id from a
    list fetched before a re-analysis still resolves. A GitHub failure never
    fails the call — `source_note` explains the missing code instead."""
    repository = _repository_or_404(db, repository_id, current_user)
    endpoint = db.execute(
        select(ApiEndpoint).where(ApiEndpoint.id == endpoint_id, ApiEndpoint.repository_id == repository.id)
    ).scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")
    return api_reference_service.build_detail(db, repository, current_user, endpoint)
