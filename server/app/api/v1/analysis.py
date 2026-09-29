"""Analysis-run REST endpoints (Phase 5.4): `POST /repos/{id}/analysis-runs`
(this file's first chunk — creates a `QUEUED` row and enqueues the real
Celery task for the first time from an actual API route) plus
`GET /analysis-runs/{id}` / `GET /repos/{id}/analysis-runs` (read-back,
added in this file's second chunk).

No shared prefix on `router` (like `github.py`'s `repos_router`) since these
paths span `/repos/{id}/analysis-runs` and `/analysis-runs/{id}` — not one
common prefix. Included into `api_router` in `router.py`, which applies
`/api/v1` for both.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.analysis_run import AnalysisRun, AnalysisRunTriggerSource
from app.models.branch import Branch
from app.models.repository import Repository
from app.models.user import User
from app.schemas.analysis import AnalysisRunRead
from app.services import github_service, repo_service
from app.services.github_auth_service import GitHubAuthError, get_installation_token_for_user
from app.workers.tasks.analysis_tasks import run_repository_analysis

router = APIRouter(tags=["analysis"])


# Duplicated from `app/api/v1/github.py`'s module-private helpers of the same
# shape, rather than imported — this codebase's established precedent for
# small cross-cutting helpers (e.g. `analysis/apis/express.py`'s duplicated
# `_resolve_by_suffix`) is to duplicate rather than reach into another
# module's underscore-prefixed (private-by-convention) functions.
def _installation_token_or_400(current_user: User) -> str:
    try:
        return get_installation_token_for_user(current_user)
    except GitHubAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


def _analysis_run_or_404(db: Session, analysis_run_id: uuid.UUID, current_user: User) -> AnalysisRun:
    """`AnalysisRun` has no direct user FK — ownership is transitive through
    `repository_id`, so this checks both "does the run exist" and "does the
    current user own the repository it belongs to" together, returning one
    404 either way (same 404-not-403 convention as `_repository_or_404`)."""
    analysis_run = db.get(AnalysisRun, analysis_run_id)
    if analysis_run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis run not found")
    if repo_service.get_repository_for_user(db, analysis_run.repository_id, current_user.id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis run not found")
    return analysis_run


@router.post("/repos/{repository_id}/analysis-runs", response_model=AnalysisRunRead)
def create_analysis_run(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisRunRead:
    """Queues a new analysis run for `repository_id`'s currently selected
    branch (`Branch.is_selected` — Phase 2.4's `select_branch`/
    `upsert_branches`; this endpoint never accepts an ad-hoc branch in the
    request body) and returns immediately — the actual pipeline runs in a
    Celery worker (Phase 5.3's `run_repository_analysis`), not this request.

    A fresh commit sha is resolved from GitHub right now (the same
    installation-token/`get_branch` pattern `select_repo_branch` already
    uses) rather than reusing `Branch.latest_commit_sha`, which may be
    stale since the last branch sync. No concurrent-run prevention — an
    existing in-flight run for this repository does not block a new one
    (explicitly out of scope for this phase).
    """
    repository = _repository_or_404(db, repository_id, current_user)

    branch = db.execute(
        select(Branch).where(Branch.repository_id == repository.id, Branch.is_selected == True)  # noqa: E712
    ).scalar_one_or_none()
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No branch selected for analysis"
        )

    token = _installation_token_or_400(current_user)
    try:
        gh_branch = github_service.get_branch(token, repository.owner, repository.name, branch.name)
    except github_service.GitHubServiceError as exc:
        not_found = exc.status_code == status.HTTP_404_NOT_FOUND
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if not_found else status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub error: {exc}",
        ) from exc
    commit_sha = gh_branch["commit"]["sha"]

    analysis_run = repo_service.create_queued_analysis_run(
        db,
        repository_id=repository.id,
        branch=branch.name,
        commit_sha=commit_sha,
        trigger_source=AnalysisRunTriggerSource.MANUAL,
    )

    run_repository_analysis.delay(str(analysis_run.id))

    return analysis_run


@router.get("/analysis-runs/{analysis_run_id}", response_model=AnalysisRunRead)
def get_analysis_run(
    analysis_run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisRun:
    """Reads back a single run's current state — the REST snapshot/fallback
    the frontend uses alongside `WS /ws/analysis-runs/{id}` (per the plan
    doc's Frontend Integration Pattern section)."""
    return _analysis_run_or_404(db, analysis_run_id, current_user)


@router.get("/repos/{repository_id}/analysis-runs", response_model=list[AnalysisRunRead])
def list_analysis_runs(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AnalysisRun]:
    """A repository's run history, newest first — matches `analysisHistory[]`
    / `app.history.tsx`'s expected use per the plan doc. No pagination
    (out of scope for this phase)."""
    repository = _repository_or_404(db, repository_id, current_user)
    return list(
        db.execute(
            select(AnalysisRun)
            .where(AnalysisRun.repository_id == repository.id)
            .order_by(AnalysisRun.created_at.desc())
        ).scalars()
    )
