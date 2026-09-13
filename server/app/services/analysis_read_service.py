"""Shared "read facts back from a completed run" helpers (Phase 6.3) — the
home for lookups every read-only `/repos/{id}/...` screen endpoint needs but
none of them owns exclusively. `get_latest_completed_run`/
`get_previous_completed_run` back the Dashboard (6.3/6.4); `load_dependency_edges`
(added in 6.5) backs Architecture and, per `docs/PHASE_6_PLAN.md`, is shared
again by 6.9's Dependencies screen once it lands.

Distinct from `repo_service.py`: that module owns *writes* (connect/select/
persist) plus the one existing read it already had before this phase
(`get_repository_for_user`, ownership scoping). This module is exclusively
read-only aggregation over already-persisted `AnalysisRun` rows, for the new
screen endpoints introduced from Phase 6.3 onward.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.dependencies import DependencyEdge as PureDependencyEdge
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus
from app.models.dependency import DependencyEdge as DependencyEdgeRow
from app.models.file import File

logger = logging.getLogger(__name__)


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


def load_dependency_edges(db: Session, analysis_run_id: uuid.UUID) -> list[PureDependencyEdge]:
    """Reconstructs Phase 3.7's pure `DependencyEdge` dataclass
    (`app.analysis.dependencies`) from the persisted `File`+`DependencyEdge`
    rows of one run, so the already-tested `analyze_dependency_graph()`
    (Phase 4.2) can run fresh at read time instead of a second, hand-rolled
    cycle/coupling/orphan pass over raw rows. Introduced in 6.5
    (Architecture); shared again by 6.9 (Dependencies) per the plan.

    Two dataclass fields the DB never persisted (`raw_specifier`, `line` —
    a Phase 4.1 schema simplification, not this function's own) are filled
    with `""`/`0` placeholders. Confirmed safe against `analysis/graph.py`'s
    own module docstring: cycle detection, coupling, and orphan detection
    are computed purely from `source_file`/`target_file`/`edge_type` — a
    caller that ever needs the real import-statement text or line number
    for something else must not rely on this reconstruction for it.

    A `DependencyEdge` row whose `source_file_id` doesn't resolve to a
    `File` row in this same run (only reachable today via the FK's
    `SET NULL` behavior firing on that one `File` row without the whole
    `AnalysisRun` being deleted alongside it — not a real code path
    anywhere in this codebase) is skipped with a warning rather than
    raising, since the pure dataclass's `source_file` field is required.
    """
    file_rows = db.execute(
        select(File.id, File.path, File.module, File.language).where(File.analysis_run_id == analysis_run_id)
    ).all()
    path_by_file_id = {row.id: row.path for row in file_rows}
    module_by_file_id = {row.id: row.module for row in file_rows}
    language_by_file_id = {row.id: row.language for row in file_rows}

    edge_rows = db.execute(
        select(DependencyEdgeRow).where(DependencyEdgeRow.analysis_run_id == analysis_run_id)
    ).scalars().all()

    edges: list[PureDependencyEdge] = []
    for row in edge_rows:
        source_path = path_by_file_id.get(row.source_file_id) if row.source_file_id else None
        if source_path is None:
            logger.warning(
                "load_dependency_edges: skipping edge %s with unresolved source_file_id", row.id
            )
            continue
        target_path = path_by_file_id.get(row.target_file_id) if row.target_file_id else None
        edges.append(
            PureDependencyEdge(
                source_file=source_path,
                module=module_by_file_id[row.source_file_id],
                # `language` is typed `Language` (not Optional) on the
                # dataclass, but every file that ever produced a
                # DependencyEdge was necessarily run through a per-language
                # extractor first — so `File.language` is never actually
                # None here in practice. Falling back to the source file's
                # (possibly None) language rather than raising keeps this
                # reconstruction from blowing up if that invariant is ever
                # violated; `analyze_dependency_graph` never reads this
                # field either way (see this function's own docstring).
                language=language_by_file_id.get(row.source_file_id),
                edge_type=row.edge_type,
                target_file=target_path,
                target_package=row.target_package_name,
                raw_specifier="",
                line=0,
            )
        )
    return edges
