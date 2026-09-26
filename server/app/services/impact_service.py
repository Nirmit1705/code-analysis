"""Change-impact analysis (Phase 9.2) — given a set of changed file paths
(from `analysis/change.py::diff_analysis_runs`'s `ChangeSet`), finds
everything downstream that could be affected and assigns a deterministic
risk bucket. The actual "impact" payoff `app/api/v1/impact.py` (9.3) exposes.

Not pure, unlike `analysis/graph.py`/`analysis/debt.py` — this is a
`services/` module, so it's allowed to (and does) query the DB directly,
same precedent `insight_service.py` (Phase 8.4) already established for
re-deriving graph facts at read time rather than threading them through
another layer.

Reuses `analysis_read_service.load_dependency_edges` + `analyze_dependency_
graph()` exactly as `app/api/v1/dependencies.py`/`insight_service.py` already
do — one graph pass produces both the reverse adjacency this module walks
and the `coupling`/`circular_files` facts `_compute_risk` folds in, never a
second hand-rolled pass over the same edges.

BFS direction is the mirror image of `analysis/graph.py::_detect_cycles`'s
own forward adjacency (`source_file -> target_file`, "what does this file
import"): here it's reversed (`target_file -> {source_file, ...}`, "who
imports this file") so a walk starting from the changed files answers "what
depends on what changed," not "what did the change depend on."
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.debt import COUPLING_SCORE_THRESHOLDS
from app.analysis.dependencies import DependencyEdge, DependencyEdgeType
from app.analysis.graph import CouplingScore, analyze_dependency_graph
from app.models.api_endpoint import ApiEndpoint
from app.models.file import File
from app.services import analysis_read_service

# Unvalidated round number, same "flag, don't preemptively tune" stance as
# every other round-number constant in this codebase (`analysis/debt.py`'s
# thresholds, the Phase 7 splitter/RRF constants) — revisit only if a real
# repo's results look off.
_MAX_IMPACT_DEPTH = 5

# Risk-score weights and the (medium, high) bucket thresholds below are also
# unvalidated round numbers, same stance. `COUPLING_SCORE_THRESHOLDS[0]`
# (imported from `analysis/debt.py`, not redefined) is the one exception —
# "flagged high-coupling" here means the exact same thing it means to the
# debt engine, not a second, silently-different definition of "coupled".
_SCORE_PER_DIRECT_FILE = 8.0
_SCORE_PER_INDIRECT_FILE = 3.0
_SCORE_PER_AFFECTED_API = 10.0
_SCORE_COUPLED_OR_CIRCULAR_BONUS = 25.0
_RISK_SCORE_THRESHOLDS: tuple[float, float] = (20.0, 50.0)  # (medium, high), ascending like debt.py's own tuples


@dataclass(frozen=True)
class ImpactedFile:
    path: str
    hops: int
    why: str


@dataclass(frozen=True)
class ImpactResult:
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    score: int
    direct: list[ImpactedFile]
    indirect: list[ImpactedFile]
    affected_apis: list[str]


def compute_impact(db: Session, *, head_analysis_run_id: uuid.UUID, changed_file_paths: set[str]) -> ImpactResult:
    """The single entry point. `changed_file_paths` is expected to be the
    union of a `ChangeSet`'s added/modified/removed file paths (9.1) — this
    function itself doesn't know or care where they came from."""
    edges = analysis_read_service.load_dependency_edges(db, head_analysis_run_id)
    graph = analyze_dependency_graph(edges)

    reverse_adjacency = _build_reverse_adjacency(edges)
    direct, indirect = _bfs_dependents(reverse_adjacency, changed_file_paths)

    affected_paths = changed_file_paths | {f.path for f in direct} | {f.path for f in indirect}
    affected_apis = _resolve_affected_apis(db, head_analysis_run_id, affected_paths)

    risk, score = _compute_risk(direct, indirect, affected_apis, graph.coupling, graph.circular_files)

    return ImpactResult(risk=risk, score=score, direct=direct, indirect=indirect, affected_apis=affected_apis)


# --- BFS over the reversed internal dependency graph ------------------------


def _build_reverse_adjacency(edges: list[DependencyEdge]) -> dict[str, set[str]]:
    """`target_file -> {source_file, ...}`, internal edges only — an
    `EXTERNAL` edge's target is a package name, not a file, so it has no
    "who imports this" question to answer."""
    reverse_adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.edge_type is not DependencyEdgeType.INTERNAL or edge.target_file is None:
            continue
        reverse_adjacency[edge.target_file].add(edge.source_file)
    return reverse_adjacency


def _bfs_dependents(
    reverse_adjacency: dict[str, set[str]], changed_file_paths: set[str]
) -> tuple[list[ImpactedFile], list[ImpactedFile]]:
    """Multi-source BFS from every changed file at once, capped at
    `_MAX_IMPACT_DEPTH`. `why` names the specific node each file was
    discovered through (a changed file for a depth-1 hit, per the plan's own
    example; another already-affected file for a deeper one) — file-grain
    only, per the pre-existing Known Issue #7 limitation, so this never
    claims a specific imported symbol. Processing order is sorted at every
    step so the result (which parent "wins" when a file is reachable from
    more than one changed file at the same depth) is deterministic."""
    depth_of: dict[str, int] = {path: 0 for path in changed_file_paths}
    parent: dict[str, str] = {}

    frontier = sorted(changed_file_paths)
    depth = 0
    while frontier and depth < _MAX_IMPACT_DEPTH:
        depth += 1
        next_frontier: list[str] = []
        for node in frontier:
            for dependent in sorted(reverse_adjacency.get(node, ())):
                if dependent in depth_of:
                    continue
                depth_of[dependent] = depth
                parent[dependent] = node
                next_frontier.append(dependent)
        frontier = next_frontier

    direct = [
        ImpactedFile(path=path, hops=1, why=f"imports `{parent[path]}`")
        for path in sorted(path for path, d in depth_of.items() if d == 1)
    ]
    indirect = [
        ImpactedFile(path=path, hops=d, why=f"imports `{parent[path]}`")
        for d, path in sorted((d, path) for path, d in depth_of.items() if d >= 2)
    ]
    return direct, indirect


# --- affected APIs -----------------------------------------------------------


def _resolve_affected_apis(db: Session, analysis_run_id: uuid.UUID, affected_paths: set[str]) -> list[str]:
    """`"METHOD /path"` strings for every `ApiEndpoint` whose file falls in
    `affected_paths` — matches the plain-string shape `schemas/compare.py`'s
    `added_apis`/`removed_apis` already use, so both screens render API
    impact the same way."""
    if not affected_paths:
        return []
    file_ids = db.execute(
        select(File.id).where(File.analysis_run_id == analysis_run_id, File.path.in_(affected_paths))
    ).scalars().all()
    if not file_ids:
        return []
    api_rows = db.execute(
        select(ApiEndpoint.method, ApiEndpoint.path)
        .where(ApiEndpoint.analysis_run_id == analysis_run_id, ApiEndpoint.file_id.in_(file_ids))
        .order_by(ApiEndpoint.method, ApiEndpoint.path)
    ).all()
    return [f"{row.method} {row.path}" for row in api_rows]


# --- risk scoring -------------------------------------------------------------


def _compute_risk(
    direct: list[ImpactedFile],
    indirect: list[ImpactedFile],
    affected_apis: list[str],
    coupling: list[CouplingScore],
    circular_files: frozenset[str],
) -> tuple[Literal["LOW", "MEDIUM", "HIGH"], int]:
    affected_paths = {f.path for f in direct} | {f.path for f in indirect}
    touches_circular = bool(affected_paths & circular_files)

    coupling_by_module = {c.module: c for c in coupling}
    affected_modules = {_module_of(path) for path in affected_paths}
    touches_high_coupling = any(
        coupling_by_module[module].score >= COUPLING_SCORE_THRESHOLDS[0]
        for module in affected_modules
        if module in coupling_by_module
    )

    score = (
        len(direct) * _SCORE_PER_DIRECT_FILE
        + len(indirect) * _SCORE_PER_INDIRECT_FILE
        + len(affected_apis) * _SCORE_PER_AFFECTED_API
        + (_SCORE_COUPLED_OR_CIRCULAR_BONUS if (touches_circular or touches_high_coupling) else 0.0)
    )
    score = min(100.0, score)

    medium, high = _RISK_SCORE_THRESHOLDS
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    if score >= high:
        risk = "HIGH"
    elif score >= medium:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    return risk, int(round(score))


def _module_of(file_path: str) -> str:
    """Same "containing directory" convention as `analysis/graph.py`'s own
    (private) `_module_of` — duplicated locally rather than imported across
    a module boundary, per this codebase's established small-helper
    convention."""
    return str(PurePosixPath(file_path).parent)
