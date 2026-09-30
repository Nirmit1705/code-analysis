"""`app/api/v1/compare.py` (Phase 9.3): a thin HTTP surface over 9.1's
`analysis/change.py::diff_analysis_runs` — no new patterns invented, matches
every other Phase 6+ screen endpoint's established conventions exactly.

Per `docs/PHASE_9_PLAN.md` design decisions #1/#3: Compare diffs two
already-persisted `AnalysisRun`s (never a live git patch), picked from the
repository's own run history — never raw branch names.

Per-edge `risk` (`DependencyChangeSchema.risk`) is a simple heuristic off
the *head* run's own dependency graph (design decision #7's `analyze_
dependency_graph`, reused exactly — not a second graph pass): an edge that
touches a file in a real cycle is "high"; one that touches a module already
flagged coupled by `analysis/debt.py`'s own `COUPLING_SCORE_THRESHOLDS[0]`
bar (the same bar `impact_service.py` reuses, not a second, silently
different definition of "coupled") is "medium"; anything else is "low".

No shared prefix on `router` — matches `dashboard.py`'s/`assistant.py`'s own
per-file `APIRouter()` convention (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.analysis.change import ApiFact, FileFact, diff_analysis_runs
from app.analysis.dependencies import DependencyEdge, DependencyEdgeType
from app.analysis.debt import COUPLING_SCORE_THRESHOLDS
from app.analysis.graph import analyze_dependency_graph
from app.api.deps import get_current_user, get_db
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus
from app.models.repository import Repository
from app.models.user import User
from app.schemas.compare import CompareRead, DependencyChangeSchema, FileChangeSchema
from app.services import analysis_read_service, change_narrative_service, repo_service

router = APIRouter(tags=["compare"])


# Duplicated from `app/api/v1/assistant.py`'s/`analysis.py`'s own
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
    already established); 409 if it exists but hasn't completed — there's
    no sensible partial diff against an in-progress or failed run, same
    "no honest empty payload exists" reasoning `assistant.py`'s own 409
    already documents."""
    run = db.get(AnalysisRun, run_id)
    if run is None or run.repository_id != repository.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis run not found")
    if run.status is not AnalysisRunStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Analysis run has not completed")
    return run


@router.get("/repos/{repository_id}/compare", response_model=CompareRead)
def compare_analysis_runs(
    repository_id: uuid.UUID,
    base_run_id: uuid.UUID,
    head_run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompareRead:
    repository = _repository_or_404(db, repository_id, current_user)
    base_run = _completed_run_or_error(db, base_run_id, repository)
    head_run = _completed_run_or_error(db, head_run_id, repository)

    base_facts = analysis_read_service.load_run_facts(db, base_run.id)
    head_facts = analysis_read_service.load_run_facts(db, head_run.id)
    change_set = diff_analysis_runs(base_facts, head_facts)

    graph = analyze_dependency_graph(head_facts.edges)
    coupled_modules = {c.module for c in graph.coupling if c.score >= COUPLING_SCORE_THRESHOLDS[0]}

    changed_dependencies = [
        _dependency_change_schema(edge, added=True, circular_files=graph.circular_files, coupled_modules=coupled_modules)
        for edge in change_set.added_edges
    ] + [
        _dependency_change_schema(edge, added=False, circular_files=graph.circular_files, coupled_modules=coupled_modules)
        for edge in change_set.removed_edges
    ]

    # Phase 9.7 (design decision #15): best-effort AI narrative over the facts
    # above. Never raises, and `None` (no LLM call at all) for an empty diff,
    # so it can't fail or change anything else in this response.
    explanation = change_narrative_service.generate_change_explanation(
        change_narrative_service.build_change_facts(
            base_ref=f"{base_run.branch}@{base_run.commit_sha[:7]}",
            head_ref=f"{head_run.branch}@{head_run.commit_sha[:7]}",
            change_set=change_set,
            debt_score_delta=head_run.debt_score - base_run.debt_score,
        )
    )

    return CompareRead(
        base_run_id=base_run.id,
        head_run_id=head_run.id,
        base_branch=base_run.branch,
        base_commit_sha=base_run.commit_sha,
        head_branch=head_run.branch,
        head_commit_sha=head_run.commit_sha,
        added_files=[_file_change_schema(f, note="new file") for f in change_set.added_files],
        removed_files=[_file_change_schema(f, note="removed file") for f in change_set.removed_files],
        changed_dependencies=changed_dependencies,
        added_apis=[_api_str(a) for a in change_set.added_apis],
        removed_apis=[_api_str(a) for a in change_set.removed_apis],
        file_count_delta=head_run.file_count - base_run.file_count,
        module_count_delta=head_run.module_count - base_run.module_count,
        api_count_delta=head_run.api_count - base_run.api_count,
        dependency_count_delta=head_run.dependency_count - base_run.dependency_count,
        debt_score_delta=head_run.debt_score - base_run.debt_score,
        explanation=explanation,
    )


def _file_change_schema(fact: FileFact, *, note: str) -> FileChangeSchema:
    return FileChangeSchema(path=fact.path, note=note)


def _api_str(fact: ApiFact) -> str:
    return f"{fact.method} {fact.path}"


def _dependency_edge_label(edge: DependencyEdge) -> str:
    """`"{source_module} -> {target}"`, matching `impact_service.py::
    ImpactedFile.why`'s own file/module-grain-only convention (Known Issue
    #7) — `target` is the target module for an internal edge, the raw
    package name for an external one."""
    source_module = _module_of(edge.source_file)
    if edge.edge_type is DependencyEdgeType.INTERNAL and edge.target_file is not None:
        target = _module_of(edge.target_file)
    else:
        target = edge.target_package or "?"
    return f"{source_module} -> {target}"


def _dependency_change_schema(
    edge: DependencyEdge, *, added: bool, circular_files: frozenset[str], coupled_modules: set[str]
) -> DependencyChangeSchema:
    target = edge.target_file if edge.edge_type is DependencyEdgeType.INTERNAL else edge.target_package
    touches_circular = edge.source_file in circular_files or (target is not None and target in circular_files)
    touches_coupled = _module_of(edge.source_file) in coupled_modules or (
        edge.edge_type is DependencyEdgeType.INTERNAL
        and edge.target_file is not None
        and _module_of(edge.target_file) in coupled_modules
    )
    risk = "high" if touches_circular else "medium" if touches_coupled else "low"

    label = _dependency_edge_label(edge)
    change = f"new import of `{target or '?'}`" if added else f"removed import of `{target or '?'}`"
    return DependencyChangeSchema(edge=label, change=change, risk=risk)


def _module_of(file_path: str) -> str:
    """Same "containing directory" convention as `analysis/graph.py`'s own
    (private) `_module_of` — duplicated locally per this codebase's
    established small-helper convention (see `impact_service.py`'s own
    copy)."""
    return str(PurePosixPath(file_path).parent)
