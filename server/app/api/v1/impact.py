"""`app/api/v1/impact.py` (Phase 9.3): a thin HTTP surface wiring 9.1's
`diff_analysis_runs` and 9.2's `compute_impact` together — no new patterns
invented, matches every other Phase 6+ screen endpoint's established
conventions exactly.

Per `docs/PHASE_9_PLAN.md` design decision #2: Impact is commit/diff-driven,
not a static "pick any file" tool. Given one already-completed
`analysis_run_id`, this resolves its immediate predecessor **on the same
branch** (`analysis_read_service.get_previous_completed_run`'s new `branch`
filter, design decision #14), diffs the two, and computes the downstream
blast radius of exactly what changed.

No shared prefix on `router` — matches `dashboard.py`'s/`assistant.py`'s own
per-file `APIRouter()` convention (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.analysis.change import diff_analysis_runs
from app.api.deps import get_current_user, get_db
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus
from app.models.repository import Repository
from app.models.user import User
from app.schemas.impact import ImpactedFileSchema, ImpactRead
from app.services import analysis_read_service, impact_service, repo_service

router = APIRouter(tags=["impact"])


# Duplicated from `app/api/v1/compare.py`'s/`assistant.py`'s own
# module-private helper of the same shape, rather than imported — this
# codebase's established convention (see those files' own docstrings).
def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


def _completed_run_or_error(db: Session, run_id: uuid.UUID, repository: Repository) -> AnalysisRun:
    """404 if `run_id` doesn't exist or doesn't belong to `repository`
    (same 404-not-403 convention `analysis.py::_analysis_run_or_404`
    already established); 409 if it exists but hasn't completed."""
    run = db.get(AnalysisRun, run_id)
    if run is None or run.repository_id != repository.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis run not found")
    if run.status is not AnalysisRunStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Analysis run has not completed")
    return run


@router.get("/repos/{repository_id}/impact/{analysis_run_id}", response_model=ImpactRead)
def get_impact(
    repository_id: uuid.UUID,
    analysis_run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImpactRead:
    repository = _repository_or_404(db, repository_id, current_user)
    head_run = _completed_run_or_error(db, analysis_run_id, repository)

    previous_run = analysis_read_service.get_previous_completed_run(
        db, repository.id, before=head_run.completed_at, branch=head_run.branch
    )
    if previous_run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No earlier completed analysis run on this branch to compare against",
        )

    base_facts = analysis_read_service.load_run_facts(db, previous_run.id)
    head_facts = analysis_read_service.load_run_facts(db, head_run.id)
    change_set = diff_analysis_runs(base_facts, head_facts)

    changed_file_paths = (
        {f.path for f in change_set.added_files}
        | {f.path for f in change_set.removed_files}
        | set(change_set.modified_files)
    )
    result = impact_service.compute_impact(
        db, head_analysis_run_id=head_run.id, changed_file_paths=changed_file_paths
    )

    return ImpactRead(
        analysis_run_id=head_run.id,
        previous_analysis_run_id=previous_run.id,
        branch=head_run.branch,
        commit_sha=head_run.commit_sha,
        risk=result.risk,
        score=result.score,
        direct=[ImpactedFileSchema(path=f.path, why=f.why, hops=f.hops) for f in result.direct],
        indirect=[ImpactedFileSchema(path=f.path, why=f.why, hops=f.hops) for f in result.indirect],
        affected_apis=result.affected_apis,
        explanation=None,
    )
