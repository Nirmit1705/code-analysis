"""Repository settings, disconnect and purge (Phase 10.11):

- `GET /repos/{id}/settings` — connection facts (incl. a *live* "is the GitHub
  App still installed on this repository" check — never the installation id),
  the last completed analysis, and the two editable settings plus the
  always-on default ignore patterns.
- `PATCH /repos/{id}/settings` — `analysis_mode` and/or `ignore_patterns`;
  both take effect from the next analysis.
- `DELETE /repos/{id}` — disconnect: deletes the repository and (by DB
  cascade) everything analyzed for it. Does not uninstall the GitHub App.
- `POST /repos/{id}/purge` — deletes every run (History/trends included) and
  queues one fresh `MANUAL` run on the selected branch (202).

Disconnect and purge answer 409 while a run is in flight
(`repo_service.IN_FLIGHT_WINDOW`). No shared prefix on `router`, same per-file
convention as `onboarding.py`/`tracer.py`.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.analysis.ingestion import DEFAULT_IGNORE_PATTERNS, MAX_IGNORE_PATTERN_LENGTH, MAX_USER_IGNORE_PATTERNS
from app.api.deps import get_current_user, get_db
from app.models.analysis_run import AnalysisRun
from app.models.repository import Repository
from app.models.user import User
from app.schemas.analysis import AnalysisRunRead
from app.schemas.repository_settings import (
    InstallationStatus,
    LastAnalysis,
    PurgeRead,
    RepositorySettingsRead,
    RepositorySettingsUpdate,
    RepositorySettingsUpdateRead,
    RunInFlight,
)
from app.services import analysis_read_service, github_service, repo_service
from app.services.github_auth_service import GitHubAuthError, get_installation_token_for_user
from app.workers.tasks.analysis_tasks import run_repository_analysis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["repository-settings"])


# Duplicated per-file, matching `onboarding.py`/`analysis.py` (this codebase
# does not import another module's underscore-prefixed helpers).
def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


def _in_flight_409(analysis_run: AnalysisRun) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"An analysis run is in progress ({analysis_run.status.value}); wait for it to finish.",
    )


def _run_in_flight(analysis_run: AnalysisRun | None) -> RunInFlight | None:
    if analysis_run is None:
        return None
    return RunInFlight(
        analysis_run_id=analysis_run.id, status=analysis_run.status.value, started_at=analysis_run.started_at
    )


def _installation_status(repository: Repository) -> InstallationStatus:
    """One App-JWT call to GitHub. Never raises: anything that is not a clear
    answer from GitHub is `unknown`, so the settings screen still loads."""
    try:
        installation = github_service.get_repository_installation(repository.owner, repository.name)
    except github_service.GitHubServiceError as exc:
        return "not_installed" if exc.status_code == status.HTTP_404_NOT_FOUND else "unknown"
    except Exception:  # noqa: BLE001 — network error, App key not configured, non-JSON body
        logger.warning("settings: installation check failed for %s", repository.full_name, exc_info=True)
        return "unknown"
    if not isinstance(installation, dict) or installation.get("id") is None:
        return "unknown"
    if installation.get("suspended_at"):
        return "suspended"
    if str(installation["id"]) != repository.installation_id:
        return "reinstalled"
    return "connected"


@router.get("/repos/{repository_id}/settings", response_model=RepositorySettingsRead)
def get_repository_settings(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RepositorySettingsRead:
    repository = _repository_or_404(db, repository_id, current_user)
    selected = repo_service.get_selected_branch(db, repository.id)
    latest = analysis_read_service.get_latest_completed_run(db, repository.id)
    return RepositorySettingsRead(
        repository_id=repository.id,
        owner=repository.owner,
        name=repository.name,
        full_name=repository.full_name,
        private=repository.private,
        default_branch=repository.default_branch,
        selected_branch=selected.name if selected is not None else None,
        installation=_installation_status(repository),
        analysis_status=repository.analysis_status,
        last_analysis=(
            LastAnalysis(
                analysis_run_id=latest.id,
                branch=latest.branch,
                commit_sha=latest.commit_sha,
                completed_at=latest.completed_at,
                file_count=latest.file_count,
            )
            if latest is not None
            else None
        ),
        analysis_run_count=repo_service.count_analysis_runs(db, repository.id),
        run_in_flight=_run_in_flight(repo_service.get_in_flight_run(db, repository.id)),
        analysis_mode=repository.analysis_mode,
        ignore_patterns=list(repository.ignore_patterns or []),
        default_ignore_patterns=list(DEFAULT_IGNORE_PATTERNS),
        max_ignore_patterns=MAX_USER_IGNORE_PATTERNS,
        max_ignore_pattern_length=MAX_IGNORE_PATTERN_LENGTH,
    )


@router.patch("/repos/{repository_id}/settings", response_model=RepositorySettingsUpdateRead)
def update_repository_settings(
    repository_id: uuid.UUID,
    body: RepositorySettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RepositorySettingsUpdateRead:
    """Validation (patterns trimmed, blank/`#` lines dropped, <= 50 x <= 200
    chars, no `!` negation, no match-everything pattern) is the schema's — an
    invalid body is a 422 and nothing is saved."""
    repository = _repository_or_404(db, repository_id, current_user)
    repository = repo_service.update_repository_settings(
        db, repository, analysis_mode=body.analysis_mode, ignore_patterns=body.ignore_patterns
    )
    return RepositorySettingsUpdateRead(
        analysis_mode=repository.analysis_mode, ignore_patterns=list(repository.ignore_patterns or [])
    )


@router.delete("/repos/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_repository(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    repository = _repository_or_404(db, repository_id, current_user)
    try:
        repo_service.delete_repository(db, repository)
    except repo_service.RunInFlightError as exc:
        raise _in_flight_409(exc.analysis_run) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/repos/{repository_id}/purge", status_code=status.HTTP_202_ACCEPTED, response_model=PurgeRead)
def purge_repository(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Order matters: everything that can fail without side effects (no
    selected branch, a live run, GitHub unreachable while resolving the head
    commit) is checked *before* anything is deleted. The delete + the new
    `QUEUED` row are one transaction. If only the final enqueue fails (broker
    down) the old runs are already gone: the new row is marked `FAILED` and
    the answer is a 503 saying so (same handling as the webhook)."""
    repository = _repository_or_404(db, repository_id, current_user)

    branch = repo_service.get_selected_branch(db, repository.id)
    if branch is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No branch selected for analysis")
    in_flight = repo_service.get_in_flight_run(db, repository.id)
    if in_flight is not None:
        raise _in_flight_409(in_flight)

    try:
        token = get_installation_token_for_user(current_user)
    except GitHubAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    try:
        gh_branch = github_service.get_branch(token, repository.owner, repository.name, branch.name)
    except github_service.GitHubServiceError as exc:
        not_found = exc.status_code == status.HTTP_404_NOT_FOUND
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if not_found else status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub error: {exc}",
        ) from exc
    commit_sha = gh_branch["commit"]["sha"]

    try:
        analysis_run, deleted = repo_service.purge_repository_runs(
            db, repository, branch=branch.name, commit_sha=commit_sha
        )
    except repo_service.RunInFlightError as exc:  # a run started between the pre-check and the lock
        raise _in_flight_409(exc.analysis_run) from None

    try:
        run_repository_analysis.delay(str(analysis_run.id))
    except Exception as exc:  # noqa: BLE001 — broker down: don't leave the row stuck at QUEUED
        logger.exception("purge: failed to enqueue analysis run %s", analysis_run.id)
        repo_service.mark_analysis_run_failed(
            db, analysis_run=analysis_run, error_message=f"Failed to enqueue analysis task: {exc}"
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "Previous analysis runs were deleted, but the new analysis could not be queued. "
                "Start one with Analyze.",
                "deleted_run_count": deleted,
            },
        )

    return PurgeRead(deleted_run_count=deleted, analysis_run=AnalysisRunRead.model_validate(analysis_run))
