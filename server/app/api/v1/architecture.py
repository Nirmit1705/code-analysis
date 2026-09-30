"""`GET /repos/{id}/architecture` (Phase 6.5): derives `GraphNode`/
`GraphEdge` lists from the latest completed run's `File`/`ApiEndpoint`/
`DependencyEdge` rows — no live GitHub call, purely a DB read.

No shared prefix on `router` — matches `dashboard.py`'s/`analysis.py`'s own
per-file `APIRouter()` convention (composed under `/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict

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
from app.schemas.architecture import ArchitectureRead, GraphEdge, GraphNode, ModuleKindLiteral
from app.services import analysis_read_service, repo_service
from app.services.module_classification import ModuleClassification, classify_modules

router = APIRouter(tags=["architecture"])

# Synthetic node id for the generic "external packages" catch-all node —
# a package that's real and external but not individually recognized
# below. Can never collide with a real module's id (every real group id
# is either a role-based key, a path fallback, or an "other:<kind>" fold —
# never the bare string "external").
EXTERNAL_NODE_ID = "external"

# --- named external-service recognition (Phase 6.5/6.6 follow-up) -----------
#
# Replaces a single generic "external" blob with recognized, individually
# labeled components when there's real evidence for one — grounded in
# persisted `DependencyEdge` facts (which package a file actually imports),
# never guessed from a route path or file name. A curated, documented MVP
# vocabulary, same spirit as `analysis/debt.py`'s own well-known-package
# table — a real external service this doesn't recognize still shows up
# honestly in the generic "External Dependencies" catch-all, not hidden.

# Prisma's real engine (from `AnalysisRun.database_provider`, set by
# `analysis/datasource.py` at analysis time) maps to a proper display name;
# unrecognized/absent falls back to a still-honest generic label.
_DB_PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "sqlserver": "SQL Server",
    "mongodb": "MongoDB",
    "cockroachdb": "CockroachDB",
}

# Known SaaS/infra service SDKs -> display label. Not exhaustive — an
# unrecognized real external package still shows up honestly in the
# generic "External Dependencies" node below, never hidden or guessed at.
_KNOWN_EXTERNAL_SERVICES: dict[str, str] = {
    "@deepgram/sdk": "Deepgram",
    "openai": "OpenAI",
    "@anthropic-ai/sdk": "Anthropic",
    "stripe": "Stripe",
    "twilio": "Twilio",
    "@sendgrid/mail": "SendGrid",
    "sendgrid": "SendGrid",
    "aws-sdk": "AWS",
    "googleapis": "Google Cloud",
    "@supabase/supabase-js": "Supabase",
}
# Direct DB drivers (not via Prisma) — their own node, kind=data, same as
# the Prisma-derived database node below but never merged with it (no
# reliable signal that they point at the same physical database).
_KNOWN_DATABASE_PACKAGES: dict[str, str] = {
    "pg": "PostgreSQL",
    "mysql2": "MySQL",
    "mongodb": "MongoDB",
    "mongoose": "MongoDB",
}
_KNOWN_CACHE_PACKAGES = {"ioredis", "redis"}

# Not real external services: Node.js builtins and dev/build-only tooling.
# Dropped from the external surface entirely — showing them as
# "architecture" would be noise, not signal.
_EXCLUDED_EXTERNAL_PACKAGES: set[str] = {
    # Node.js builtins.
    "fs", "path", "util", "os", "http", "https", "crypto", "stream",
    "events", "child_process", "url", "querystring", "buffer", "assert",
    "net", "zlib", "readline", "timers", "process",
    # Dev/build-only tooling.
    "vite", "eslint", "typescript-eslint", "prettier", "jest", "ts-jest",
    "ts-node", "nodemon", "supertest", "husky", "lint-staged", "globals",
    "typescript", "@vitejs/plugin-react", "@eslint/js",
    "eslint-plugin-react-hooks", "eslint-plugin-react-refresh",
}


def _database_node_id_and_label(database_provider: str | None) -> tuple[str, str]:
    if database_provider is None:
        return "data:database", "Database (Prisma)"
    label = _DB_PROVIDER_DISPLAY_NAMES.get(database_provider, database_provider.title())
    return "data:database", label


def _resolve_external_target(
    package: str, database_provider: str | None
) -> tuple[str, str, ModuleKindLiteral] | None:
    """Maps one real, imported external package to the synthetic node it
    should route to — or `None` if it's excluded entirely (a language
    builtin or dev/build tooling, not real runtime architecture)."""
    if package in _EXCLUDED_EXTERNAL_PACKAGES:
        return None
    if package == "@prisma/client":
        node_id, label = _database_node_id_and_label(database_provider)
        return node_id, label, "data"
    if package in _KNOWN_DATABASE_PACKAGES:
        return f"data:{package}", _KNOWN_DATABASE_PACKAGES[package], "data"
    if package in _KNOWN_CACHE_PACKAGES:
        return "cache:redis", "Redis", "cache"
    if package in _KNOWN_EXTERNAL_SERVICES:
        node_id = "external:" + package.lstrip("@").replace("/", "-")
        return node_id, _KNOWN_EXTERNAL_SERVICES[package], "external"
    return EXTERNAL_NODE_ID, "External Dependencies", "external"


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
    graph = analyze_dependency_graph(edges)
    circular_pairs = {(e.source_file, e.target_file) for e in graph.circular_edges}

    # A cycle whose files all sit inside one group produces no group-to-group
    # edge (`_build_edges` skips same-group pairs), so an edge flag alone would
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
    graph_edges, external_nodes = _build_edges(edges, classification, circular_pairs, current_run.database_provider)
    nodes.extend(external_nodes)

    return ArchitectureRead(
        analyzed=True,
        analysis_run_id=current_run.id,
        nodes=nodes,
        edges=graph_edges,
        cycle_count=len(graph.cycles),
    )


def _build_edges(
    edges: list[PureDependencyEdge],
    classification: ModuleClassification,
    circular_pairs: set[tuple[str, str | None]],
    database_provider: str | None,
) -> tuple[list[GraphEdge], list[GraphNode]]:
    """Aggregates file-level `DependencyEdge`s into group-to-group edges,
    plus whichever named external-service nodes (`_resolve_external_target`)
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
        resolved = _resolve_external_target(target_package, database_provider)
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
