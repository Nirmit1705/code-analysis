"""Analysis-run progress event schema (Phase 5.3).

Published over Redis pub/sub by `workers/tasks/analysis_tasks.py` as an
`AnalysisRun` moves through its status lifecycle (see
`services/progress_service.py`); consumed, eventually, by Phase 5.4's
`GET /ws/analysis-runs/{id}` WebSocket handler.

This is a deliberately minimal, provisional wire shape — Phase 5.4 may need
to extend it once the actual WebSocket contract's needs are clear (e.g.
richer per-stage detail). Not treated as a frozen contract by this phase.
"""

from datetime import datetime

from pydantic import BaseModel


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
