"""`GET /repos/{id}/dependencies` response schemas (Phase 6.9).

`circular`/`coupled`/`orphans` mirror Phase 4.2's `analysis/graph.py`
dataclasses (`CycleResult`/`CouplingScore`/`GraphAnalysis.orphans`)
one-for-one — re-derived fresh at read time via `analysis_read_service
.load_dependency_edges` + `analyze_dependency_graph()`, the exact same
technique Architecture (6.5) already established for its own circular-edge
highlighting, not a second hand-rolled graph pass. `coupled` is the full
ranking, not gated by whether a module happens to cross
`analysis/debt.py`'s own EXCESSIVE_COUPLING threshold — the UI's
"leaderboard" intent is real even for a non-flagged module (per
`docs/PHASE_6_PLAN.md`'s 6.9 design decision) — so, matching the mock
`dependencyRisks.coupled` shape, it carries no `severity` field at all
(inventing one for every module, threshold-crossing or not, would be
exactly the kind of fabricated signal the `insight`/`ai_insights` empty
rules already forbid elsewhere).

`CircularGroup` is a real strongly-connected component (`files`), not a
pairwise `{a, b}` shape like the mock's own `dependencyRisks.circular` —
a real cycle routinely spans more than two files (confirmed against
`Nirmit1705/HireSight`'s own real 14-file cycle during this sub-phase's
verification).

**External-dependency data is honestly limited to what Phase 4.3's naive
version-age heuristic actually persisted** (`docs/PHASE_6_PLAN.md`'s 6.9
design decision, load-bearing): there is no full package inventory anywhere
in this codebase, and no CVE field is ever populated (never faked, always
omitted — same rule as `ai_insights`/`insight`). Rather than regex-parsing
`DebtIssue.description`'s free-text sentence apart into separate
installed/latest-version fields, `ExternalDependencyFinding` exposes the
ready-made `description` sentence plus `majors_behind` directly — the
frontend's "Installed"/"Latest" columns become a single "Finding" column
(Phase 6.10). No `version`/`latest` fields exist here at all.

Field names snake_case throughout, matching this codebase's actual
established convention (confirmed in 6.6/6.7's own notes — `dashboard.py`'s
docstring claim of a camelCase frontend translation doesn't reflect any
real query module).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel

DependencySeverity = Literal["low", "medium", "high", "critical"]


class CircularGroup(BaseModel):
    """One `CycleResult` (Phase 4.2) that qualifies as a real cycle (see
    `graph.py`'s own module docstring for the size/self-loop rule). `files`
    is sorted for deterministic output; `edge_count` is
    `len(CycleResult.edges)` — the real internal `DependencyEdge` count
    participating in this cycle, not just `len(files)`."""

    files: list[str]
    edge_count: int
    severity: DependencySeverity


class CoupledModule(BaseModel):
    """One `CouplingScore` (Phase 4.2), verbatim — every module that
    touches at least one internal dependency edge, sorted by score desc
    then module asc (`GraphAnalysis.coupling`'s own order, unchanged
    here)."""

    module: str
    fan_in: int
    fan_out: int
    score: int


class ExternalDependencyFinding(BaseModel):
    """One persisted `DebtIssue` row of type `OUTDATED_DEPENDENCY` for the
    latest completed run. `name` is parsed off the issue's own
    `{name}@{version} is N major version(s) behind ...` description
    (Phase 4.3's fixed sentence shape) — not a separately stored column,
    since Known Issue #11 keeps `DebtIssue.metric_value` to one float per
    rule type. `used_by` is counted fresh against this run's `EXTERNAL`
    `DependencyEdge` rows (distinct importing files), not trusted from
    anything `debt.py` itself recorded."""

    name: str
    description: str
    severity: DependencySeverity
    majors_behind: int
    used_by: int


class DependenciesRead(BaseModel):
    """`analyzed=False` mirrors `DashboardRead`/`ArchitectureRead`/
    `FileTreeRead`'s own "not analyzed yet" convention (Phases 6.3/6.5/6.7)
    — every other field left at its empty default rather than a broken
    empty aggregate."""

    analyzed: bool
    analysis_run_id: uuid.UUID | None = None
    circular: list[CircularGroup] = []
    coupled: list[CoupledModule] = []
    orphans: list[str] = []
    external: list[ExternalDependencyFinding] = []
