"""`GET /repos/{id}/architecture` (Phase 6.5): derives `GraphNode`/
`GraphEdge` lists from the latest completed run's `File`/`ApiEndpoint`/
`DependencyEdge` rows — no live GitHub call, purely a DB read. The derivation
itself lives in `services/architecture_service.py` (extracted in Phase 10.8 so
the generated documentation shares it).

No shared prefix on `router` — matches `dashboard.py`'s/`analysis.py`'s own
per-file `APIRouter()` convention (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.repository import Repository
from app.models.user import User
from app.schemas.architecture import ArchitectureRead, ComponentInsightRead
from app.services import architecture_service, component_insight_service, repo_service

router = APIRouter(tags=["architecture"])


# Duplicated from `dashboard.py`'s/`analysis.py`'s own module-private helper
# of the same shape, rather than imported — matches this codebase's
# established convention of not reaching into another module's
# underscore-prefixed functions (see `analysis.py`'s own docstring).
def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


@router.get("/repos/{repository_id}/architecture", response_model=ArchitectureRead)
def get_architecture(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArchitectureRead:
    """Returns `analyzed=False` (no other field populated) when the
    repository has never completed an analysis, matching `DashboardRead`'s
    own convention."""
    repository = _repository_or_404(db, repository_id, current_user)
    built = architecture_service.build_architecture(db, repository)
    if built is None:
        return ArchitectureRead(analyzed=False)
    return ArchitectureRead(
        analyzed=True,
        analysis_run_id=built.run.id,
        nodes=built.nodes,
        edges=built.edges,
        cycle_count=built.cycle_count,
    )


@router.get("/repos/{repository_id}/architecture/insight", response_model=ComponentInsightRead)
def get_component_insight(
    repository_id: uuid.UUID,
    node_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ComponentInsightRead:
    """An AI insight for one Architecture component, generated on demand when
    its node is clicked (see `component_insight_service`). `insight` is `None`
    when the model call failed or returned nothing usable — never an error, so
    the detail panel degrades to a calm "not available" line. 404 only when the
    repository is not the caller's, or `node_id` is not a component of the
    latest analysis."""
    repository = _repository_or_404(db, repository_id, current_user)
    built = architecture_service.build_architecture(db, repository)
    node = next((n for n in built.nodes if n.id == node_id), None) if built is not None else None
    if built is None or node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Component not found")

    facts = component_insight_service.build_component_facts(
        db,
        node=node,
        nodes=built.nodes,
        edges=built.edges,
        classification=built.classification,
        files=built.files,
        analysis_run_id=built.run.id,
    )
    return ComponentInsightRead(node_id=node.id, insight=component_insight_service.generate_component_insight(facts))
