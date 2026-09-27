"""`GET /repos/{id}/impact/{analysis_run_id}` response schema (Phase 9.3).

Mirrors `services/impact_service.py::ImpactResult`/`ImpactedFile`
one-for-one, plus the target run's own `branch`/`commit_sha` and the
predecessor run's id it was diffed against — no business logic here,
`app/api/v1/impact.py` does all the shaping.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel


class ImpactedFileSchema(BaseModel):
    path: str
    why: str | None = None
    hops: int | None = None


class ImpactRead(BaseModel):
    analysis_run_id: uuid.UUID
    previous_analysis_run_id: uuid.UUID
    branch: str
    commit_sha: str
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    score: int
    direct: list[ImpactedFileSchema]
    indirect: list[ImpactedFileSchema]
    affected_apis: list[str]
    explanation: str | None = None
