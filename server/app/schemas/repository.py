"""Repository/branch request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.repository import RepositoryAnalysisStatus


class GitHubRepoSummary(BaseModel):
    """One entry from `GET /github/repos` — live data from GitHub, not yet a
    persisted `Repository` row. Conceptually mirrors `githubRepos[]`
    (client/src/lib/repo-data.ts: name/lang/branch/private/stars/size) but
    kept as raw/typed values (an ISO timestamp, a KB integer) rather than
    pre-formatted display strings ("2 hours ago", "184 MB") — that
    formatting is a frontend concern, wired up in Phase 2.5."""

    github_repo_id: str
    owner: str
    name: str
    full_name: str
    private: bool
    language: str | None
    default_branch: str
    stargazers_count: int
    size_kb: int
    pushed_at: datetime | None


class RepoSelectRequest(BaseModel):
    github_repo_id: str
    owner: str
    name: str
    full_name: str
    default_branch: str
    # Phase 6.1: the frontend already has this from `GET /github/repos`'s
    # `GitHubRepoSummary.private` at the point it calls this endpoint — no
    # default, so a caller can't silently omit it and get a wrong `False`.
    private: bool


class RepositoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner: str
    name: str
    full_name: str
    github_repo_id: str
    default_branch: str
    private: bool
    analysis_status: RepositoryAnalysisStatus
    last_analyzed_commit_sha: str | None
    created_at: datetime


class RepositoryListItem(RepositoryRead):
    """`RepositoryRead` plus one computed field for `GET /repos`'s list view
    (Phase 6.1) — when this repository most recently *finished* an analysis
    run, distinct from `analysis_status` (which only tracks the *outcome* of
    the most recent run, not when a completed one finished). Not a stored
    column: `repo_service.get_last_completed_analysis_times` computes it via
    `MAX(completed_at)` over `AnalysisRun` rows filtered to `COMPLETED`.
    Defaults to `None` so a plain `RepositoryRead`-shaped ORM object can
    still `model_validate` against this schema before the field is filled in."""

    last_completed_analysis_at: datetime | None = None


class BranchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    latest_commit_sha: str
    is_default: bool
    is_selected: bool
