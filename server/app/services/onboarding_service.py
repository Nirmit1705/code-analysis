"""The onboarding path (Phase 10.10, decision #8): a reading order through a
repository's Architecture components, foundations first, plus a short list of
repository-wide starting points and the user's own progress.

Everything here is derived from the latest completed run's facts — the same
components the Architecture screen shows (`architecture_service`), the
file-level import graph, endpoint ownership, line counts and complexity. There
is no LLM call: the body of a step is the existing per-component insight
(`GET .../architecture/insight`), which the screen fetches when a step is
opened.

Which components become steps
    A file-derived component (external services are not) with at least one
    *source* file: a parsed language, not a test (`analysis/trace.py::is_test_path`)
    and not a lockfile/minified artifact (`analysis/debt.py::is_generated_file`).
    That drops a test-only group (HireSight's `server/__tests__`) and groups of
    JSON/assets only, which have nothing to read in this sense.

Edges between steps
    Recomputed from distinct (source file, target file) import pairs with
    neither side a test or generated file, mapped onto component ids. This is
    the Architecture screen's own edge rule minus test imports — a test importing
    a service is not the service "building on" anything.

Order (`order_path`, pure)
    Components that import each other are condensed into one group (Tarjan
    SCC). The groups are then laid out topologically — a component only after
    everything it imports — choosing among the ready ones by:
      1. connected first: a component that imports, is imported by, or serves
         endpoints comes before an isolated one (typically tooling/config such
         as `vite.config.ts`). Data/cache components count as connected even
         when isolated: a Prisma schema is imported by nothing in the graph but
         is still the foundation of the data layer;
      2. kind: data, cache, service, gateway (routes), frontend — so a
         full-stack repository reads its backend bottom-up before the UI that
         calls it;
      3. importance (more importers + endpoints first), then label.
    Inside a cycle group, members go by importance.

Cap (`cap_path`, pure)
    At most `MAX_STEPS` steps: the most important (importers + endpoints, then
    connected, then source lines), kept in path order. `total_components`
    reports how many qualified, so the screen can say so.

Progress
    `onboarding_progress` rows keyed by component id, so a tick survives
    re-analysis; a row whose component is no longer in the path is ignored,
    not deleted.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analysis.debt import is_generated_file
from app.analysis.dependencies import DependencyEdgeType
from app.analysis.trace import is_test_path
from app.models.api_endpoint import ApiEndpoint
from app.models.file import File
from app.models.onboarding_progress import OnboardingProgress
from app.models.repository import Repository
from app.models.user import User
from app.schemas.onboarding import (
    OnboardingProgressRead,
    OnboardingRead,
    OnboardingStep,
    ReadFirstFile,
    StartingPoint,
)
from app.services import architecture_service

# Legibility caps, not claims about the repository — unvalidated round numbers.
MAX_STEPS = 10
MAX_READ_FIRST = 5
MAX_SAMPLE_APIS = 6
MAX_READMES = 2
MAX_ENTRY_POINTS = 2
MAX_MOST_IMPORTED = 2
# An entry point must reach at least this many source files to be worth
# starting from (a lone script importing one helper is not an entry point).
MIN_ENTRY_REACH = 3
# A most-imported starting point needs at least this many importers.
MIN_STARTING_IMPORTERS = 2
# `File.complexity_score` is the file's *average* function complexity; below
# this it is not worth calling out as "the complex one".
MIN_NOTABLE_COMPLEXITY = 3

_KIND_RANK = {"data": 0, "cache": 1, "service": 2, "gateway": 3, "frontend": 4, "external": 5}
_FOUNDATION_KINDS = {"data", "cache"}


class NotAnalyzedError(Exception):
    """The repository has no completed analysis, so there is no path."""


class StepNotFoundError(Exception):
    """The step id is not part of the repository's current path."""


# --- pure: ordering ------------------------------------------------------------------


@dataclass(frozen=True)
class PathComponent:
    id: str
    label: str
    kind: str
    # Importers (distinct file pairs from other path components) + endpoints.
    importance: int
    # Source lines, the last tie-break for the cap.
    size: int


def order_path(components: list[PathComponent], depends_on: Mapping[str, set[str]]) -> list[list[str]]:
    """The reading order as a list of groups (a group of more than one id is a
    cycle: those components import each other). `depends_on[a]` holds the ids
    `a` imports; ids outside `components` are ignored. Deterministic."""
    by_id = {c.id: c for c in components}
    deps = {c.id: {d for d in depends_on.get(c.id, set()) if d in by_id and d != c.id} for c in components}
    connected = {c.id for c in components if deps[c.id] or c.importance > 0 or c.kind in _FOUNDATION_KINDS}
    for targets in deps.values():
        connected |= targets

    sccs = _strongly_connected(sorted(by_id), deps)
    scc_of = {member: index for index, scc in enumerate(sccs) for member in scc}
    scc_deps = [{scc_of[d] for m in scc for d in deps[m]} - {i} for i, scc in enumerate(sccs)]

    def member_key(cid: str) -> tuple:
        c = by_id[cid]
        return (-c.importance, _KIND_RANK.get(c.kind, 9), c.label, c.id)

    def group_key(index: int) -> tuple:
        members = sccs[index]
        return (
            0 if any(m in connected for m in members) else 1,
            min(_KIND_RANK.get(by_id[m].kind, 9) for m in members),
            -sum(by_id[m].importance for m in members),
            min(by_id[m].label for m in members),
            min(members),
        )

    placed: set[int] = set()
    ordered: list[list[str]] = []
    remaining = set(range(len(sccs)))
    while remaining:
        # The condensation is a DAG, so something is always ready.
        ready = [i for i in remaining if scc_deps[i] <= placed]
        chosen = min(ready, key=group_key)
        ordered.append(sorted(sccs[chosen], key=member_key))
        placed.add(chosen)
        remaining.discard(chosen)
    return ordered


def cap_path(groups: list[list[str]], components: list[PathComponent], limit: int = MAX_STEPS) -> list[list[str]]:
    """Keeps the `limit` most important components, in path order (a cycle
    group keeps only its surviving members; an emptied group disappears)."""
    flat = [cid for group in groups for cid in group]
    if len(flat) <= limit:
        return [list(g) for g in groups]
    by_id = {c.id: c for c in components}
    path_position = {cid: index for index, cid in enumerate(flat)}  # earlier = more foundational
    keep = set(
        sorted(flat, key=lambda cid: (-by_id[cid].importance, -by_id[cid].size, path_position[cid], cid))[:limit]
    )
    capped = [[cid for cid in group if cid in keep] for group in groups]
    return [g for g in capped if g]


def _strongly_connected(nodes: list[str], deps: Mapping[str, set[str]]) -> list[list[str]]:
    """Tarjan's SCC over `deps` (small graphs: at most ~24 components). Kept
    local rather than importing `analysis/graph.py`'s private file-level one,
    per this codebase's "duplicate small private helpers" convention."""
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        index_of[node] = lowlink[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(deps.get(node, ())):
            if target not in index_of:
                visit(target)
                lowlink[node] = min(lowlink[node], lowlink[target])
            elif target in on_stack:
                lowlink[node] = min(lowlink[node], index_of[target])
        if lowlink[node] == index_of[node]:
            members = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                members.append(member)
                if member == node:
                    break
            result.append(sorted(members))

    for node in nodes:
        if node not in index_of:
            visit(node)
    return result


# --- pure: file picks ----------------------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    path: str
    loc: int
    complexity: int | None
    endpoints: int
    importers: int


def pick_read_first(
    files: list[SourceFile], other_files: Iterable[SourceFile] = (), limit: int = MAX_READ_FIRST
) -> list[ReadFirstFile]:
    """Up to `limit` files to open first in one component, each with the fact
    that earned it a place: interleaves the biggest endpoint owners, the
    most-imported files and the most complex one, then fills with the largest.

    `files` are the component's source files; `other_files` are its remaining
    non-test, non-generated files (a `schema.prisma`, a README, a manifest),
    which can only fill by size — they have no import or complexity facts."""
    by_endpoints = sorted((f for f in files if f.endpoints > 0), key=lambda f: (-f.endpoints, f.path))
    by_importers = sorted((f for f in files if f.importers > 0), key=lambda f: (-f.importers, f.path))
    by_complexity = sorted(
        (f for f in files if f.complexity is not None and f.complexity >= MIN_NOTABLE_COMPLEXITY),
        key=lambda f: (-(f.complexity or 0), f.path),
    )
    by_size = sorted((f for f in [*files, *other_files] if f.loc > 0), key=lambda f: (-f.loc, f.path))

    candidates: list[ReadFirstFile] = []
    for source, reason, value in (
        (by_endpoints[:1], "endpoints", lambda f: f.endpoints),
        (by_importers[:1], "imported_by", lambda f: f.importers),
        (by_complexity[:1], "complexity", lambda f: f.complexity or 0),
        (by_endpoints[1:2], "endpoints", lambda f: f.endpoints),
        (by_importers[1:2], "imported_by", lambda f: f.importers),
        (by_size, "largest", lambda f: f.loc),
    ):
        for f in source:
            candidates.append(ReadFirstFile(path=f.path, reason=reason, count=value(f)))

    picked: list[ReadFirstFile] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.path in seen:
            continue
        seen.add(candidate.path)
        picked.append(candidate)
        if len(picked) == limit:
            break
    return picked


def pick_starting_points(
    all_paths: Iterable[str],
    source_paths: set[str],
    imports: Mapping[str, set[str]],
    importers: Mapping[str, int],
) -> list[StartingPoint]:
    """Repository-wide places to start: READMEs (shallowest first), entry
    points (source files nothing imports that reach the most source files
    through imports) and the most-imported files. `imports` is the source-only
    import graph (`a -> {b, ...}`)."""
    points: list[StartingPoint] = []
    seen: set[str] = set()

    readmes = sorted(
        (p for p in all_paths if PurePosixPath(p).name.lower().startswith("readme")),
        key=lambda p: (p.count("/"), p),
    )
    for path in readmes[:MAX_READMES]:
        points.append(StartingPoint(path=path, reason="readme"))
        seen.add(path)

    roots = [p for p in source_paths if importers.get(p, 0) == 0 and imports.get(p)]
    reach = {p: _reach(p, imports) for p in roots}
    entries = sorted((p for p in roots if reach[p] >= MIN_ENTRY_REACH), key=lambda p: (-reach[p], p))
    for path in entries[:MAX_ENTRY_POINTS]:
        points.append(StartingPoint(path=path, reason="entry_point", count=reach[path]))
        seen.add(path)

    most_imported = sorted(
        (p for p in source_paths if importers.get(p, 0) >= MIN_STARTING_IMPORTERS and p not in seen),
        key=lambda p: (-importers[p], p),
    )
    for path in most_imported[:MAX_MOST_IMPORTED]:
        points.append(StartingPoint(path=path, reason="imported_by", count=importers[path]))
    return points


def _reach(start: str, imports: Mapping[str, set[str]]) -> int:
    """Distinct files reachable from `start` through imports (excluding itself)."""
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for target in imports.get(current, ()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return len(seen) - 1


# --- DB: building the path -----------------------------------------------------------


@dataclass
class _Path:
    analysis_run_id: uuid.UUID
    steps: list[OnboardingStep]  # `done`/`completed_at` not filled yet
    starting_points: list[StartingPoint]
    total_components: int


def _is_source(f: File) -> bool:
    return f.language is not None and not is_test_path(f.path) and not is_generated_file(f.path)


def _build_path(db: Session, repository: Repository) -> _Path | None:
    built = architecture_service.build_architecture(db, repository)
    if built is None:
        return None

    group_of = built.classification.file_group_id
    source_files = [f for f in built.files if _is_source(f)]
    source_paths = {f.path for f in source_files}

    # Source-only import graph, deduped to distinct file pairs.
    imports: dict[str, set[str]] = defaultdict(set)
    for edge in built.dependency_edges:
        if edge.edge_type is not DependencyEdgeType.INTERNAL or edge.target_file is None:
            continue
        if edge.source_file in source_paths and edge.target_file in source_paths and edge.source_file != edge.target_file:
            imports[edge.source_file].add(edge.target_file)
    importer_count: Counter[str] = Counter(target for targets in imports.values() for target in targets)

    endpoint_rows = db.execute(
        select(ApiEndpoint.file_id, func.count()).where(ApiEndpoint.analysis_run_id == built.run.id).group_by(ApiEndpoint.file_id)
    ).all()
    path_of_id = {f.id: f.path for f in built.files}
    endpoints_by_path = {path_of_id[file_id]: count for file_id, count in endpoint_rows if file_id in path_of_id}

    files_by_group: dict[str, list[File]] = defaultdict(list)
    for f in source_files:
        group_id = group_of.get(f.path)
        if group_id is not None:
            files_by_group[group_id].append(f)
    # Readable non-source files (schemas, READMEs, manifests) — only ever used
    # to fill a step's "read first" list by size. Dotfiles (`.gitignore`,
    # `.dockerignore`, ...) are tool settings, not reading material.
    other_files_by_group: dict[str, list[File]] = defaultdict(list)
    for f in built.files:
        group_id = group_of.get(f.path)
        if (
            group_id is not None
            and f.path not in source_paths
            and not PurePosixPath(f.path).name.startswith(".")
            and not is_test_path(f.path)
            and not is_generated_file(f.path)
        ):
            other_files_by_group[group_id].append(f)

    nodes = {n.id: n for n in built.nodes if n.id in files_by_group}

    # Component edges: distinct source-file pairs crossing two path components.
    pair_weight: Counter[tuple[str, str]] = Counter()
    for source, targets in imports.items():
        for target in targets:
            a, b = group_of.get(source), group_of.get(target)
            if a in nodes and b in nodes and a != b:
                pair_weight[(a, b)] += 1
    depends_on: dict[str, set[str]] = defaultdict(set)
    incoming: Counter[str] = Counter()
    dependents: dict[str, set[str]] = defaultdict(set)
    for (a, b), weight in pair_weight.items():
        depends_on[a].add(b)
        incoming[b] += weight
        dependents[b].add(a)

    components = [
        PathComponent(
            id=n.id,
            label=n.label,
            kind=n.kind,
            importance=incoming[n.id] + len(n.apis),
            size=sum(f.loc for f in files_by_group[n.id]),
        )
        for n in nodes.values()
    ]
    groups = cap_path(order_path(components, depends_on), components, MAX_STEPS)
    position = {cid: index for index, cid in enumerate(cid for group in groups for cid in group)}

    steps: list[OnboardingStep] = []
    for group in groups:
        for cid in group:
            node = nodes[cid]
            group_files = [
                SourceFile(
                    path=f.path,
                    loc=f.loc,
                    complexity=f.complexity_score,
                    endpoints=endpoints_by_path.get(f.path, 0),
                    importers=importer_count.get(f.path, 0),
                )
                for f in files_by_group[cid]
            ]
            steps.append(
                OnboardingStep(
                    id=cid,
                    title=node.label,
                    kind=node.kind,
                    file_count=node.files,
                    loc=node.loc,
                    endpoint_count=len(node.apis),
                    sample_apis=node.apis[:MAX_SAMPLE_APIS],
                    depended_on_by=len(dependents[cid]),
                    builds_on=sorted(
                        (d for d in depends_on[cid] if d in position and d not in group), key=position.__getitem__
                    ),
                    in_cycle_with=[m for m in group if m != cid],
                    read_first=pick_read_first(
                        group_files,
                        [
                            SourceFile(path=f.path, loc=f.loc, complexity=None, endpoints=0, importers=0)
                            for f in other_files_by_group[cid]
                        ],
                    ),
                )
            )

    starting_points = pick_starting_points((f.path for f in built.files), source_paths, imports, importer_count)
    return _Path(
        analysis_run_id=built.run.id,
        steps=steps,
        starting_points=starting_points,
        total_components=len(components),
    )


def _progress_rows(db: Session, repository: Repository, user: User) -> dict[str, OnboardingProgress]:
    rows = db.execute(
        select(OnboardingProgress).where(
            OnboardingProgress.user_id == user.id, OnboardingProgress.repository_id == repository.id
        )
    ).scalars()
    return {row.step_id: row for row in rows}


def get_onboarding(db: Session, repository: Repository, user: User) -> OnboardingRead:
    path = _build_path(db, repository)
    if path is None:
        return OnboardingRead(analyzed=False)
    progress = _progress_rows(db, repository, user)
    for step in path.steps:
        row = progress.get(step.id)
        if row is not None:
            step.done = True
            step.completed_at = row.completed_at
    return OnboardingRead(
        analyzed=True,
        analysis_run_id=path.analysis_run_id,
        steps=path.steps,
        starting_points=path.starting_points,
        completed_count=sum(1 for s in path.steps if s.done),
        total_components=path.total_components,
    )


def set_step_done(db: Session, repository: Repository, user: User, step_id: str, done: bool) -> OnboardingProgressRead:
    """Ticks (inserts, idempotently) or unticks (deletes) one step. Raises
    `NotAnalyzedError` / `StepNotFoundError` — the only failures."""
    path = _build_path(db, repository)
    if path is None:
        raise NotAnalyzedError
    step_ids = {s.id for s in path.steps}
    if step_id not in step_ids:
        raise StepNotFoundError

    if done:
        db.execute(
            insert(OnboardingProgress)
            .values(id=uuid.uuid4(), user_id=user.id, repository_id=repository.id, step_id=step_id)
            .on_conflict_do_nothing(constraint="uq_onboarding_progress_user_repository_step")
        )
    else:
        db.execute(
            delete(OnboardingProgress).where(
                OnboardingProgress.user_id == user.id,
                OnboardingProgress.repository_id == repository.id,
                OnboardingProgress.step_id == step_id,
            )
        )
    db.commit()

    progress = _progress_rows(db, repository, user)
    row = progress.get(step_id)
    return OnboardingProgressRead(
        step_id=step_id,
        done=row is not None,
        completed_at=row.completed_at if row is not None else None,
        completed_count=sum(1 for sid in step_ids if sid in progress),
        step_count=len(step_ids),
    )
