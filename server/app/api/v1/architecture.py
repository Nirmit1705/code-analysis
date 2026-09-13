"""`GET /repos/{id}/architecture` (Phase 6.5): derives `GraphNode`/
`GraphEdge` lists from the latest completed run's `File`/`ApiEndpoint`/
`DependencyEdge` rows — no live GitHub call, purely a DB read.

No shared prefix on `router` — matches `dashboard.py`'s/`analysis.py`'s own
per-file `APIRouter()` convention (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.dependencies import DependencyEdge as PureDependencyEdge
from app.analysis.dependencies import DependencyEdgeType
from app.analysis.graph import analyze_dependency_graph
from app.api.deps import get_current_user, get_db
from app.models.api_endpoint import ApiEndpoint
from app.models.file import File
from app.models.repository import Repository
from app.models.user import User
from app.schemas.architecture import ArchitectureRead, GraphEdge, GraphNode
from app.services import analysis_read_service, repo_service
from app.services.module_classification import ModuleClassification, classify_modules

router = APIRouter(tags=["architecture"])

# Synthetic node id for the aggregate "external packages" node (plan's rule
# 5: "External packages get one synthetic external node, never derived from
# File rows"). Can never collide with a real module's id — every real group
# id is either a depth-2 `File.module` path or an `"other:<kind>"` fold,
# neither of which is ever the bare string "external".
EXTERNAL_NODE_ID = "external"


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

    current_run = analysis_read_service.get_latest_completed_run(db, repository.id)
    if current_run is None:
        return ArchitectureRead(analyzed=False)

    files = db.execute(select(File).where(File.analysis_run_id == current_run.id)).scalars().all()
    api_endpoints = db.execute(
        select(ApiEndpoint).where(ApiEndpoint.analysis_run_id == current_run.id)
    ).scalars().all()
    classification = classify_modules(files, api_endpoints)

    edges = analysis_read_service.load_dependency_edges(db, current_run.id)
    circular_pairs = {
        (e.source_file, e.target_file) for e in analyze_dependency_graph(edges).circular_edges
    }

    nodes = [
        GraphNode(
            id=g.id,
            label=g.label,
            kind=g.kind.value,
            files=g.file_count,
            loc=g.loc,
            key_files=g.key_files,
            apis=g.apis,
            insight="",
        )
        for g in classification.groups
    ]
    graph_edges, external_node = _build_edges(edges, classification, circular_pairs)
    if external_node is not None:
        nodes.append(external_node)

    return ArchitectureRead(
        analyzed=True,
        analysis_run_id=current_run.id,
        nodes=nodes,
        edges=graph_edges,
    )


def _build_edges(
    edges: list[PureDependencyEdge],
    classification: ModuleClassification,
    circular_pairs: set[tuple[str, str | None]],
) -> tuple[list[GraphEdge], GraphNode | None]:
    """Aggregates file-level `DependencyEdge`s into group-to-group edges.

    Dedupes to distinct (source_file, target_file) — or (source_file,
    target_package) for an external edge — pairs *before* aggregating, the
    same convention `analysis/graph.py::_compute_coupling` uses for its own
    fan-in/fan-out counts: two import statements between the same two files
    (e.g. a TypeScript type-only import split from a value import) are one
    file-to-file dependency, not two, and shouldn't inflate an edge's
    `weight` just because it was written on two lines.
    """
    internal_file_pairs: set[tuple[str, str]] = set()
    external_file_pairs: set[tuple[str, str]] = set()
    for edge in edges:
        if edge.edge_type is DependencyEdgeType.INTERNAL and edge.target_file is not None:
            internal_file_pairs.add((edge.source_file, edge.target_file))
        elif edge.edge_type is DependencyEdgeType.EXTERNAL and edge.target_package is not None:
            external_file_pairs.add((edge.source_file, edge.target_package))

    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    pair_circular: dict[tuple[str, str], bool] = defaultdict(bool)

    for source_file, target_file in internal_file_pairs:
        source_group = classification.file_group_id.get(source_file)
        target_group = classification.file_group_id.get(target_file)
        # A source/target missing from file_group_id would mean it wasn't
        # in the same File set classify_modules saw — can't happen, both
        # come from the same run's File rows — but skip defensively rather
        # than KeyError if that invariant is ever violated. Same-group
        # edges (a file importing a sibling in its own group) aren't
        # inter-module coupling, matching analysis/graph.py's own stance.
        if source_group is None or target_group is None or source_group == target_group:
            continue
        pair = (source_group, target_group)
        pair_counts[pair] += 1
        if (source_file, target_file) in circular_pairs:
            pair_circular[pair] = True

    has_external = False
    for source_file, _target_package in external_file_pairs:
        source_group = classification.file_group_id.get(source_file)
        if source_group is None:
            continue
        has_external = True
        pair_counts[(source_group, EXTERNAL_NODE_ID)] += 1

    graph_edges = [
        GraphEdge(source=source, target=target, weight=count, circular=pair_circular.get((source, target), False))
        for (source, target), count in sorted(pair_counts.items())
    ]

    external_node = None
    if has_external:
        external_node = GraphNode(
            id=EXTERNAL_NODE_ID,
            label="External Dependencies",
            kind="external",
            files=0,
            loc=0,
            key_files=[],
            apis=[],
            insight="",
        )
    return graph_edges, external_node
