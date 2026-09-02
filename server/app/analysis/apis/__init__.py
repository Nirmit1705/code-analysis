"""Shared `ApiRoute` representation for Phase 3.6's framework-aware API
extractors (`express.py`, `spring_boot.py`, `fastapi_flask.py`). Mirrors
`app.analysis.symbols`'s pattern (Phase 3.3-3.5): one small shared
dataclass here, all framework-specific logic in the per-framework modules,
which import from here rather than the other way around.

Pure with respect to the rest of the app: no DB, no ORM, no FastAPI route
handling of its own, no Celery.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.analysis.symbols import Symbol


class Framework(str, enum.Enum):
    EXPRESS = "express"
    SPRING_BOOT = "spring_boot"
    FASTAPI = "fastapi"
    FLASK = "flask"


@dataclass(frozen=True)
class ApiRoute:
    """One extracted API endpoint.

    `handler_symbol` is the linked `Symbol` (from the Phase 3.3-3.5
    per-language extractors) for the function/method that handles this
    route, when it resolves to one. It is `None` when this phase can't
    resolve the handler to an already-extracted symbol — e.g. an inline
    anonymous Express callback, or an Express handler imported from a file
    outside the current extraction pass (see `express.py`'s module
    docstring for the exact boundary). `handler_name` always carries the
    raw handler reference text (an identifier, a qualified name like
    `auth.router`, or a placeholder like `"<inline>"`) even when
    `handler_symbol` is `None`, so a route is never silently unattributed.
    """

    method: str  # upper-case HTTP method: "GET", "POST", "PUT", "DELETE", "PATCH", ...
    path: str  # full composed route path, e.g. "/api/v1/auth/register"
    framework: Framework
    file_path: str  # POSIX-relative path of the file the route is declared in
    module: str  # same "containing directory" convention as Symbol.module
    handler_symbol: Symbol | None
    handler_name: str
    line_start: int  # 1-indexed span of the route-defining construct (call/annotation+method)
    line_end: int
