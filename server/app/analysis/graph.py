"""Internal dependency graph analysis (Phase 4.2): cycle detection,
per-module coupling scoring, and orphan-file detection — built purely from
Phase 3.7's `DependencyEdge` dataclass list (`app.analysis.dependencies`,
produced by `analysis/dependencies/imports.py`).

Same DB-free discipline as the rest of `analysis/`: takes a plain
`list[DependencyEdge]` in, returns plain dataclasses out. No SQLAlchemy/ORM
rows, no DB session, no FastAPI, no Celery, no persistence — Phase 4.4's
`persist_analysis_run` is the caller that turns this module's results into
`DependencyEdge.is_circular` updates (the *DB* model of that name — the
Phase 4.1 `models/dependency.py` table, not this module's input dataclass)
and `dependencyRisks.coupled`/`.orphans` data.

Operates at the same file/module granularity as the Phase 3.7 facts it
consumes (Known Issue #7 in `docs/BACKEND_IMPLEMENTATION_PLAN.md`: a Python
`from X import a, b, c` is one edge to module `X`, not per imported name) —
not a bug to fix here, a deliberate boundary this module doesn't redesign.

Three analyses, all built only from `edge_type is INTERNAL` edges (an
external-package edge has no `target_file` and isn't part of the internal
graph at all):

1. **Cycle detection** — Tarjan's strongly-connected-components algorithm
   (iterative, not recursive, so a long import chain in a large repo can't
   blow the interpreter's recursion limit) over the file-level internal
   adjacency. An SCC counts as a real cycle when it has more than one file,
   or is a single file with a self-loop (a file that, however indirectly,
   imports itself) — a lone file with no self-loop is never reported even
   though Tarjan always emits it as a trivial one-node SCC.
2. **Per-module coupling** — fan-in/fan-out counted over internal edges
   that cross a module boundary (`DependencyEdge.module` for the source,
   the same "containing directory" convention applied to `target_file` for
   the destination). An edge whose source and target land in the same
   module (a file importing a sibling in its own directory) isn't
   inter-module coupling and doesn't count toward either side — it's
   internal cohesion, not coupling. This matches the standard package-level
   coupling convention (Robert C. Martin's afferent/efferent coupling,
   Ca/Ce), which by definition only counts cross-package dependencies, not
   a decision unique to this module. Counted per distinct
   `(source_file, target_file)` pair, not per `DependencyEdge` — a single
   file importing the same target file via more than one import statement
   (e.g. TypeScript's routine split of a type-only import and a value
   import onto separate lines, `import type {X} from './y'` +
   `import {z} from './y'`) is one file-to-file dependency, not two, and
   shouldn't inflate either module's score just because it was written on
   two lines. A module is omitted from `coupling` only when it never
   touches an internal edge at all — neither as a source nor a target,
   mirroring orphan detection's own "confined to what the edge list
   actually mentions" scope limit below. A module that *does* touch an
   internal edge but only ever a same-module one (every file in it only
   imports siblings in its own directory, or a lone file imports itself)
   still appears, with `fan_in=0, fan_out=0, score=0` — being touched by
   an internal edge is what registers a module here, independent of
   whether that particular edge counted toward either side's tally.
3. **Orphan detection** — a file with zero internal in-edges *and* zero
   internal out-edges. Confined to the file universe this edge list
   actually mentions (every edge's `source_file`, plus every internal
   edge's `target_file`) — a file with literally zero import statements
   never appears as a `DependencyEdge` source at all, so it's outside what
   this function can see and isn't reported; that's a scope limit of
   taking `list[DependencyEdge]` alone as input (this phase doesn't also
   receive the full ingested file list), not a bug. A file whose only
   imports are external (e.g. `import os`) still counts as an orphan of
   the *internal* graph — it appears in the universe (as an edge source)
   but has no internal edges either way.

Pure with respect to the rest of the app: no DB, no ORM, no FastAPI, no
Celery.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.analysis.dependencies import DependencyEdge, DependencyEdgeType


@dataclass(frozen=True)
class CycleResult:
    """One strongly-connected component of the internal file dependency
    graph that qualifies as a real cycle (see module docstring's SCC-size
    rule). `files` is every file participating in it; `edges` is exactly
    the internal `DependencyEdge`s whose source and target both fall
    inside it — the edges `persist_analysis_run` (Phase 4.4) should mark
    `is_circular=True` on."""

    files: frozenset[str]
    edges: tuple[DependencyEdge, ...]


@dataclass(frozen=True)
class CouplingScore:
    """Fan-in/fan-out for one module (see module docstring point 2 for
    what counts, including the per-file-pair dedup). `score` is the plain
    sum, per the plan's own "fan-in + fan-out" wording — not normalized to
    any particular range; that's a later phase's call once something
    concrete consumes it (e.g. Phase 4.3's debt threshold or Phase 6's UI
    mapping), not invented here without a spec to match."""

    module: str
    fan_in: int
    fan_out: int
    score: int


@dataclass(frozen=True)
class GraphAnalysis:
    cycles: list[CycleResult]
    circular_files: frozenset[str]  # union of every CycleResult.files, for a quick membership check
    circular_edges: tuple[DependencyEdge, ...]  # flattened union of every CycleResult.edges
    coupling: list[CouplingScore]  # sorted by score desc, then module asc; a module with NO internal edges at all is omitted — one with only same-module internal edges still appears, scored 0
    orphans: list[str]  # sorted file paths


def analyze_dependency_graph(edges: list[DependencyEdge]) -> GraphAnalysis:
    """The single entry point: runs all three analyses over `edges` (the
    full Phase 3.7 `DependencyEdge` list for one analysis run — internal
    and external mixed together, exactly as `extract_import_edges`
    returns it) and returns one bundled result."""
    internal_edges = [e for e in edges if e.edge_type is DependencyEdgeType.INTERNAL and e.target_file is not None]

    cycles = _detect_cycles(internal_edges)
    circular_files = frozenset(f for cycle in cycles for f in cycle.files)
    circular_edges = tuple(e for cycle in cycles for e in cycle.edges)
    coupling = _compute_coupling(internal_edges)
    orphans = _detect_orphans(edges, internal_edges)

    return GraphAnalysis(
        cycles=cycles,
        circular_files=circular_files,
        circular_edges=circular_edges,
        coupling=coupling,
        orphans=orphans,
    )


# --- cycle detection (iterative Tarjan SCC) ------------------------------------


def _detect_cycles(internal_edges: list[DependencyEdge]) -> list[CycleResult]:
    # A set, not a list: two import statements between the same file pair
    # (see the module docstring's coupling section) would otherwise add a
    # duplicate neighbor here too — harmless for Tarjan's correctness
    # (it's duplicate-neighbor-tolerant) but wasted iteration. `component_
    # edges` below still walks the *undeduped* `internal_edges`, so every
    # real edge in a cycle is still reported.
    adjacency: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for edge in internal_edges:
        assert edge.target_file is not None  # narrowed by the caller's filter
        adjacency[edge.source_file].add(edge.target_file)
        nodes.add(edge.source_file)
        nodes.add(edge.target_file)

    components = _tarjan_scc(adjacency, nodes)

    cycles: list[CycleResult] = []
    for component in components:
        component_edges = tuple(
            edge for edge in internal_edges if edge.source_file in component and edge.target_file in component
        )
        is_self_loop = len(component) == 1 and any(edge.source_file == edge.target_file for edge in component_edges)
        if len(component) > 1 or is_self_loop:
            cycles.append(CycleResult(files=component, edges=component_edges))

    cycles.sort(key=lambda c: min(c.files))
    return cycles


def _tarjan_scc(adjacency: dict[str, set[str]], nodes: set[str]) -> list[frozenset[str]]:
    """Standard iterative Tarjan's SCC algorithm — an explicit work stack
    of `(node, next_neighbor_index)` frames stands in for the call stack a
    recursive version would use, so this doesn't risk Python's recursion
    limit on a long chain of imports. Returns every SCC, including trivial
    single-node ones with no self-loop; `_detect_cycles` is the one that
    decides which of those actually count as a cycle."""
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    tarjan_stack: list[str] = []
    next_index = 0
    sccs: list[frozenset[str]] = []

    for start in sorted(nodes):  # sorted only for deterministic SCC discovery order
        if start in index_of:
            continue
        work: list[list] = [[start, 0]]  # each frame: [node, next child index to visit]
        while work:
            frame = work[-1]
            node, next_i = frame[0], frame[1]
            if next_i == 0:
                index_of[node] = next_index
                lowlink[node] = next_index
                next_index += 1
                tarjan_stack.append(node)
                on_stack[node] = True

            # sorted(): a set has no stable iteration order across
            # processes (str hash randomization) — sorting keeps traversal,
            # and therefore SCC discovery order, deterministic.
            neighbors = sorted(adjacency.get(node, ()))
            descended = False
            i = next_i
            while i < len(neighbors):
                neighbor = neighbors[i]
                if neighbor not in index_of:
                    frame[1] = i + 1
                    work.append([neighbor, 0])
                    descended = True
                    break
                if on_stack.get(neighbor):
                    lowlink[node] = min(lowlink[node], index_of[neighbor])
                i += 1
            if descended:
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])

            if lowlink[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = tarjan_stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                sccs.append(frozenset(component))

    return sccs


# --- coupling --------------------------------------------------------------


def _compute_coupling(internal_edges: list[DependencyEdge]) -> list[CouplingScore]:
    # Dedupe to distinct (source_file, target_file) pairs before tallying —
    # see the module docstring's coupling section for why (two import
    # statements between the same two files are one file-to-file
    # dependency, not two). Derived via `_module_of` on both sides rather
    # than reading `edge.module` for the source, so this doesn't depend on
    # picking one representative edge out of a duplicate group.
    file_pairs = {(edge.source_file, edge.target_file) for edge in internal_edges}

    fan_out: dict[str, int] = defaultdict(int)
    fan_in: dict[str, int] = defaultdict(int)
    modules: set[str] = set()

    for source_file, target_file in file_pairs:
        assert target_file is not None
        source_module = _module_of(source_file)
        target_module = _module_of(target_file)
        modules.add(source_module)
        modules.add(target_module)
        if source_module == target_module:
            continue  # same-module import: cohesion, not inter-module coupling
        fan_out[source_module] += 1
        fan_in[target_module] += 1

    scores = [
        CouplingScore(module=module, fan_in=fan_in.get(module, 0), fan_out=fan_out.get(module, 0), score=fan_in.get(module, 0) + fan_out.get(module, 0))
        for module in modules
    ]
    return sorted(scores, key=lambda c: (-c.score, c.module))


def _module_of(file_path: str) -> str:
    """Same "containing directory" convention as `Symbol.module` and
    `DependencyEdge.module` itself (see `imports.py`) — applied here to a
    `target_file`, which only ever carries the bare file path, not its
    precomputed module."""
    return str(PurePosixPath(file_path).parent)


# --- orphans -----------------------------------------------------------------


def _detect_orphans(all_edges: list[DependencyEdge], internal_edges: list[DependencyEdge]) -> list[str]:
    universe: set[str] = {edge.source_file for edge in all_edges}
    universe |= {edge.target_file for edge in internal_edges if edge.target_file is not None}

    touches_internal_edge: set[str] = set()
    for edge in internal_edges:
        assert edge.target_file is not None
        touches_internal_edge.add(edge.source_file)
        touches_internal_edge.add(edge.target_file)

    return sorted(universe - touches_internal_edge)
