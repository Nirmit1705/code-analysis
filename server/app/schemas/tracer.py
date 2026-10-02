"""Flow Tracer response schemas (Phase 10.6): `GET /repos/{id}/tracer` (the
deterministic structure) and `GET /repos/{id}/tracer/notes` (the optional AI
notes, a separate call so the structure renders without waiting for a model).

Everything here is a file-grain fact from the latest completed run's import
graph (Known Issues #7/#42): a hop is a file a request's path is expected to
pass through, never a function call. The only function name is the route's
handler on the start hop (`fn`); no other hop carries one. Names such as
`layer` come from folder/filename conventions (`analysis/layers.py`) and are
labels, not verified roles.

Field names snake_case end to end, matching every other screen schema.
"""

import uuid
from typing import Literal

from pydantic import BaseModel

LayerLiteral = Literal["route", "controller", "service", "repository", "model", "other"]


class TraceStart(BaseModel):
    kind: Literal["endpoint", "file"]
    # "GET /api/auth/google" for an endpoint start, the file path for a file start.
    label: str
    file_path: str
    endpoint_id: uuid.UUID | None = None
    method: str | None = None
    path: str | None = None
    # How many endpoints the start file declares. The trace is per *file*, so
    # every one of them gets this same trace; the screen says so when > 1.
    endpoints_in_file: int = 0


class TraceExternalRead(BaseModel):
    """A recognised external service (database, cache, SaaS SDK) a hop reaches.
    Only services `services/external_services.py` names are listed; a package
    it does not recognise is simply not shown."""

    service_id: str
    label: str
    kind: str
    packages: list[str]
    # Pass-through files between the hop and the file that imports the package
    # (last entry = the importer); empty when the hop imports it directly.
    via: list[str] = []


class TraceHopRead(BaseModel):
    # Breadth-first order by (depth, layer, path) — nest by `parent_index`, not
    # by list position.
    index: int
    depth: int
    parent_index: int | None = None
    layer: LayerLiteral
    file_path: str
    # The route's handler name; set only on the start hop of an endpoint trace,
    # and only when the extractor named one (never for `<inline>`).
    fn: str | None = None
    # Pass-through (`other`) files between the parent hop and this one.
    via: list[str] = []
    # This file's own direct internal imports (tests excluded), including ones
    # the trace did not follow.
    deps: list[str] = []
    external_services: list[TraceExternalRead] = []
    loc: int = 0
    # Function/class/method/component names declared in the file, in source
    # order, capped; `symbol_count` is the real total.
    symbols: list[str] = []
    symbol_count: int = 0


class TracerRead(BaseModel):
    """`analyzed=False` (nothing else populated) when the repository has never
    completed an analysis."""

    analyzed: bool
    analysis_run_id: uuid.UUID | None = None
    commit_sha: str | None = None
    branch: str | None = None
    start: TraceStart | None = None
    hops: list[TraceHopRead] = []
    # Distinct hop layers, outermost first.
    layers_reached: list[LayerLiteral] = []
    # True when the path ends somewhere that holds data: a repository/model hop
    # or a database-kind external service.
    reaches_data: bool = False
    # A depth or hop cap cut the trace off (so there may be more downstream).
    truncated: bool = False
    max_depth: int = 0
    max_hops: int = 0


class TraceNote(BaseModel):
    index: int
    note: str


class TraceNotesRead(BaseModel):
    """`notes` is `None` when generation failed or produced nothing usable —
    never a 5xx. Otherwise it holds a note for each hop the model answered
    for; a hop without one simply has no entry."""

    analyzed: bool
    analysis_run_id: uuid.UUID | None = None
    notes: list[TraceNote] | None = None
