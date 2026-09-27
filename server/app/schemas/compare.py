"""`GET /repos/{id}/compare` request/response schemas (Phase 9.3).

Field names snake_case throughout, matching every other schema in this
codebase. Mirrors `analysis/change.py::ChangeSet` plus the head/base
`AnalysisRun` rows themselves — no business logic here, `app/api/v1/
compare.py` does all the shaping.

Deliberately has no `modified_files` field: the redesigned Compare screen
(`docs/PHASE_9_PLAN.md` design decisions #2-#4) only ever renders
added/removed files and changed dependencies — `ChangeSet.modified_files`
exists for Impact's blast-radius computation (9.2), not for display here.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel


class FileChangeSchema(BaseModel):
    path: str
    note: str


class DependencyChangeSchema(BaseModel):
    edge: str
    change: str
    risk: Literal["low", "medium", "high"]


class CompareRead(BaseModel):
    base_run_id: uuid.UUID
    head_run_id: uuid.UUID
    base_branch: str
    base_commit_sha: str
    head_branch: str
    head_commit_sha: str
    added_files: list[FileChangeSchema]
    removed_files: list[FileChangeSchema]
    changed_dependencies: list[DependencyChangeSchema]
    added_apis: list[str]
    removed_apis: list[str]
    file_count_delta: int
    module_count_delta: int
    api_count_delta: int
    dependency_count_delta: int
    debt_score_delta: float
    explanation: str | None = None
