"""Pure diff between two already-persisted analysis runs (Phase 9.1) — the
foundation both Compare (`app/api/v1/compare.py`) and Impact
(`app/services/impact_service.py`) build on.

Same DB-free discipline as the rest of `analysis/` (see `analysis/graph.py`'s
own module docstring): takes two plain `RunFacts` bundles in, returns a plain
`ChangeSet` out. `app/services/analysis_read_service.py::load_run_facts` is
the caller that builds `RunFacts` from real `File`/`Symbol`/`ApiEndpoint`
rows plus the existing `load_dependency_edges`; this module never touches a
DB session or ORM object itself.

Design decisions (full reasoning in `docs/PHASE_9_PLAN.md`'s own list,
decisions #5/#6):

- Files, symbols, APIs, and dependency edges are each matched on a natural
  key, not a stable id — nothing here has a stable identity across two runs
  (a re-analysis always creates fresh `File`/`Symbol`/`ApiEndpoint` rows,
  same precedent `file_insight_service.py`'s own docstring already notes for
  `File`). Anything only in `head` is "added"; anything only in `base` is
  "removed".
- "Modified" is symbol/LOC-based, not a text diff. No file-content hash is
  stored anywhere in this schema (Known Issue #18's binary-detection fix
  didn't change this), so a path present in both runs counts as `modified`
  iff its symbol set differs or its `loc` differs. A formatting-only change
  with identical symbols and identical LOC would not register as modified —
  a real, accepted limitation, not silently glossed over.
- Dependency edges are matched on `(source_file, target, edge_type)`, where
  `target` is `target_file` for an `INTERNAL` edge and `target_package` for
  an `EXTERNAL` one — mirroring `analysis/graph.py`'s own "internal edges
  carry a file, external edges carry a package name" split.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from app.analysis.dependencies import DependencyEdge, DependencyEdgeType
from app.analysis.language_detect import Language
from app.analysis.symbols import SymbolKind


@dataclass(frozen=True)
class FileFact:
    path: str
    language: Language | None
    loc: int


@dataclass(frozen=True)
class SymbolFact:
    file_path: str
    name: str
    kind: SymbolKind


@dataclass(frozen=True)
class ApiFact:
    method: str
    path: str
    file_path: str


@dataclass(frozen=True)
class RunFacts:
    """One analysis run's facts, already reconstructed as plain dataclasses
    — everything `diff_analysis_runs` needs, and nothing it doesn't (no
    debt/coupling/AI data; those are separate concerns for later Phase 9
    sub-phases)."""

    files: list[FileFact]
    symbols: list[SymbolFact]
    apis: list[ApiFact]
    edges: list[DependencyEdge]


@dataclass(frozen=True)
class ChangeSet:
    added_files: list[FileFact]
    removed_files: list[FileFact]
    modified_files: list[str]
    added_symbols: list[SymbolFact]
    removed_symbols: list[SymbolFact]
    added_apis: list[ApiFact]
    removed_apis: list[ApiFact]
    added_edges: list[DependencyEdge]
    removed_edges: list[DependencyEdge]


def diff_analysis_runs(base: RunFacts, head: RunFacts) -> ChangeSet:
    """The single entry point: diffs every fact category independently and
    returns one bundled result. `base` is the earlier (predecessor) run,
    `head` the later one — "added" always means "present in `head`, absent
    from `base`", matching plain git diff terminology."""
    added_files, removed_files = _diff_by_key(base.files, head.files, _file_key)
    added_symbols, removed_symbols = _diff_by_key(base.symbols, head.symbols, _symbol_key)
    added_apis, removed_apis = _diff_by_key(base.apis, head.apis, _api_key)
    added_edges, removed_edges = _diff_by_key(base.edges, head.edges, _edge_key)

    return ChangeSet(
        added_files=sorted(added_files, key=_file_key),
        removed_files=sorted(removed_files, key=_file_key),
        modified_files=_modified_files(base, head),
        added_symbols=sorted(added_symbols, key=_symbol_key),
        removed_symbols=sorted(removed_symbols, key=_symbol_key),
        added_apis=sorted(added_apis, key=_api_key),
        removed_apis=sorted(removed_apis, key=_api_key),
        added_edges=sorted(added_edges, key=_edge_key),
        removed_edges=sorted(removed_edges, key=_edge_key),
    )


# --- natural keys ------------------------------------------------------------


def _file_key(fact: FileFact) -> str:
    return fact.path


def _symbol_key(fact: SymbolFact) -> tuple[str, str, SymbolKind]:
    return (fact.file_path, fact.name, fact.kind)


def _api_key(fact: ApiFact) -> tuple[str, str]:
    return (fact.method, fact.path)


def _edge_key(edge: DependencyEdge) -> tuple[str, str, DependencyEdgeType]:
    # `or ""` guards the rare, not-a-real-code-path case `load_dependency_edges`
    # itself already flags (an INTERNAL edge whose target File row went
    # missing) — never lets a `None` reach a sort comparison against `str`.
    target = edge.target_file if edge.edge_type is DependencyEdgeType.INTERNAL else edge.target_package
    return (edge.source_file, target or "", edge.edge_type)


# --- generic added/removed diff ---------------------------------------------

_T = TypeVar("_T")
_K = TypeVar("_K")


def _diff_by_key(base_items: list[_T], head_items: list[_T], key_fn) -> tuple[list[_T], list[_T]]:
    """Everything in `head_items` whose key doesn't appear in `base_items`
    is "added"; everything in `base_items` whose key doesn't appear in
    `head_items` is "removed" — a changed-but-still-present key (e.g. a
    dependency edge whose `raw_specifier` differs but whose natural key is
    unchanged) is neither, matching this module's own "match by natural
    key, not by full equality" design."""
    base_by_key: dict[_K, _T] = {key_fn(item): item for item in base_items}
    head_by_key: dict[_K, _T] = {key_fn(item): item for item in head_items}
    added = [head_by_key[key] for key in head_by_key.keys() - base_by_key.keys()]
    removed = [base_by_key[key] for key in base_by_key.keys() - head_by_key.keys()]
    return added, removed


# --- modified files -----------------------------------------------------------


def _modified_files(base: RunFacts, head: RunFacts) -> list[str]:
    base_files_by_path = {fact.path: fact for fact in base.files}
    head_files_by_path = {fact.path: fact for fact in head.files}
    common_paths = base_files_by_path.keys() & head_files_by_path.keys()

    base_symbols_by_path: dict[str, set[tuple[str, SymbolKind]]] = {}
    for symbol in base.symbols:
        base_symbols_by_path.setdefault(symbol.file_path, set()).add((symbol.name, symbol.kind))
    head_symbols_by_path: dict[str, set[tuple[str, SymbolKind]]] = {}
    for symbol in head.symbols:
        head_symbols_by_path.setdefault(symbol.file_path, set()).add((symbol.name, symbol.kind))

    modified = [
        path
        for path in common_paths
        if base_files_by_path[path].loc != head_files_by_path[path].loc
        or base_symbols_by_path.get(path, set()) != head_symbols_by_path.get(path, set())
    ]
    return sorted(modified)
