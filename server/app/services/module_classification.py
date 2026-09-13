"""Pure per-module classification for Phase 6.5's Architecture screen
(`GET /repos/{id}/architecture`): groups a completed run's `File` rows into
a bounded set of legible "module" nodes and assigns each one a coarse
`ModuleKind`, per `docs/PHASE_6_PLAN.md`'s 6.5 design decisions.

Pure in the same sense as `app/analysis/`'s own modules: no DB session, no
queries — every `File`/`ApiEndpoint` row it needs is passed in already
loaded, and every output is a plain dataclass. Lives under `app/services/`
rather than `app/analysis/` because it operates on Phase 4's *persisted*
ORM rows (not Phase 3's raw ingestion facts) and only ever runs at read
time against already-stored data — it has no role in Phase 5's write-path
pipeline, unlike everything under `app/analysis/`.

Ordered classification rules (first match wins), exactly as specified:
1. Folder-keyword match, scanned over every path segment of every file in
   a group (not just the group's own truncated depth-2 key — a
   `server/app/models` directory should still read as `data` even though
   its group key folds to `server/app`).
2. Any `ApiEndpoint` rows in the group -> tentatively `service`; the one
   such group with the highest endpoint count and smallest average file
   size (a thin routing-layer signal) is promoted to `gateway`, but only
   if rule 1 didn't already assign a `gateway` group elsewhere.
3. Predominantly JS/TS/TSX, zero API endpoints, frontend-ish path
   keywords -> `frontend`. Flagged in the plan as the weakest signal —
   Phase 3 has no real frontend-framework detector, only backend-framework
   API extraction.
4. Default -> `service`.

The synthetic `external` node (rule 5 in the plan) is *not* produced here
— it's never derived from `File` rows, so `app/api/v1/architecture.py`
adds it itself from `DependencyEdge` facts once `load_dependency_edges`
returns them.
"""

from __future__ import annotations

import enum
from collections import defaultdict
from dataclasses import dataclass

from app.analysis.language_detect import Language
from app.models.api_endpoint import ApiEndpoint
from app.models.file import File

# Cap on the number of real (non-"other") module nodes returned — an MVP
# legibility bound per the plan, not a claim about the repo's true module
# count. Groups beyond this, ranked by file count, are folded into one
# "other" node per surviving kind rather than dropped or left unbounded.
MAX_MODULE_GROUPS = 24

_DATA_KEYWORDS = {"db", "database", "migrations", "models", "schema", "prisma"}
_CACHE_KEYWORDS = {"cache", "redis", "memcache"}
_GATEWAY_KEYWORDS = {"gateway", "edge", "proxy"}
_FRONTEND_KEYWORDS = {"client", "web", "frontend", "components", "pages"}
_FRONTEND_LANGUAGES = (Language.JAVASCRIPT, Language.TYPESCRIPT, Language.TSX)

# How many of a group's largest-by-loc files to surface as "key files" —
# an arbitrary, documented legibility cap, same spirit as MAX_MODULE_GROUPS.
KEY_FILES_PER_GROUP = 5


class ModuleKind(str, enum.Enum):
    FRONTEND = "frontend"
    GATEWAY = "gateway"
    SERVICE = "service"
    DATA = "data"
    CACHE = "cache"
    # Only ever assigned to the synthetic external-packages node
    # `app/api/v1/architecture.py` adds itself — `classify_modules` never
    # returns this kind for a file-derived group.
    EXTERNAL = "external"


@dataclass(frozen=True)
class ModuleGroup:
    id: str  # the depth-2 group key, or "other:<kind>" for a folded bucket
    label: str
    kind: ModuleKind
    file_count: int
    loc: int
    key_files: list[str]  # up to KEY_FILES_PER_GROUP paths, largest-by-loc first
    apis: list[str]  # "METHOD path" strings, sorted and deduped


@dataclass(frozen=True)
class ModuleClassification:
    groups: list[ModuleGroup]  # sorted by file_count desc, then id asc
    # Every input file's path -> the id of the group it actually landed in
    # (post-fold — a file whose original group got folded into "other:<kind>"
    # maps to that folded id, not its original group key). Callers building
    # edges between nodes (architecture.py) need this to map a
    # DependencyEdge's raw file paths onto the *node* ids this function
    # actually returned.
    file_group_id: dict[str, str]


def classify_modules(files: list[File], api_endpoints: list[ApiEndpoint]) -> ModuleClassification:
    if not files:
        return ModuleClassification(groups=[], file_group_id={})

    files_by_key: dict[str, list[File]] = defaultdict(list)
    for f in files:
        files_by_key[_group_key(f.module)].append(f)

    apis_by_key: dict[str, list[ApiEndpoint]] = defaultdict(list)
    for a in api_endpoints:
        apis_by_key[_group_key(a.module)].append(a)

    kind_by_key = _classify_kinds(files_by_key, apis_by_key)

    prelim_groups = {
        key: _build_group(key, key, kind_by_key[key], group_files, apis_by_key.get(key, []))
        for key, group_files in files_by_key.items()
    }

    return _fold_to_cap(prelim_groups, files_by_key)


def _group_key(module: str) -> str:
    """`File.module`/`ApiEndpoint.module` are already "containing
    directory" paths (root files get `"."`, per that field's own
    convention) — this just truncates to the first 2 path segments, per
    the plan's grouping rule."""
    segments = [s for s in module.split("/") if s not in ("", ".")]
    if not segments:
        return "."
    return "/".join(segments[:2])


def _classify_kinds(
    files_by_key: dict[str, list[File]], apis_by_key: dict[str, list[ApiEndpoint]]
) -> dict[str, ModuleKind]:
    kinds: dict[str, ModuleKind] = {}

    # Rule 1: folder-keyword match, checked over every path segment of
    # every file actually in the group — not just the group's own
    # truncated depth-2 key, which would miss a deeper real folder like
    # `server/app/models` (folds to group key `server/app`).
    for key, group_files in files_by_key.items():
        segments = {seg.lower() for f in group_files for seg in f.module.split("/") if seg not in ("", ".")}
        if segments & _DATA_KEYWORDS:
            kinds[key] = ModuleKind.DATA
        elif segments & _CACHE_KEYWORDS:
            kinds[key] = ModuleKind.CACHE
        elif segments & _GATEWAY_KEYWORDS:
            kinds[key] = ModuleKind.GATEWAY

    # Rule 2: any ApiEndpoint rows -> tentatively `service`; the single
    # busiest+thinnest such group is promoted to `gateway`, but only if
    # rule 1 didn't already claim a gateway group elsewhere.
    tentative_service = [
        key for key, group_files in files_by_key.items() if key not in kinds and apis_by_key.get(key)
    ]
    for key in tentative_service:
        kinds[key] = ModuleKind.SERVICE

    if tentative_service and ModuleKind.GATEWAY not in kinds.values():
        def _gateway_signal(key: str) -> tuple[int, float]:
            endpoint_count = len(apis_by_key[key])
            avg_file_size = sum(f.loc for f in files_by_key[key]) / len(files_by_key[key])
            return (-endpoint_count, avg_file_size)  # highest count, then smallest avg size, wins

        promoted = min(tentative_service, key=_gateway_signal)
        kinds[promoted] = ModuleKind.GATEWAY

    # Rule 3: predominantly JS/TS/TSX, zero API endpoints, frontend-ish
    # path keywords -> frontend. Weakest signal in this ordering (per the
    # plan) — Phase 3 has no real frontend-framework detector.
    for key, group_files in files_by_key.items():
        if key in kinds:
            continue
        frontend_lang_count = sum(1 for f in group_files if f.language in _FRONTEND_LANGUAGES)
        if frontend_lang_count <= len(group_files) / 2:
            continue
        segments = {seg.lower() for f in group_files for seg in f.module.split("/") if seg not in ("", ".")}
        if segments & _FRONTEND_KEYWORDS:
            kinds[key] = ModuleKind.FRONTEND

    # Rule 4: default.
    for key in files_by_key:
        kinds.setdefault(key, ModuleKind.SERVICE)

    return kinds


def _build_group(
    group_id: str, label: str, kind: ModuleKind, group_files: list[File], group_apis: list[ApiEndpoint]
) -> ModuleGroup:
    key_files = [f.path for f in sorted(group_files, key=lambda f: f.loc, reverse=True)[:KEY_FILES_PER_GROUP]]
    apis = sorted({f"{a.method} {a.path}" for a in group_apis})
    return ModuleGroup(
        id=group_id,
        label=label,
        kind=kind,
        file_count=len(group_files),
        loc=sum(f.loc for f in group_files),
        key_files=key_files,
        apis=apis,
    )


def _fold_to_cap(
    prelim_groups: dict[str, ModuleGroup], files_by_key: dict[str, list[File]]
) -> ModuleClassification:
    ordered = sorted(prelim_groups.values(), key=lambda g: (-g.file_count, g.id))
    kept = ordered[:MAX_MODULE_GROUPS]
    overflow = ordered[MAX_MODULE_GROUPS:]

    file_group_id: dict[str, str] = {}
    for g in kept:
        for f in files_by_key[g.id]:
            file_group_id[f.path] = g.id

    if not overflow:
        return ModuleClassification(groups=kept, file_group_id=file_group_id)

    # Fold every group beyond the cap into one "other" node per surviving
    # kind (a repo whose overflow spans multiple kinds gets multiple
    # "other" nodes, e.g. "other:service" and "other:data" — never a single
    # kind-less bucket, so the fold doesn't erase the one signal
    # (kind) that's still cheap to preserve).
    folded_by_kind: dict[ModuleKind, list[ModuleGroup]] = defaultdict(list)
    for g in overflow:
        folded_by_kind[g.kind].append(g)

    folded_groups: list[ModuleGroup] = []
    for kind, groups in folded_by_kind.items():
        other_id = f"other:{kind.value}"
        all_files = [f for g in groups for f in files_by_key[g.id]]
        all_apis = sorted({s for g in groups for s in g.apis})
        key_files = [f.path for f in sorted(all_files, key=lambda f: f.loc, reverse=True)[:KEY_FILES_PER_GROUP]]
        folded_groups.append(
            ModuleGroup(
                id=other_id,
                label=f"Other ({kind.value})",
                kind=kind,
                file_count=len(all_files),
                loc=sum(f.loc for f in all_files),
                key_files=key_files,
                apis=all_apis,
            )
        )
        for f in all_files:
            file_group_id[f.path] = other_id

    all_groups = sorted(kept + folded_groups, key=lambda g: (-g.file_count, g.id))
    return ModuleClassification(groups=all_groups, file_group_id=file_group_id)
