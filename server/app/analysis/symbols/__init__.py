"""Shared `Symbol` representation for Phase 3.3-3.5's per-language symbol
extractors (`js_ts.py`, `python.py`, `java.py`). Each language module
produces a `list[Symbol]` for one file; nothing in this package is
language-specific — that lives in the per-language modules, which import
from here rather than the other way around.

Pure with respect to the rest of the app: no DB, no ORM, no FastAPI, no
Celery. `Symbol.module` is this file's containing directory, POSIX-relative
to the repo root ("." for a root-level file) — the plan doesn't otherwise
define what "module" means for a per-symbol row, and a directory grouping is
the simplest interpretation that needs no extra configuration and matches
how `dependencyRisks`/coupling scores group things by folder later.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.analysis.language_detect import Language


class SymbolKind(str, enum.Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    # JSX-returning function, or a class extending React.Component /
    # React.PureComponent — see `js_ts.py` for the exact heuristic.
    COMPONENT = "component"


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: SymbolKind
    file_path: str  # POSIX-relative path, same convention as ingestion.IngestedFile.path
    line_start: int  # 1-indexed, inclusive
    line_end: int  # 1-indexed, inclusive
    module: str
    language: Language
    has_docstring: bool
