"""API Reference response schemas (Phase 10.1).

Only what the extractors / persisted rows actually ground: method, composed
path, framework, the declaring file, the Architecture group ("service") that
file belongs to, the handler, path parameters parsed from the path text, the
middleware named on the route call (Express only), plus — on the per-endpoint
detail — the real source lines and the route file's real structural facts.
Deliberately absent (docs/PHASE_10_PLAN.md decision #2): call counts,
request/response bodies and any auth *claim* — `ApiEndpoint.auth_required` is
a placeholder that is always `False`, and absence of a per-route middleware
means nothing (it may be applied via `app.use`), so none of it is invented.

Field names snake_case end to end, matching every other screen schema.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

FrameworkLiteral = Literal["express", "spring_boot", "fastapi", "flask"]


class ApiHandler(BaseModel):
    """The function/method handling a route.

    `resolved=True`: the extractor linked the route to a real `Symbol`, so
    `file_path`/`line_start`/`line_end` locate the handler function itself.
    `resolved=False`: only the raw reference text survived (an inline
    callback shows as `<inline>`, an imported Express handler as its
    identifier) and there is no location to claim — the three location
    fields are `None` rather than pointing at the route declaration.
    """

    name: str
    resolved: bool
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None


class ApiPathParam(BaseModel):
    """A path parameter parsed from the route's own path text. `type` is only
    set when the path syntax carries one (Flask `<int:id>`)."""

    name: str
    type: str | None = None


class ApiEndpointRead(BaseModel):
    id: uuid.UUID
    method: str
    path: str
    framework: FrameworkLiteral
    # The Architecture group label for the declaring file (e.g. "API Routes"),
    # from `classify_modules` — the same grouping that screen shows.
    service: str
    # Resource prefix derived from the path (`/api/auth`, `/health`, `/`) —
    # what the screen groups its list by.
    group: str
    module: str
    file_path: str
    # First/last line of the route declaration in `file_path`. `None` for runs
    # persisted before these were recorded.
    line: int | None = None
    line_end: int | None = None
    handler: ApiHandler | None = None
    path_params: list[ApiPathParam] = []
    # Names between the path and the handler on the route call itself, in
    # order. `None` = not recorded (non-Express framework, or an older run);
    # `[]` = recorded, none. Never an auth claim; `app.use(...)` is not included.
    middleware: list[str] | None = None
    # "new" = no endpoint with this (method, path) in the baseline run.
    # `None` both for unchanged endpoints and when there is no baseline.
    change: Literal["new"] | None = None


class ApiRemovedEndpoint(BaseModel):
    method: str
    path: str
    file_path: str


class ApiBaseline(BaseModel):
    """The earlier completed run on the same branch, at a *different commit*,
    that `change`/`removed` are measured against (a re-analysis of the same
    commit is not a change, so it is skipped)."""

    analysis_run_id: uuid.UUID
    commit_sha: str
    completed_at: datetime


class ApiReferenceRead(BaseModel):
    """`analyzed=False` mirrors every other screen endpoint's "not analyzed
    yet" convention — every other field left at its empty default."""

    analyzed: bool
    analysis_run_id: uuid.UUID | None = None
    commit_sha: str | None = None
    branch: str | None = None
    endpoints: list[ApiEndpointRead] = []
    baseline: ApiBaseline | None = None
    new_count: int = 0
    removed: list[ApiRemovedEndpoint] = []


# --- per-endpoint detail --------------------------------------------------------


class CodeSnippet(BaseModel):
    """Real source lines read from GitHub at the analyzed commit, 1-indexed
    `start_line`..`end_line`, capped (`truncated` when the span was longer)."""

    file_path: str
    start_line: int
    end_line: int
    code: str
    truncated: bool = False


class TouchedFile(BaseModel):
    file_path: str
    service: str


class TouchedService(BaseModel):
    """A recognised external service (Database / Redis / a SaaS SDK) the route
    file imports, with the real package names behind it."""

    label: str
    kind: str
    packages: list[str]


class TouchedDebt(BaseModel):
    issue_type: str
    severity: str
    description: str


class ApiTouches(BaseModel):
    """File-grain facts about the file that declares the route — shared by
    every route in that file (`routes_in_file`), not specific to one endpoint
    (dependency edges are file-grain, Known Issue #7)."""

    route_file: str
    routes_in_file: int
    loc: int
    complexity_score: int | None = None
    in_cycle: bool = False
    cycle_size: int | None = None
    imports: list[TouchedFile] = []
    external: list[TouchedService] = []
    used_by: list[TouchedFile] = []
    used_by_total: int = 0
    debt: list[TouchedDebt] = []


class ApiEndpointDetailRead(BaseModel):
    id: uuid.UUID
    # The route declaration (for an inline Express handler, the whole callback).
    declaration: CodeSnippet | None = None
    # The handler's own code, only when it resolved to a symbol *outside* the
    # declaration span (otherwise the declaration already contains it).
    handler_code: CodeSnippet | None = None
    # Why there is no code (GitHub unreachable, run predates line capture...),
    # never an error: the detail still carries `touches`.
    source_note: str | None = None
    touches: ApiTouches
