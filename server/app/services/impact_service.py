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

import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.change import ChangeKind
from app.analysis.dependencies import DependencyEdge, DependencyEdgeType
from app.analysis.graph import analyze_dependency_graph
from app.models.api_endpoint import ApiEndpoint
from app.models.file import File
from app.services import analysis_read_service

# Unvalidated round number, same "flag, don't preemptively tune" stance as
# every other round-number constant in this codebase (`analysis/debt.py`'s
# thresholds, the Phase 7 splitter/RRF constants) — revisit only if a real
# repo's results look off.
_MAX_IMPACT_DEPTH = 5

# Risk-score constants are unvalidated round numbers, same stance. Reworked
# after the first live run (docs/PHASE_9_PLAN.md, 9.5 follow-up): the original
# linear +10-per-API / +25-coupling-bonus formula scored a one-file, no-surface-
# change commit HIGH/100, and across every real HireSight file treated as the
# sole change it produced 78 HIGH / 2 MEDIUM / 56 LOW (the coupling bonus fired
# for *every* file with a dependent - 11 of the repo's 18 modules exceed the
# debt engine's coupling bar, so it carried no signal). The current model:
#
#   score = min(100, (file_points + api_points + cycle_bonus) * change_multiplier)
#
# * file_points - each *non-test* dependent, weighted by how far away it is.
# * api_points - log-scaled so a wide API surface can't saturate the score alone.
# * cycle_bonus - only when a *changed* file is itself part of a real cycle.
# * change_multiplier - a change that removed a file/symbol/endpoint keeps full
#   weight; one that removed nothing gets a mild (not large) discount, since
#   behaviour changes inside a body are not detectable from symbols/LOC.
_FILE_POINTS_BY_HOP: dict[int, float] = {1: 6.0, 2: 3.0, 3: 1.5}
_FILE_POINTS_DEEP = 1.0  # hop >= 4
_API_POINTS_COEFFICIENT = 5.0  # api_points = coefficient * log2(1 + affected API count)
_CYCLE_BONUS = 15.0
_CHANGE_MULTIPLIER: dict[ChangeKind, float] = {"breaking": 1.0, "non_breaking": 0.75}
_RISK_SCORE_THRESHOLDS: tuple[float, float] = (20.0, 50.0)  # (medium, high), ascending like debt.py's own tuples

# Test files a change reaches are a "re-run these" signal, not downstream
# breakage - still listed in `direct`/`indirect`, never scored. Path-name
# heuristic only (there is no real test/coverage detection in this codebase).
_TEST_DIR_NAMES = frozenset({"__tests__", "tests", "test", "spec", "specs", "e2e"})
_TEST_FILE_RE = re.compile(r"(\.(test|spec)\.[^./]+$)|(^test_[^/]*\.py$)|(_test\.py$)|(Tests?\.java$)")


@dataclass(frozen=True)
class ImpactedFile:
    path: str
    hops: int
    why: str


@dataclass(frozen=True)
class ScoreBreakdown:
    """How `score` was reached, so the UI never has to guess or claim a cause
    it can't prove. `file_points + api_points + cycle_bonus`, times
    `multiplier`, capped at 100, is `score`."""

    file_points: float
    api_points: float
    cycle_bonus: float
    change_kind: ChangeKind
    multiplier: float
    excluded_test_files: int  # test files reached by the change but not scored


@dataclass(frozen=True)
class ImpactResult:
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    score: int
    direct: list[ImpactedFile]
    indirect: list[ImpactedFile]
    affected_apis: list[str]
    breakdown: ScoreBreakdown


def compute_impact(
    db: Session,
    *,
    head_analysis_run_id: uuid.UUID,
    changed_file_paths: set[str],
    change_kind: ChangeKind,
) -> ImpactResult:
    """The single entry point. `changed_file_paths` is expected to be the
    union of a `ChangeSet`'s added/modified/removed file paths (9.1) and
    `change_kind` is `analysis.change.classify_change(change_set)` — this
    function itself doesn't know or care where they came from."""
    edges = analysis_read_service.load_dependency_edges(db, head_analysis_run_id)
    graph = analyze_dependency_graph(edges)

    reverse_adjacency = _build_reverse_adjacency(edges)
    direct, indirect = _bfs_dependents(reverse_adjacency, changed_file_paths)

    # Test files are listed in `direct`/`indirect` but never scored, and an
    # endpoint is only ever counted from the changed + non-test files.
    scored_dependents = [f for f in direct + indirect if not _is_test_path(f.path)]
    affected_paths = changed_file_paths | {f.path for f in scored_dependents}
    affected_apis = _resolve_affected_apis(db, head_analysis_run_id, affected_paths)

    risk, score, breakdown = _compute_risk(
        direct,
        indirect,
        affected_apis,
        changed_file_paths=changed_file_paths,
        circular_files=graph.circular_files,
        change_kind=change_kind,
    )

    return ImpactResult(
        risk=risk, score=score, direct=direct, indirect=indirect, affected_apis=affected_apis, breakdown=breakdown
    )


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


def _is_test_path(file_path: str) -> bool:
    path = PurePosixPath(file_path)
    if any(part.lower() in _TEST_DIR_NAMES for part in path.parts[:-1]):
        return True
    return bool(_TEST_FILE_RE.search(path.name))


def _compute_risk(
    direct: list[ImpactedFile],
    indirect: list[ImpactedFile],
    affected_apis: list[str],
    *,
    changed_file_paths: set[str],
    circular_files: frozenset[str],
    change_kind: ChangeKind,
) -> tuple[Literal["LOW", "MEDIUM", "HIGH"], int, ScoreBreakdown]:
    dependents = direct + indirect
    scored = [f for f in dependents if not _is_test_path(f.path)]

    file_points = sum(_FILE_POINTS_BY_HOP.get(f.hops, _FILE_POINTS_DEEP) for f in scored)
    api_points = _API_POINTS_COEFFICIENT * math.log2(1 + len(affected_apis))
    cycle_bonus = _CYCLE_BONUS if changed_file_paths & circular_files else 0.0
    multiplier = _CHANGE_MULTIPLIER[change_kind]

    score = min(100.0, (file_points + api_points + cycle_bonus) * multiplier)

    medium, high = _RISK_SCORE_THRESHOLDS
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    if score >= high:
        risk = "HIGH"
    elif score >= medium:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    breakdown = ScoreBreakdown(
        file_points=round(file_points, 1),
        api_points=round(api_points, 1),
        cycle_bonus=cycle_bonus,
        change_kind=change_kind,
        multiplier=multiplier,
        excluded_test_files=len(dependents) - len(scored),
    )
    return risk, int(round(score)), breakdown
