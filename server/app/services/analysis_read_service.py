"""Shared "read facts back from a completed run" helpers (Phase 6.3) — the
home for lookups every read-only `/repos/{id}/...` screen endpoint needs but
none of them owns exclusively. `get_latest_completed_run`/
`get_previous_completed_run` back the Dashboard (6.3/6.4); 6.5 (Architecture)
and 6.9 (Dependencies) are expected to add their own `load_dependency_edges`
helper here once they land, per `docs/PHASE_6_PLAN.md`'s "Critical files"
section.

Distinct from `repo_service.py`: that module owns *writes* (connect/select/
persist) plus the one existing read it already had before this phase
(`get_repository_for_user`, ownership scoping). This module is exclusively
read-only aggregation over already-persisted `AnalysisRun` rows, for the new
screen endpoints introduced from Phase 6.3 onward.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis_run import AnalysisRun, AnalysisRunStatus


def get_latest_completed_run(db: Session, repository_id: uuid.UUID) -> AnalysisRun | None:
    """The most recently *completed* run for a repository — not simply the
    most recently created row, which could be a newer `QUEUED`/`FAILED`
    attempt made after the last real success. Ordered by `completed_at`
    desc (ties broken by `created_at` desc for determinism, e.g. two runs
    completed within the same second). `None` if the repository has never
    completed an analysis — callers are expected to render a defined
    "not analyzed" response for that case rather than fabricating one from
    an empty aggregate (see `docs/PHASE_6_PLAN.md`'s 6.3 section)."""
    return db.execute(
        select(AnalysisRun)
        .where(AnalysisRun.repository_id == repository_id, AnalysisRun.status == AnalysisRunStatus.COMPLETED)
        .order_by(AnalysisRun.completed_at.desc(), AnalysisRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_previous_completed_run(db: Session, repository_id: uuid.UUID, before: datetime) -> AnalysisRun | None:
    """The completed run immediately preceding `before` (in practice, the
    latest run's own `completed_at`) — used to compute a health metric's
    `delta` (Phase 6.3). `None` if there is no earlier completed run (a
    repository's first analysis has nothing to compare against, so
    `delta` is defined as `0` in that case rather than compared against a
    fabricated baseline)."""
    return db.execute(
        select(AnalysisRun)
        .where(
            AnalysisRun.repository_id == repository_id,
            AnalysisRun.status == AnalysisRunStatus.COMPLETED,
            AnalysisRun.completed_at < before,
        )
        .order_by(AnalysisRun.completed_at.desc(), AnalysisRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
