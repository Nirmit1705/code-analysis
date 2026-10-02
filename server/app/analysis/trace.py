"""Request-flow tracing for the Flow Tracer (Phase 10.5): from one start file
(a route's file, or any file the user picks), which files does a request's
path plausibly run through, layer by layer — computed from the real import
graph, never guessed.

Pure, same discipline as the rest of `analysis/`: plain mappings in, plain
frozen dataclasses out, no DB/ORM/FastAPI. The caller (Phase 10.6's
`services/trace_service.py`) loads one run's facts and resolves external
packages to named services (`services/external_services.py`) before calling
in, so this module never knows about Prisma, Redis or SDK names.

**File-grain, by construction** (Known Issue #7): edges are file imports, so a
hop means "this file is imported along the path", never "this function calls
that function". Only the start hop carries a function name (the route's
handler, when the extractor named one); every other hop's `fn` is `None` —
a function name is never invented.

Traversal (docs/PHASE_10_PLAN.md decision #5):
- **Forward BFS** over internal imports from the start file.
- **Rank-monotone**: a hop only reaches files whose layer rank is >= its own
  (`analysis/layers.py::LAYER_RANK` — route < controller < service <
  repository < model). Same-layer (service -> service) and skip-layer
  (route -> service) imports are followed; a backwards import (a service
  importing a controller) is ignored. A start file whose own layer is `other`
  is treated as the outermost rank, so everything below it is reachable.
- **`other` files are pass-through, not hops**: a hop's imports are followed
  *through* any chain of `other` files (utils/config/types/middleware). Ranked
  files found beyond them become hops with that chain recorded in `via`; the
  external services those `other` files import attach to the hop they were
  reached from (so `authService -> config/database -> @prisma/client` still
  ends in a database on the `authService` hop, with `via` naming the file).
- **Test files are excluded** entirely (neither hops nor pass-through) — a
  path-name heuristic duplicated from `services/impact_service.py`'s own,
  per the repo's duplicate-small-helpers convention.
- **Bounded**: depth <= `MAX_DEPTH`, hops <= `MAX_HOPS` (start included), a
  visited set so a cycle terminates; `truncated` says a bound cut something
  off.
- **Deterministic order**: hops are numbered by `(depth, rank, path)`, so a
  hop's `parent_index` is always smaller than its own `index`. When several
  hops at one depth import the same file, the lowest-index parent claims it.
  The list is breadth-first, not a pre-order tree walk — a client rendering a
  tree should nest by `parent_index`, not by list position.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.analysis.layers import LAYER_RANK, Layer

MAX_DEPTH = 8
MAX_HOPS = 25

# Same heuristic as `services/impact_service.py` (duplicated, not imported —
# small helpers stay local per module, an established convention here).
_TEST_DIR_NAMES = frozenset({"__tests__", "tests", "test", "spec", "specs", "e2e"})
_TEST_FILE_RE = re.compile(r"(\.(test|spec)\.[^./]+$)|(^test_[^/]*\.py$)|(_test\.py$)|(Tests?\.java$)")


@dataclass(frozen=True)
class ExternalRef:
    """One external package a file imports, already resolved by the caller to
    the named service it belongs to (`services/external_services.py`'s
    `resolve_external_target`). `kind` is that function's kind literal
    (`"data"`, `"cache"`, `"external"`, ...)."""

    service_id: str
    label: str
    kind: str
    package: str


@dataclass(frozen=True)
class HopExternal:
    """A named external service a hop reaches: directly (`via == ()`) or
    through a chain of pass-through `other` files (`via` = that chain, the
    last entry being the file that imports the package). When it is reached
    more than one way, the shortest chain is kept (ties: lexicographic)."""

    service_id: str
    label: str
    kind: str
    packages: tuple[str, ...]
    via: tuple[str, ...]


@dataclass(frozen=True)
class TraceHop:
    index: int
    depth: int
    parent_index: int | None  # None only for the start hop
    layer: Layer
    file_path: str
    fn: str | None  # the route handler's name on the start hop; never guessed elsewhere
    # Pass-through `other` files between the parent hop and this one (empty =
    # the parent imports this file directly).
    via: tuple[str, ...]
    deps: tuple[str, ...]  # this file's own direct internal imports (test files excluded), sorted
    external_services: tuple[HopExternal, ...]  # sorted by service_id


@dataclass(frozen=True)
class Trace:
    hops: tuple[TraceHop, ...]
    truncated: bool

    @property
    def layers_reached(self) -> list[Layer]:
        """Distinct hop layers, outermost first (`other` last, if the start was one)."""
        present = {hop.layer for hop in self.hops}
        return [layer for layer in Layer if layer in present]

    @property
    def reaches_data(self) -> bool:
        """True when the path ends somewhere that holds data: a repository or
        model hop, or any hop reaching a `data`-kind external service (e.g. the
        database behind `@prisma/client`)."""
        return any(
            hop.layer in (Layer.REPOSITORY, Layer.MODEL)
            or any(ext.kind == "data" for ext in hop.external_services)
            for hop in self.hops
        )


def is_test_path(file_path: str) -> bool:
    path = PurePosixPath(file_path)
    if any(part.lower() in _TEST_DIR_NAMES for part in path.parts[:-1]):
        return True
    return bool(_TEST_FILE_RE.search(path.name))


def trace_from(
    start_file: str,
    adjacency: Mapping[str, Iterable[str]],
    external_by_file: Mapping[str, Sequence[ExternalRef]],
    layer_of: Mapping[str, Layer],
    handler_name: str | None = None,
    *,
    max_depth: int = MAX_DEPTH,
    max_hops: int = MAX_HOPS,
) -> Trace:
    """`adjacency` maps a file to the internal files it imports (forward
    edges), `external_by_file` a file to its resolved external packages,
    `layer_of` a file to its `classify_layer` result (a missing file is
    `other`). `handler_name` is the route's handler reference when the trace
    starts from an endpoint; an extractor placeholder such as `<inline>` is
    not a function name and yields `fn=None`."""

    def layer(path: str) -> Layer:
        return layer_of.get(path, Layer.OTHER)

    def rank(path: str) -> int:
        # `other` only ever reaches here as the start file: outermost.
        return LAYER_RANK.get(layer(path), -1)

    start_expansion = _expand(start_file, rank(start_file), adjacency, external_by_file, layer_of)
    hops: list[TraceHop] = [
        TraceHop(
            index=0,
            depth=0,
            parent_index=None,
            layer=layer(start_file),
            file_path=start_file,
            fn=_handler_fn(handler_name),
            via=(),
            deps=start_expansion.deps,
            external_services=start_expansion.externals,
        )
    ]
    children_of: dict[int, dict[str, tuple[str, ...]]] = {0: start_expansion.children}
    visited = {start_file}
    frontier = [0]
    truncated = False
    depth = 0

    while frontier:
        # child path -> (parent index, via chain); lowest-index parent wins.
        candidates: dict[str, tuple[int, tuple[str, ...]]] = {}
        for parent_index in frontier:
            for child, via in children_of[parent_index].items():
                if child not in visited and child not in candidates:
                    candidates[child] = (parent_index, via)
        if not candidates:
            break
        if depth + 1 > max_depth:
            truncated = True
            break

        ordered = sorted(candidates, key=lambda p: (rank(p), p))
        room = max_hops - len(hops)
        if len(ordered) > room:
            truncated = True
            ordered = ordered[:room]
        if not ordered:
            break

        depth += 1
        frontier = []
        for child in ordered:
            parent_index, via = candidates[child]
            expansion = _expand(child, rank(child), adjacency, external_by_file, layer_of)
            index = len(hops)
            hops.append(
                TraceHop(
                    index=index,
                    depth=depth,
                    parent_index=parent_index,
                    layer=layer(child),
                    file_path=child,
                    fn=None,
                    via=via,
                    deps=expansion.deps,
                    external_services=expansion.externals,
                )
            )
            children_of[index] = expansion.children
            visited.add(child)
            frontier.append(index)

    return Trace(hops=tuple(hops), truncated=truncated)


@dataclass(frozen=True)
class _Expansion:
    children: dict[str, tuple[str, ...]]  # eligible ranked file -> shortest via chain
    deps: tuple[str, ...]
    externals: tuple[HopExternal, ...]


def _expand(
    hop_file: str,
    hop_rank: int,
    adjacency: Mapping[str, Iterable[str]],
    external_by_file: Mapping[str, Sequence[ExternalRef]],
    layer_of: Mapping[str, Layer],
) -> _Expansion:
    """What one hop reaches: a BFS from `hop_file` that walks through `other`
    files and stops at ranked ones. BFS order means the first time a file or
    package is seen is along a shortest chain; equal-length chains are
    resolved lexicographically by visiting imports in sorted order."""
    deps = tuple(sorted({t for t in adjacency.get(hop_file, ()) if t != hop_file and not is_test_path(t)}))

    children: dict[str, tuple[str, ...]] = {}
    found: dict[str, list[tuple[tuple[str, ...], ExternalRef]]] = {}

    def collect_externals(path: str, chain: tuple[str, ...]) -> None:
        for ref in external_by_file.get(path, ()):
            found.setdefault(ref.service_id, []).append((chain, ref))

    collect_externals(hop_file, ())
    seen = {hop_file}
    queue: deque[tuple[str, tuple[str, ...]]] = deque([(hop_file, ())])
    while queue:
        current, chain = queue.popleft()
        for target in sorted(set(adjacency.get(current, ()))):
            if target in seen or is_test_path(target):
                continue
            seen.add(target)
            target_layer = layer_of.get(target, Layer.OTHER)
            if target_layer == Layer.OTHER:
                target_chain = chain + (target,)
                collect_externals(target, target_chain)
                queue.append((target, target_chain))
            elif LAYER_RANK[target_layer] >= hop_rank:
                children[target] = chain
            # else: an import that goes backwards in layer — not followed.

    externals = []
    for service_id in sorted(found):
        entries = found[service_id]
        best_chain = min((chain for chain, _ in entries), key=lambda c: (len(c), c))
        first = entries[0][1]
        externals.append(
            HopExternal(
                service_id=service_id,
                label=first.label,
                kind=first.kind,
                packages=tuple(sorted({ref.package for _, ref in entries})),
                via=best_chain,
            )
        )
    return _Expansion(children=children, deps=deps, externals=tuple(externals))


def _handler_fn(handler_name: str | None) -> str | None:
    if handler_name is None:
        return None
    name = handler_name.strip()
    if not name or name.startswith("<"):
        return None
    return name
