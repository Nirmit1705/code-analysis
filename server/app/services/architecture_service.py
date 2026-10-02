"""Builds the Architecture screen's component graph from a repository's latest
completed run (`GET /repos/{id}/architecture`, Phase 6.5) — extracted from
`api/v1/architecture.py` unchanged in Phase 10.8, so the generated Architecture
and Modules documents are computed from exactly what that screen shows rather
than a second, possibly different, derivation (same precedent as the 10.1
extraction of `external_services.py` and the 6.5 `module_classification.py`).

The three additions over the original router-private version are
`dependency_edges` and `graph` on `Architecture` (already computed here; the
documentation generator needs the cycles and the importer counts) — nothing
else changed, and `smoke_api_architecture.py` is the regression check.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.dependencies import DependencyEdge as PureDependencyEdge
from app.analysis.dependencies import DependencyEdgeType
from app.analysis.graph import GraphAnalysis, analyze_dependency_graph
from app.models.analysis_run import AnalysisRun
from app.models.api_endpoint import ApiEndpoint
from app.models.file import File
from app.models.repository import Repository
from app.schemas.architecture import GraphEdge, GraphNode
from app.services import analysis_read_service
from app.services.external_services import resolve_external_target
from app.services.module_classification import ModuleClassification, classify_modules


@dataclass
class Architecture:
    """Everything derived for one run, kept together so the per-component
    insight endpoint and the generated documents are computed from exactly what
    the screen shows."""

    run: AnalysisRun
    files: list[File]
    classification: ModuleClassification
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    cycle_count: int
    dependency_edges: list[PureDependencyEdge]
    graph: GraphAnalysis


def build_architecture(db: Session, repository: Repository) -> Architecture | None:
    """`None` when the repository has never completed an analysis."""
    current_run = analysis_read_service.get_latest_completed_run(db, repository.id)
    if current_run is None:
        return None

    files = db.execute(select(File).where(File.analysis_run_id == current_run.id)).scalars().all()
    api_endpoints = db.execute(
        select(ApiEndpoint).where(ApiEndpoint.analysis_run_id == current_run.id)
    ).scalars().all()
    classification = classify_modules(files, api_endpoints)

    edges = analysis_read_service.load_dependency_edges(db, current_run.id)
    graph = analyze_dependency_graph(edges)
    circular_pairs = {(e.source_file, e.target_file) for e in graph.circular_edges}

    # A cycle whose files all sit inside one group produces no group-to-group
    # edge (`build_edges` skips same-group pairs), so an edge flag alone would
    # hide it — e.g. HireSight's 14-file cycle lives entirely in `client/src`.
    # Count each group's cycle-member files instead, from the same cycles the
    # Dependencies screen lists (no second detection pass). Cycles are SCCs, so
    # a file is in at most one.
    circular_files_by_group: Counter[str] = Counter()
    for cycle in graph.cycles:
        for file_path in cycle.files:
            group_id = classification.file_group_id.get(file_path)
            if group_id is not None:
                circular_files_by_group[group_id] += 1

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
            circular_files=circular_files_by_group.get(g.id, 0),
        )
        for g in classification.groups
    ]
    graph_edges, external_nodes = build_edges(edges, classification, circular_pairs, current_run.database_provider)
    nodes.extend(external_nodes)

    return Architecture(
        run=current_run,
        files=list(files),
        classification=classification,
        nodes=nodes,
        edges=graph_edges,
        cycle_count=len(graph.cycles),
        dependency_edges=edges,
        graph=graph,
    )


def build_edges(
    edges: list[PureDependencyEdge],
    classification: ModuleClassification,
    circular_pairs: set[tuple[str, str | None]],
    database_provider: str | None,
) -> tuple[list[GraphEdge], list[GraphNode]]:
    """Aggregates file-level `DependencyEdge`s into group-to-group edges,
    plus whichever named external-service nodes (`external_services.resolve_external_target`)
    real imports actually justify.

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

    external_nodes: dict[str, GraphNode] = {}
    for source_file, target_package in external_file_pairs:
        source_group = classification.file_group_id.get(source_file)
        if source_group is None:
            continue
        resolved = resolve_external_target(target_package, database_provider)
        if resolved is None:
            continue  # a language builtin or dev/build tool — not real architecture
        node_id, label, kind = resolved
        pair_counts[(source_group, node_id)] += 1
        external_nodes.setdefault(
            node_id,
            GraphNode(id=node_id, label=label, kind=kind, files=0, loc=0, key_files=[], apis=[], insight=""),
        )

    graph_edges = [
        GraphEdge(source=source, target=target, weight=count, circular=pair_circular.get((source, target), False))
        for (source, target), count in sorted(pair_counts.items())
    ]

    return graph_edges, sorted(external_nodes.values(), key=lambda n: n.id)
