"""Repository settings schemas (Phase 10.11): `GET`/`PATCH
/repos/{id}/settings` and `POST /repos/{id}/purge`.

What is deliberately *not* here (decision #2): the GitHub App installation
id (Known Issue #16 — it is a credential-adjacent value stored in plaintext;
the screen only needs "is the App still installed"), an "indexed size", tracked
branches beyond the one selected branch, and a daily schedule.

`analysis_mode` is the `AnalysisMode` enum itself, so the wire format is its
lowercase value (`"on_push"`/`"manual"`) while the column stores the member
name — the same split as `AnalysisRunRead.status`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.analysis.ingestion import normalize_ignore_patterns
from app.models.repository import AnalysisMode, RepositoryAnalysisStatus
from app.schemas.analysis import AnalysisRunRead

# Live answer from GitHub (`GET /repos/{owner}/{repo}/installation`), never a
# stored flag:
# - connected: the App is installed on this repository under the installation
#   this connection uses;
# - suspended: installed, but the installation is suspended on GitHub;
# - not_installed: GitHub says the App is not installed on this repository
#   (uninstalled, or the repository was removed from its selection);
# - reinstalled: installed, but under a *different* installation than the one
#   stored at connect time — analysis would fail until the repository is
#   reconnected;
# - unknown: GitHub could not be asked (network error, App not configured).
InstallationStatus = Literal["connected", "suspended", "not_installed", "reinstalled", "unknown"]


class LastAnalysis(BaseModel):
    """The newest *completed* run (any branch) — what every analysis screen
    currently shows."""

    analysis_run_id: uuid.UUID
    branch: str
    commit_sha: str
    completed_at: datetime | None
    file_count: int


class RunInFlight(BaseModel):
    analysis_run_id: uuid.UUID
    status: str  # lowercase AnalysisRunStatus value, e.g. "parsing"
    started_at: datetime


class RepositorySettingsRead(BaseModel):
    repository_id: uuid.UUID
    owner: str
    name: str
    full_name: str
    private: bool
    default_branch: str
    # `Branch.is_selected` — the one branch analyses run on (`None` if no
    # branch has been selected yet).
    selected_branch: str | None
    installation: InstallationStatus
    analysis_status: RepositoryAnalysisStatus
    last_analysis: LastAnalysis | None
    # Every run row (any status) — what a purge would delete.
    analysis_run_count: int
    # A non-terminal run created recently enough to still be live; while set,
    # disconnect and purge answer 409. See `repo_service.IN_FLIGHT_WINDOW`.
    run_in_flight: RunInFlight | None
    analysis_mode: AnalysisMode
    ignore_patterns: list[str]
    # `ingestion.DEFAULT_IGNORE_PATTERNS` — always applied, not editable.
    default_ignore_patterns: list[str]
    max_ignore_patterns: int
    max_ignore_pattern_length: int


class RepositorySettingsUpdate(BaseModel):
    """`PATCH` body. Both fields optional; an omitted (or `null`) field is left
    unchanged. Unknown fields are a 422, so a misspelt key (`ignorePatterns`)
    can't turn into a silent no-op."""

    model_config = ConfigDict(extra="forbid")

    analysis_mode: AnalysisMode | None = None
    # Raw lines from the UI's textarea; cleaned and validated by
    # `ingestion.normalize_ignore_patterns` (<= 50 patterns x <= 200 chars
    # after blank/`#` lines are dropped). The raw caps only bound the request.
    ignore_patterns: Annotated[
        list[Annotated[str, StringConstraints(max_length=1000)]] | None, Field(max_length=500)
    ] = None

    @field_validator("ignore_patterns")
    @classmethod
    def _normalize(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return normalize_ignore_patterns(value)


class RepositorySettingsUpdateRead(BaseModel):
    """`PATCH` response: the editable settings as stored. (Not the full
    `RepositorySettingsRead` — that one asks GitHub about the installation,
    which a save should not wait on.)"""

    analysis_mode: AnalysisMode
    ignore_patterns: list[str]


class PurgeRead(BaseModel):
    """`POST /repos/{id}/purge` response (202): the fresh run queued after the
    old ones were deleted — follow it over `WS /ws/analysis-runs/{id}`."""

    status: Literal["queued"] = "queued"
    deleted_run_count: int
    analysis_run: AnalysisRunRead
