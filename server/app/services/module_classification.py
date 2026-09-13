"""Semantic component classification for the Architecture screen
(`GET /repos/{id}/architecture`): groups a completed run's `File` rows
into a bounded set of *architectural* nodes — real components like "API
Routes," "Controllers," "Services" — not a folder/directory dependency
graph, even a finely-split one.

**Design principle (revised after real-repo review, see
`server/CLAUDE.md`'s Phase 6.5/6.6 follow-up)**: prioritize recognizing
real, evidenced architectural roles over path structure. Path-based
grouping (the original Phase 6.5 depth-2 truncation) is now the *fallback*
for files that don't match any confident semantic signal, not the primary
organizing principle — a group whose label is a raw path is an explicit
admission "we don't have a confident semantic read on this," not a
disguised default.

Pure in the same sense as `app/analysis/`'s own modules: no DB session, no
queries — every `File`/`ApiEndpoint` row it needs is passed in already
loaded, and every output is a plain dataclass. Lives under `app/services/`
rather than `app/analysis/` because it operates on Phase 4's *persisted*
ORM rows and only ever runs at read time — it has no role in Phase 5's
write-path pipeline.

Grouping + classification, in order:
1. **Routes are recognized directly, not guessed**: any file that owns
   1+ `ApiEndpoint` rows belongs to a `"<top-level>/routes"` group —
   label `"API Routes"`, kind `gateway`. 100%-grounded (a real,
   already-extracted fact), and replaces the old "busiest+thinnest group
   promoted to gateway" heuristic entirely — that was a workaround for
   coarse groups; with routes properly separated, it's unnecessary.
2. **A small, curated role-keyword vocabulary**, scanned over every
   file's *full* module path (same mechanism the data/cache/gateway
   keyword rule already used, just with more entries): `controllers`/
   `handlers` -> `"Controllers"`, `services` -> `"Services"`,
   `middleware` -> `"Middleware"`. Kind for these follows the same
   frontend-language-majority signal already used for the frontend rule,
   so e.g. a frontend-side `services/` folder doesn't get miscategorized
   as backend business logic.
3. The pre-existing data/cache/gateway keyword rule and frontend rule
   (unchanged) apply to whatever's left.
4. **Anything matching none of the above** falls back to the original
   depth-2 path truncation, labeled with its real raw path — the honest
   "insufficient evidence for a semantic role" outcome, not hidden.

Named external services (Prisma/database, Redis, Deepgram, ...) are
*not* produced here — they're never derived from `File` rows, so
`app/api/v1/architecture.py` adds them from `DependencyEdge` facts (see
that module for the curated package-recognition table).
"""

from __future__ import annotations

import enum
from collections import defaultdict
from dataclasses import dataclass

from app.analysis.language_detect import Language
from app.models.api_endpoint import ApiEndpoint
from app.models.file import File

# Cap on the number of real (non-"other") module nodes returned — an MVP
# legibility bound, not a claim about the repo's true component count.
# Groups beyond this, ranked by file count, are folded into one "other"
# node per surviving kind rather than dropped or left unbounded.
MAX_MODULE_GROUPS = 24

_DATA_KEYWORDS = {"db", "database", "migrations", "models", "schema", "prisma"}
_CACHE_KEYWORDS = {"cache", "redis", "memcache"}
_GATEWAY_KEYWORDS = {"gateway", "edge", "proxy"}
_FRONTEND_KEYWORDS = {"client", "web", "frontend", "components", "pages"}
_FRONTEND_LANGUAGES = (Language.JAVASCRIPT, Language.TYPESCRIPT, Language.TSX)

# Role-keyword vocabulary (step 2 above) — a curated, documented MVP
# heuristic, same spirit as the data/cache/gateway keywords it sits
# alongside. A codebase using different naming conventions (`handlers`
# not in this set, `resolvers/`, `usecases/`, ...) simply won't be
# recognized here and falls through to the honest path-based fallback —
# not a bug, a real limitation of a curated vocabulary.
_CONTROLLER_KEYWORDS = {"controllers", "controller", "handlers", "handler"}
_SERVICES_KEYWORDS = {"services", "service"}
_MIDDLEWARE_KEYWORDS = {"middleware", "middlewares"}
_ROLE_LABELS = {"routes": "API Routes", "controllers": "Controllers", "services": "Services", "middleware": "Middleware"}

# How many of a group's largest-by-loc files to surface as "key files" —
# an arbitrary, documented legibility cap, same spirit as MAX_MODULE_GROUPS.
KEY_FILES_PER_GROUP = 5


class ModuleKind(str, enum.Enum):
    FRONTEND = "frontend"
    GATEWAY = "gateway"
    SERVICE = "service"
    DATA = "data"
    CACHE = "cache"
    # Assigned to file-derived groups only via the data/cache keyword
    # rules above; every *synthetic* external-service node (Database,
    # Redis, Deepgram, generic "External Dependencies", ...) is added by
    # `app/api/v1/architecture.py`, not here.
    EXTERNAL = "external"


@dataclass(frozen=True)
class ModuleGroup:
    id: str  # a role-based key ("server/routes"), a path fallback, or "other:<kind>" for a folded bucket
    label: str  # a semantic role name ("API Routes"), or the raw path for a fallback/folded group
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

    api_endpoint_modules = {a.module for a in api_endpoints}

    files_by_key: dict[str, list[File]] = defaultdict(list)
    role_by_key: dict[str, str | None] = {}
    for f in files:
        key, role = _compute_group_key(f.module, has_api_endpoint=f.module in api_endpoint_modules)
        files_by_key[key].append(f)
        role_by_key[key] = role

    apis_by_key: dict[str, list[ApiEndpoint]] = defaultdict(list)
    for a in api_endpoints:
        key, _role = _compute_group_key(a.module, has_api_endpoint=True)
        apis_by_key[key].append(a)

    kind_by_key, label_by_key = _classify_kinds_and_labels(files_by_key, role_by_key)

    prelim_groups = {
        key: _build_group(key, label_by_key[key], kind_by_key[key], group_files, apis_by_key.get(key, []))
        for key, group_files in files_by_key.items()
    }

    return _fold_to_cap(prelim_groups, files_by_key)


def _compute_group_key(module: str, *, has_api_endpoint: bool) -> tuple[str, str | None]:
    """Returns `(group_key, role)` — `role` is one of `"routes"`,
    `"controllers"`, `"services"`, `"middleware"`, or `None` (the file
    matched no recognized role and falls back to the original depth-2
    path-truncation key). Grounded, high-confidence signal (owns a real
    `ApiEndpoint`) is checked before the curated keyword vocabulary, which
    is checked before the honest structural fallback."""
    segments = [s for s in module.split("/") if s not in ("", ".")]
    if not segments:
        return ".", None
    top = segments[0]

    if has_api_endpoint:
        return f"{top}/routes", "routes"

    role = _match_role_keyword(segments)
    if role is not None:
        return f"{top}/{role}", role

    return "/".join(segments[:2]), None


def _match_role_keyword(segments: list[str]) -> str | None:
    lowered = {s.lower() for s in segments}
    if lowered & _CONTROLLER_KEYWORDS:
        return "controllers"
    if lowered & _SERVICES_KEYWORDS:
        return "services"
    if lowered & _MIDDLEWARE_KEYWORDS:
        return "middleware"
    return None


def _classify_kinds_and_labels(
    files_by_key: dict[str, list[File]], role_by_key: dict[str, str | None]
) -> tuple[dict[str, ModuleKind], dict[str, str]]:
    kinds: dict[str, ModuleKind] = {}
    labels: dict[str, str] = {}

    if "." in files_by_key:
        labels["."] = "(root)"

    # Labels are set unconditionally from the group's role (independent of
    # kind priority below) — a group's *identity* is "this is the routes
    # layer" regardless of which kind rule ends up coloring it, so label
    # assignment doesn't need to race kind assignment for priority.
    for key, role in role_by_key.items():
        if role is not None:
            labels[key] = _ROLE_LABELS[role]

    # Routes: always gateway, a direct consequence of grouping (step 1) —
    # no cross-group comparison needed, unlike the old promotion heuristic.
    for key, role in role_by_key.items():
        if role == "routes":
            kinds[key] = ModuleKind.GATEWAY

    # Existing data/cache/gateway folder-keyword rule, unchanged: scanned
    # over every file's full module path. Takes priority over the
    # controllers/services/middleware role *kind* below — a folder that's
    # arguably both (e.g. a "services/cache" subfolder) reads more usefully
    # as the more specific "cache" signal for its *kind* (color), even
    # though its *label* still says "Services" per the role-based rule
    # above (the two are independent).
    for key, group_files in files_by_key.items():
        if key in kinds:
            continue
        segments = {seg.lower() for f in group_files for seg in f.module.split("/") if seg not in ("", ".")}
        if segments & _DATA_KEYWORDS:
            kinds[key] = ModuleKind.DATA
        elif segments & _CACHE_KEYWORDS:
            kinds[key] = ModuleKind.CACHE
        elif segments & _GATEWAY_KEYWORDS:
            kinds[key] = ModuleKind.GATEWAY

    # Controllers/services/middleware: language-aware kind (a same-named
    # folder on the frontend side, e.g. a React app's `services/` of
    # API-calling hooks, shouldn't read as backend logic). Label already
    # set above.
    #
    # Language majority *alone* is not a reliable frontend signal here —
    # a Node/Express backend is TypeScript too, so `_FRONTEND_LANGUAGES`
    # matches both sides equally. Requires the same second signal the
    # frontend rule below already needs (a frontend-ish path keyword),
    # not just the language check on its own — confirmed necessary
    # against a real TypeScript backend (HireSight) during this rule's
    # own testing, where language-only classified `server/services` as
    # frontend.
    for key, role in role_by_key.items():
        if key in kinds or role not in ("controllers", "services", "middleware"):
            continue
        group_files = files_by_key[key]
        frontend_lang_count = sum(1 for f in group_files if f.language in _FRONTEND_LANGUAGES)
        segments = {seg.lower() for f in group_files for seg in f.module.split("/") if seg not in ("", ".")}
        is_frontend_side = frontend_lang_count > len(group_files) / 2 and bool(segments & _FRONTEND_KEYWORDS)
        kinds[key] = ModuleKind.FRONTEND if is_frontend_side else ModuleKind.SERVICE

    # Frontend rule (only reaches groups not already kinded — i.e. today's
    # plain path-fallback groups): predominantly JS/TS/TSX, zero API
    # endpoints (guaranteed here — an endpoint-owning group was already
    # routed to "routes" above), frontend-ish path keywords. Flagged as
    # the weakest signal — Phase 3 has no real frontend-framework detector.
    for key, group_files in files_by_key.items():
        if key in kinds:
            continue
        frontend_lang_count = sum(1 for f in group_files if f.language in _FRONTEND_LANGUAGES)
        if frontend_lang_count <= len(group_files) / 2:
            continue
        segments = {seg.lower() for f in group_files for seg in f.module.split("/") if seg not in ("", ".")}
        if segments & _FRONTEND_KEYWORDS:
            kinds[key] = ModuleKind.FRONTEND

    # Default kind, and the fallback label: the group's own raw path —
    # honest about having no confident semantic read, not a disguised
    # default. Only ever reaches groups with no recognized role at all
    # (role-based groups already got their label set above).
    for key in files_by_key:
        kinds.setdefault(key, ModuleKind.SERVICE)
        labels.setdefault(key, key)

    return kinds, labels


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
