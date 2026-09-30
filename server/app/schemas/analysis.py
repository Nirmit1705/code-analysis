"""Analysis-run progress event + REST response schemas.

`AnalysisProgressEvent` (Phase 5.3) is published over Redis pub/sub by
`workers/tasks/analysis_tasks.py` as an `AnalysisRun` moves through its
status lifecycle (see `services/progress_service.py`); consumed by Phase
5.4's `WS /ws/analysis-runs/{id}` handler, which also builds its initial/
terminal snapshot messages as instances of this same model — there is no
separate WS-specific wire format. Confirmed as this phase's design: Phase
5.3's event contract is reused exactly, not extended or duplicated.

`AnalysisRunRead` (Phase 5.4) is the REST response shape for
`POST /repos/{id}/analysis-runs`, `GET /analysis-runs/{id}`, and
`GET /repos/{id}/analysis-runs` — a full row snapshot, unlike the
progress event's minimal `{id, status, timestamp, error_message}`.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.analysis_run import AnalysisRunStatus, AnalysisRunTriggerSource


class AnalysisProgressEvent(BaseModel):
    """One progress update for a single `AnalysisRun`.

    `status` is the lowercase `AnalysisRunStatus` value (e.g. `"cloning"`,
    `"completed"`, `"failed"`) — a plain `str` here, not the enum itself, so
    this schema has no import-time dependency on `app.models.analysis_run`
    (schemas stay decoupled from ORM models elsewhere in this codebase too).
    `error_message` is only ever set on the terminal failure event.
    """

    analysis_run_id: str
    status: str
    timestamp: datetime
    error_message: str | None = None


class AnalysisRunRead(BaseModel):
    """Full `AnalysisRun` row snapshot, returned by every Phase 5.4 REST
    endpoint (including the `POST` that creates one — same precedent as
    `RepositoryRead` being reused for both `GET`/`POST /repos`). Imports
    `AnalysisRunStatus` directly rather than keeping `status` a plain `str`
    — matches `RepositoryRead`'s own precedent of importing
    `RepositoryAnalysisStatus` for schemas that map 1:1 onto a real ORM
    model, as opposed to `AnalysisProgressEvent` above, which deliberately
    avoids the import since it's a wire event, not a row mirror."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    branch: str
    commit_sha: str
    status: AnalysisRunStatus
    trigger_source: AnalysisRunTriggerSource
    started_at: datetime
    completed_at: datetime | None
    file_count: int
    module_count: int
    api_count: int
    dependency_count: int
    debt_score: float
    error_message: str | None
    created_at: datetime
