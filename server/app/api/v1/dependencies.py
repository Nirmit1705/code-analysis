"""`GET /repos/{id}/dependencies` (Phase 6.9): real re-derived
circular/coupled/orphans plus honestly-scoped external-package findings.

`circular`/`coupled`/`orphans` reuse 6.5's `analysis_read_service
.load_dependency_edges` + `analyze_dependency_graph()` (Phase 4.2) — the
exact same facts Architecture's own circular-edge highlighting is built
from, not a second hand-rolled graph pass. `external` is the one part of
this screen that reads `DebtIssue` rows directly (Phase 4.3's
`OUTDATED_DEPENDENCY` rule) rather than recomputing anything: that naive
version-age heuristic against a small hand-maintained package table is the
*only* place in this codebase a real "this package looks outdated" fact
exists — there is no live registry/CVE call to re-derive it from.

No shared prefix on `router` — matches `dashboard.py`'s/`architecture.py`'s/
`codebase.py`'s own per-file `APIRouter()` convention (composed under
`/api/v1` by `router.py`).
"""

from __future__ import annotations

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.debt import DebtIssueType
from app.analysis.dependencies import DependencyEdge as PureDependencyEdge
from app.analysis.dependencies import DependencyEdgeType
from app.analysis.graph import analyze_dependency_graph
from app.api.deps import get_current_user, get_db
from app.models.debt_issue import DebtIssue
from app.models.repository import Repository
from app.models.user import User
from app.schemas.dependencies import (
    CircularGroup,
    CoupledModule,
    DependenciesRead,
    DependencySeverity,
    ExternalDependencyFinding,
)
from app.services import analysis_read_service, repo_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dependencies"])

# Duplicated from `analysis/debt.py`'s own `CIRCULAR_DEPENDENCY_SIZE_
# THRESHOLDS` (medium, high, critical), matching this codebase's
# established "duplicate small private constants" convention (see
# `dashboard.py`'s/`codebase.py`'s own duplication of
# `_SEVERITY_WEIGHT`/`_COMPLEXITY_THRESHOLDS`). `circular` is re-derived
# fresh from `DependencyEdge` rows at read time (same as Architecture's own
# circular-edge highlighting), not read back from a persisted `DebtIssue`
# row, so this endpoint needs its own copy of the tiering rule rather than
# reusing the DB row `debt.py` already computed for the exact same fact at
# analysis time.
_CIRCULAR_SIZE_THRESHOLDS = (1, 3, 6)

# Parses the leading "{name}@{version}" off `DebtIssue.description`'s fixed
# sentence shape (`analysis/debt.py::_check_outdated_dependencies`'s own
# f-string: "{name}@{version} is {N} major version(s) behind ..."). `.+` is
# greedy, so it backtracks to the *last* "@" immediately before a
# non-whitespace version token — correctly handling a scoped npm package
# name that itself contains "@" (e.g. "@sendgrid/mail@1.2.3" -> name
# "@sendgrid/mail", not "@sendgrid/mail@1.2.3").
_EXTERNAL_FINDING_RE = re.compile(r"^(?P<name>.+)@(?P<version>\S+) is \d+ major version\(s\) behind")


def _severity_for_cycle_size(size: int) -> DependencySeverity:
    medium, high, critical = _CIRCULAR_SIZE_THRESHOLDS
    if size >= critical:
        return "critical"
    if size >= high:
        return "high"
    # Every real `CycleResult` has at least 1 file (a self-loop) or 2+ (a
    # real multi-file cycle) — always clears the MEDIUM floor of 1, so this
    # branch is the true floor, never a bare fallback for an impossible case.
    assert size >= medium
    return "medium"


def _normalize_package_name(name: str) -> str:
    """Duplicated from `analysis/debt.py`'s own `_normalize_package_name`
    — same rationale as this module's other duplicated small private
    constants/helpers: case/separator normalization only (`React` vs
    `react`, `some_pkg` vs `some-pkg`), enough to match a manifest-declared
    name against an import specifier's resolved package name without
    replicating either ecosystem's full name-normalization rules."""
    return name.strip().lower().replace("_", "-")


# Duplicated from `app/api/v1/github.py`'s/`analysis.py`'s/`dashboard.py`'s/
# `architecture.py`'s/`codebase.py`'s own module-private helpers of the
# same shape, rather than imported — matches this codebase's established
# convention of not reaching into another module's underscore-prefixed
# functions (see `analysis.py`'s own docstring).
def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


@router.get("/repos/{repository_id}/dependencies", response_model=DependenciesRead)
def get_dependencies(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DependenciesRead:
    """Returns `analyzed=False` (no other field populated) when the
    repository has never completed an analysis, matching every other
    Phase 6 screen endpoint's convention."""
    repository = _repository_or_404(db, repository_id, current_user)

    current_run = analysis_read_service.get_latest_completed_run(db, repository.id)
    if current_run is None:
        return DependenciesRead(analyzed=False)

    edges = analysis_read_service.load_dependency_edges(db, current_run.id)
    graph = analyze_dependency_graph(edges)

    circular = [
        CircularGroup(
            files=sorted(cycle.files),
            edge_count=len(cycle.edges),
            severity=_severity_for_cycle_size(len(cycle.files)),
        )
        for cycle in graph.cycles
    ]
    coupled = [
        CoupledModule(module=c.module, fan_in=c.fan_in, fan_out=c.fan_out, score=c.score) for c in graph.coupling
    ]

    return DependenciesRead(
        analyzed=True,
        analysis_run_id=current_run.id,
        circular=circular,
        coupled=coupled,
        orphans=graph.orphans,
        external=_build_external_findings(db, current_run.id, edges),
    )


def _build_external_findings(
    db: Session, analysis_run_id: uuid.UUID, edges: list[PureDependencyEdge]
) -> list[ExternalDependencyFinding]:
    """One entry per persisted `DebtIssue` row of type `OUTDATED_DEPENDENCY`
    for this run — see module docstring for why this is the one part of
    the screen that reads `DebtIssue` directly instead of re-deriving.
    `used_by` is counted fresh against this same run's `EXTERNAL` edges
    (already loaded by the caller for the graph analysis above, not a
    second query) — the number of distinct files that actually import the
    package, not trusted from anything `debt.py` itself recorded."""
    issues = db.execute(
        select(DebtIssue).where(
            DebtIssue.analysis_run_id == analysis_run_id, DebtIssue.issue_type == DebtIssueType.OUTDATED_DEPENDENCY
        )
    ).scalars().all()
    if not issues:
        return []

    external_edges = [e for e in edges if e.edge_type is DependencyEdgeType.EXTERNAL and e.target_package]

    findings: list[ExternalDependencyFinding] = []
    for issue in issues:
        match = _EXTERNAL_FINDING_RE.match(issue.description)
        if match is None:
            # Should never happen — every OUTDATED_DEPENDENCY issue is
            # generated by `_check_outdated_dependencies`'s own fixed
            # f-string — but skip defensively rather than fabricate a name
            # or fail the whole request over one malformed row.
            logger.warning(
                "get_dependencies: OUTDATED_DEPENDENCY issue %s description didn't match the expected "
                "shape: %r",
                issue.id,
                issue.description,
            )
            continue
        name = match.group("name")
        normalized_name = _normalize_package_name(name)
        used_by = len(
            {
                edge.source_file
                for edge in external_edges
                if _normalize_package_name(edge.target_package) == normalized_name
            }
        )
        findings.append(
            ExternalDependencyFinding(
                name=name,
                description=issue.description,
                severity=issue.severity.value,
                majors_behind=int(issue.metric_value),
                used_by=used_by,
            )
        )

    _severity_rank: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    findings.sort(key=lambda f: (-_severity_rank[f.severity], f.name))
    return findings
