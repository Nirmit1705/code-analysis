"""Builds the API Reference screen's data from one completed run (Phase 10.1):
the endpoint list (handler, service, resource group, path parameters,
middleware, "new since" markers) and the per-endpoint detail (real source
lines + the route file's structural facts). A service rather than inline in
the router so later chunks that need "this run's endpoints with their handler
and service" (the tracer's picker, the generated API docs) reuse one loader.

Everything here is a read over already-persisted rows, plus one live GitHub
file read for the code snippets (same source the Codebase screen uses). No
LLM. File-grain facts (`build_touches`) are labelled as such by the schema.
"""

from __future__ import annotations

import base64
import re
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.binary_detect import is_binary
from app.analysis.dependencies import DependencyEdgeType
from app.analysis.graph import analyze_dependency_graph
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus
from app.models.api_endpoint import ApiEndpoint
from app.models.debt_issue import DebtIssue
from app.models.file import File
from app.models.repository import Repository
from app.models.symbol import Symbol
from app.models.user import User
from app.schemas.api_reference import (
    ApiBaseline,
    ApiEndpointDetailRead,
    ApiEndpointRead,
    ApiHandler,
    ApiPathParam,
    ApiReferenceRead,
    ApiRemovedEndpoint,
    ApiTouches,
    CodeSnippet,
    TouchedDebt,
    TouchedFile,
    TouchedService,
)
from app.services import analysis_read_service, github_service
from app.services.external_services import resolve_external_target
from app.services.github_auth_service import GitHubAuthError, get_installation_token_for_user
from app.services.module_classification import classify_modules

# A snippet longer than this is cut (and flagged) so one giant inline handler
# can't turn the detail response into a file dump.
MAX_SNIPPET_LINES = 60
MAX_USED_BY = 10
MAX_REMOVED = 50

# Duplicated from `analysis/debt.py`/`dashboard.py` per this codebase's
# "duplicate small private constants" convention. Debt severity is sorted in
# Python, never SQL (Known Issue #15: the enum column stores member *names*).
_SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}

_PATH_PARAM_PATTERNS = (
    # Flask `<int:id>` / `<id>`
    (re.compile(r"<(?:(?P<type>[A-Za-z_]+):)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)>"), True),
    # FastAPI/Spring `{id}` / `{id:regex}` / `{id:path}`
    (re.compile(r"\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}"), False),
    # Express `:id` (not preceded by a word char, so `http://` style never matches)
    (re.compile(r"(?<![A-Za-z0-9_]):(?P<name>[A-Za-z_][A-Za-z0-9_]*)"), False),
)

_VERSION_SEGMENT = re.compile(r"^v\d+$", re.IGNORECASE)


def extract_path_params(path: str) -> list[ApiPathParam]:
    """Parameters named in the path text itself, in order of appearance,
    de-duplicated by name. Pure string parsing — no claim about their types
    beyond what the syntax states (Flask converters)."""
    found: list[tuple[int, ApiPathParam]] = []
    for pattern, has_type in _PATH_PARAM_PATTERNS:
        for m in pattern.finditer(path):
            found.append((m.start(), ApiPathParam(name=m.group("name"), type=m.group("type") if has_type else None)))
    found.sort(key=lambda item: item[0])
    seen: set[str] = set()
    params: list[ApiPathParam] = []
    for _pos, param in found:
        if param.name not in seen:
            seen.add(param.name)
            params.append(param)
    return params


def resource_group(path: str) -> str:
    """The resource prefix an endpoint is listed under: skip leading `api` /
    `vN` segments (kept in the label), then the first static segment —
    `/api/auth/google/callback` -> `/api/auth`, `/health` -> `/health`,
    `/` or `/:id` -> `/`."""
    segments = [s for s in path.split("/") if s]
    prefix: list[str] = []
    for segment in segments:
        if segment.lower() == "api" or _VERSION_SEGMENT.match(segment):
            prefix.append(segment)
            continue
        if segment.startswith((":", "{", "<")):
            break
        prefix.append(segment)
        break
    return "/" + "/".join(prefix) if prefix else "/"


def load_api_endpoints(db: Session, analysis_run_id: uuid.UUID) -> list[ApiEndpointRead]:
    """Every endpoint of the run, sorted by `(path, method, id)` in Python.

    Sorting is done here, not in SQL: `framework` is stored as the enum
    member *name* (Known Issue #15) so a SQL ordering on enum columns would
    not match the values, and a stable total order is needed anyway so the
    client's list does not reshuffle between reads."""
    endpoints = db.execute(
        select(ApiEndpoint).where(ApiEndpoint.analysis_run_id == analysis_run_id)
    ).scalars().all()
    if not endpoints:
        return []

    files = db.execute(select(File).where(File.analysis_run_id == analysis_run_id)).scalars().all()
    file_by_id = {f.id: f for f in files}

    classification = classify_modules(files, endpoints)
    group_label_by_id = {g.id: g.label for g in classification.groups}

    handler_symbol_ids = [e.handler_symbol_id for e in endpoints if e.handler_symbol_id is not None]
    symbol_by_id: dict[uuid.UUID, Symbol] = {}
    if handler_symbol_ids:
        symbol_by_id = {
            s.id: s
            for s in db.execute(select(Symbol).where(Symbol.id.in_(handler_symbol_ids))).scalars().all()
        }

    result: list[ApiEndpointRead] = []
    for endpoint in endpoints:
        file_row = file_by_id.get(endpoint.file_id)
        if file_row is None:
            # Cannot happen (`file_id` is a CASCADE FK into this same run) —
            # skip rather than fabricate a path.
            continue
        group_id = classification.file_group_id.get(file_row.path)
        service = group_label_by_id.get(group_id, file_row.module) if group_id is not None else file_row.module

        result.append(
            ApiEndpointRead(
                id=endpoint.id,
                method=endpoint.method,
                path=endpoint.path,
                framework=endpoint.framework.value,
                service=service,
                group=resource_group(endpoint.path),
                module=endpoint.module,
                file_path=file_row.path,
                line=endpoint.handler_line_start,
                line_end=endpoint.handler_line_end,
                handler=_build_handler(endpoint, symbol_by_id, file_by_id),
                path_params=extract_path_params(endpoint.path),
                middleware=list(endpoint.middleware) if endpoint.middleware is not None else None,
            )
        )

    result.sort(key=lambda e: (e.path, e.method, str(e.id)))
    return result


def _build_handler(
    endpoint: ApiEndpoint, symbol_by_id: dict[uuid.UUID, Symbol], file_by_id: dict[uuid.UUID, File]
) -> ApiHandler | None:
    """Prefers the linked `Symbol` row (the real handler location) and falls
    back to the persisted raw `handler_name` (no location). `None` only when
    neither exists — an old-run row whose `Symbol` link was never set."""
    symbol = symbol_by_id.get(endpoint.handler_symbol_id) if endpoint.handler_symbol_id is not None else None
    if symbol is not None:
        symbol_file = file_by_id.get(symbol.file_id)
        return ApiHandler(
            name=symbol.name,
            resolved=True,
            file_path=symbol_file.path if symbol_file is not None else None,
            line_start=symbol.line_start,
            line_end=symbol.line_end,
        )
    if endpoint.handler_name:
        return ApiHandler(name=endpoint.handler_name, resolved=False)
    return None


# --- list + change markers -----------------------------------------------------------


def build_api_reference(db: Session, run: AnalysisRun) -> ApiReferenceRead:
    endpoints = load_api_endpoints(db, run.id)
    baseline = analysis_read_service.get_baseline_run(db, run)

    removed: list[ApiRemovedEndpoint] = []
    new_count = 0
    baseline_read: ApiBaseline | None = None
    if baseline is not None and baseline.completed_at is not None:
        baseline_read = ApiBaseline(
            analysis_run_id=baseline.id, commit_sha=baseline.commit_sha, completed_at=baseline.completed_at
        )
        previous = db.execute(
            select(ApiEndpoint.method, ApiEndpoint.path, File.path)
            .join(File, File.id == ApiEndpoint.file_id)
            .where(ApiEndpoint.analysis_run_id == baseline.id)
        ).all()
        previous_keys = {(m, p) for m, p, _f in previous}
        current_keys = {(e.method, e.path) for e in endpoints}
        for e in endpoints:
            if (e.method, e.path) not in previous_keys:
                e.change = "new"
                new_count += 1
        removed = sorted(
            (ApiRemovedEndpoint(method=m, path=p, file_path=f) for m, p, f in previous if (m, p) not in current_keys),
            key=lambda r: (r.path, r.method),
        )[:MAX_REMOVED]

    return ApiReferenceRead(
        analyzed=True,
        analysis_run_id=run.id,
        commit_sha=run.commit_sha,
        branch=run.branch,
        endpoints=endpoints,
        baseline=baseline_read,
        new_count=new_count,
        removed=removed,
    )


# --- per-endpoint detail -------------------------------------------------------------


def make_snippet(text: str, file_path: str, start: int, end: int) -> CodeSnippet | None:
    """Lines `start..end` (1-indexed, inclusive) of `text`, clamped to the
    file and capped at `MAX_SNIPPET_LINES`. `None` when the range is outside
    the file (the file changed since the analyzed commit — can't happen for a
    ref-pinned read, but never fabricate lines)."""
    lines = text.splitlines()
    if start < 1 or start > len(lines):
        return None
    end = max(start, min(end, len(lines)))
    truncated = end - start + 1 > MAX_SNIPPET_LINES
    if truncated:
        end = start + MAX_SNIPPET_LINES - 1
    return CodeSnippet(
        file_path=file_path,
        start_line=start,
        end_line=end,
        code="\n".join(lines[start - 1 : end]),
        truncated=truncated,
    )


def fetch_source_text(repository: Repository, user: User, file_row: File) -> tuple[str | None, str | None]:
    """`(text, None)` on success, `(None, reason)` otherwise — never raises;
    the detail endpoint degrades to "no code" with a note instead of a 5xx.
    Reads the exact analyzed commit from GitHub, like the Codebase screen."""
    try:
        token = get_installation_token_for_user(user)
        data = github_service.get_file_contents(
            token, repository.owner, repository.name, file_row.path, ref=file_row.last_analyzed_commit_sha
        )
    except GitHubAuthError:
        return None, "GitHub is not connected for this account, so the source can't be shown."
    except github_service.GitHubServiceError:
        return None, "The source couldn't be fetched from GitHub right now."
    except Exception:  # noqa: BLE001 — a code preview must never fail the detail
        return None, "The source couldn't be fetched from GitHub right now."
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return None, "This file is too large to preview."
    raw = base64.b64decode(data.get("content") or "")
    if is_binary(raw):
        return None, "This file isn't text."
    return raw.decode("utf-8", errors="replace"), None


def build_detail(
    db: Session, repository: Repository, user: User, endpoint: ApiEndpoint
) -> ApiEndpointDetailRead:
    run = db.get(AnalysisRun, endpoint.analysis_run_id)
    assert run is not None  # FK-guaranteed
    files = db.execute(select(File).where(File.analysis_run_id == run.id)).scalars().all()
    file_by_id = {f.id: f for f in files}
    route_file = file_by_id[endpoint.file_id]

    declaration: CodeSnippet | None = None
    handler_code: CodeSnippet | None = None
    source_note: str | None = None

    symbol = db.get(Symbol, endpoint.handler_symbol_id) if endpoint.handler_symbol_id is not None else None
    handler_file = file_by_id.get(symbol.file_id) if symbol is not None else None
    start, end = endpoint.handler_line_start, endpoint.handler_line_end

    text_by_path: dict[str, str | None] = {}

    def text_of(file_row: File) -> str | None:
        nonlocal source_note
        if file_row.path not in text_by_path:
            text, note = fetch_source_text(repository, user, file_row)
            text_by_path[file_row.path] = text
            if text is None and source_note is None:
                source_note = note
        return text_by_path[file_row.path]

    if start is not None and end is not None:
        text = text_of(route_file)
        if text is not None:
            declaration = make_snippet(text, route_file.path, start, end)

    if symbol is not None and handler_file is not None:
        inside_declaration = (
            handler_file.id == route_file.id and start is not None and end is not None
            and start <= symbol.line_start and symbol.line_end <= end
        )
        if not inside_declaration:
            text = text_of(handler_file)
            if text is not None:
                handler_code = make_snippet(text, handler_file.path, symbol.line_start, symbol.line_end)

    if declaration is None and handler_code is None and source_note is None:
        source_note = "This run predates source-line capture — re-analyze to see the code."

    return ApiEndpointDetailRead(
        id=endpoint.id,
        declaration=declaration,
        handler_code=handler_code,
        source_note=source_note,
        touches=build_touches(db, run, route_file, files),
    )


def build_touches(db: Session, run: AnalysisRun, route_file: File, files: list[File]) -> ApiTouches:
    """File-grain structural facts about the route's own file, from the same
    stored edges/issues every other screen reads (no second analysis)."""
    api_endpoints = db.execute(select(ApiEndpoint).where(ApiEndpoint.analysis_run_id == run.id)).scalars().all()
    classification = classify_modules(files, api_endpoints)
    label_by_id = {g.id: g.label for g in classification.groups}

    def service_of(path: str) -> str:
        group_id = classification.file_group_id.get(path)
        return label_by_id.get(group_id, "") if group_id is not None else ""

    edges = analysis_read_service.load_dependency_edges(db, run.id)
    graph = analyze_dependency_graph(edges)

    imports = sorted({e.target_file for e in edges if e.edge_type is DependencyEdgeType.INTERNAL
                      and e.source_file == route_file.path and e.target_file is not None})
    used_by_all = sorted({e.source_file for e in edges if e.edge_type is DependencyEdgeType.INTERNAL
                          and e.target_file == route_file.path})

    packages_by_service: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in edges:
        if e.edge_type is DependencyEdgeType.EXTERNAL and e.source_file == route_file.path and e.target_package:
            resolved = resolve_external_target(e.target_package, run.database_provider)
            if resolved is not None:
                _node_id, label, kind = resolved
                packages_by_service[(label, kind)].add(e.target_package)

    cycle = next((c for c in graph.cycles if route_file.path in c.files), None)

    issues = db.execute(
        select(DebtIssue).where(DebtIssue.analysis_run_id == run.id, DebtIssue.file_id == route_file.id)
    ).scalars().all()
    issues = sorted(issues, key=lambda i: (-_SEVERITY_RANK[i.severity.value], i.issue_type.value, i.description))

    routes_in_file = sum(1 for a in api_endpoints if a.file_id == route_file.id)

    return ApiTouches(
        route_file=route_file.path,
        routes_in_file=routes_in_file,
        loc=route_file.loc,
        complexity_score=route_file.complexity_score,
        in_cycle=cycle is not None,
        cycle_size=len(cycle.files) if cycle is not None else None,
        imports=[TouchedFile(file_path=p, service=service_of(p)) for p in imports],
        external=[
            TouchedService(label=label, kind=kind, packages=sorted(pkgs))
            for (label, kind), pkgs in sorted(packages_by_service.items())
        ],
        used_by=[TouchedFile(file_path=p, service=service_of(p)) for p in used_by_all[:MAX_USED_BY]],
        used_by_total=len(used_by_all),
        debt=[
            TouchedDebt(issue_type=i.issue_type.value, severity=i.severity.value, description=i.description)
            for i in issues
        ],
    )
