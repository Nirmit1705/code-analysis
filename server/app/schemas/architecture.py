"""`GET /repos/{id}/architecture` response schemas (Phase 6.5).

Field names are chosen to translate cleanly to `client/src/lib/repo-data.ts`'s
mock `GraphNode`/`graphEdges` shapes once 6.6 wires the frontend, but this
schema is deliberately narrower than that mock in ways `docs/PHASE_6_PLAN.md`
calls out explicitly:

- No `x`/`y` — the backend doesn't compute layout; that's Phase 6.6's job
  (`graph-layout.ts`).
- No per-node `health` score. The plan gives 6.3's per-*repo* health
  metrics a fully-specified formula but never specifies a per-*module*
  one — inventing a number here would be exactly the kind of fabricated
  signal the `insight: ""` rule below already forbids for text. Revisit
  only if a future phase actually specifies a real per-module formula.
- `insight` is always `""` — no fake per-node AI text, same rule as
  `DashboardRead.ai_insights` (Phase 6.3).
"""

import uuid
from typing import Literal

from pydantic import BaseModel

ModuleKindLiteral = Literal["frontend", "gateway", "service", "data", "cache", "external"]


class GraphNode(BaseModel):
    id: str
    label: str
    kind: ModuleKindLiteral
    files: int
    loc: int
    key_files: list[str]
    apis: list[str]
    insight: str = ""


class GraphEdge(BaseModel):
    source: str
    target: str
    # Distinct (source_file, target_file) — or (source_file, target_package)
    # for an edge into the synthetic "external" node — pairs crossing these
    # two nodes' boundary, deduped the same way `analysis/graph.py`'s own
    # coupling computation dedupes multiple import statements between the
    # same two files into one count. Not a raw per-DependencyEdge-row count.
    weight: int
    # True when at least one underlying file-pair edge behind this
    # group-to-group edge is part of a real cycle, per
    # `analyze_dependency_graph().circular_edges` (Phase 4.2) — reused
    # directly, not a second cycle-detection pass over grouped nodes.
    circular: bool


class ArchitectureRead(BaseModel):
    """`analyzed=False` mirrors `DashboardRead`'s own "not analyzed yet"
    convention (Phase 6.3) — every other field left at its empty default
    rather than a broken empty aggregate."""

    analyzed: bool
    analysis_run_id: uuid.UUID | None = None
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
