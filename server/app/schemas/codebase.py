"""`GET /repos/{id}/files`, `GET /repos/{id}/files/{file_id}`, and
`GET /repos/{id}/files/{file_id}/insight` response schemas (Phase 6.7).

`FileInsightRead.purpose`/`.explanation` were Phase-4-data-only placeholders
(always `None`) through Phase 6/7 — real as of Phase 8.7's
`file_insight_service.py`, still `None` only when a file has nothing to
ground on or generation fails, never a fabricated guess. Field names are
snake_case throughout, matching this codebase's actual established
convention (confirmed in 6.6's own notes: `dashboard.py`'s docstring claims a
camelCase frontend translation happens, but every real query module keeps
snake_case as-is) rather than the plan doc's illustrative `usedBy` spelling.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class FileTreeEntry(BaseModel):
    """One row per `File` in the latest completed run. A **flat** list, not
    pre-nested — the frontend builds the tree client-side (`buildFileTree`,
    Phase 6.8), per the plan."""

    id: uuid.UUID
    path: str
    language: str | None
    size_bytes: int
    loc: int


class FileTreeRead(BaseModel):
    """`analyzed=False` mirrors `DashboardRead`/`ArchitectureRead`'s own
    "not analyzed yet" convention (Phases 6.3/6.5) — not spelled out
    verbatim by the plan's terse `FileTreeEntry`-only schema list, but kept
    consistent with every other screen endpoint this phase built on."""

    analyzed: bool
    analysis_run_id: uuid.UUID | None = None
    files: list[FileTreeEntry] = []


class FileContentRead(BaseModel):
    """`content` is `None` whenever `truncated` or `binary` is `True` — a
    file over GitHub's ~1MB inline-content cap (`truncated`) or whose
    decoded bytes aren't valid UTF-8 (`binary`), per the plan's explicit
    "binary/truncated fallback, not a silent failure or garbage" rule."""

    path: str
    content: str | None = None
    truncated: bool
    binary: bool


class ComplexitySummary(BaseModel):
    """`score`/`loc` are real, persisted `File` columns. `functions` is a
    real count of this file's function-like `Symbol` rows (FUNCTION/METHOD/
    COMPONENT — not CLASS, which isn't itself a function). There is no
    per-file "branches" fact anywhere in Phase 3/4's persisted data (only
    each function's already-aggregated cyclomatic complexity is kept, not a
    separate decision-node count) — deliberately omitted rather than
    fabricated, unlike `repo-data.ts`'s mock `fileInsight.complexity.branches`."""

    score: int | None
    label: str | None
    loc: int
    functions: int


class FileInsightRead(BaseModel):
    path: str
    purpose: str | None = None
    complexity: ComplexitySummary
    dependencies: list[str] = []
    used_by: list[str] = []
    apis: list[str] = []
    explanation: str | None = None
