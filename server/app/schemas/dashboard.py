"""`GET /repos/{id}/dashboard` response schemas (Phase 6.3).

Field names/shapes are written to match `client/src/lib/repo-data.ts`'s
mock `repo.stats`/`repo.languages`/`healthMetrics`/`recentCommits`/
`aiInsights` exports as closely as sensible (per `docs/PHASE_6_PLAN.md`'s
"critical files" note), snake_cased to match this codebase's existing
schema convention (`AnalysisRunRead`, `RepositoryRead`, ...) rather than
the frontend's camelCase — Phase 6.4's query module does that translation,
same as `queries/analysis.ts`/`queries/repos.ts` already do for their own
backend schemas.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

HealthMetricKey = Literal["architecture", "quality", "dependency", "docs", "debt"]
HealthTone = Literal["success", "warning", "danger"]
CommitImpact = Literal["high", "medium", "low"]


class RepoStats(BaseModel):
    files: int
    # Total LOC across *every* ingested file, not just the ones in
    # `languages` below (which only covers Phase 3's detected-language
    # set) — see `languages`' own docstring for why the two totals differ.
    loc: int
    modules: int
    apis: int
    dependencies: int
    contributors: int


class LanguageBreakdown(BaseModel):
    """One `File.language` group's share of the repository, by LOC. Only
    covers files Phase 3 could classify (`File.language IS NOT NULL`) — a
    file outside that set (e.g. a lockfile, markdown, YAML) still counts
    toward `RepoStats.loc`'s repo-wide total, just never appears here; a
    real, sometimes large, gap between the two totals (confirmed against
    `Nirmit1705/HireSight`'s own real data during this phase's
    verification — a single large `package-lock.json`-style file can
    outweigh every detected-language file combined), not a bug. No `color`
    field — matches Architecture's own "backend doesn't assign display
    colors" stance (`docs/PHASE_6_PLAN.md` 6.5); that's a Phase 6.4
    frontend concern."""

    name: str
    loc: int
    pct: float


class HealthMetric(BaseModel):
    key: HealthMetricKey
    label: str
    value: int
    delta: int
    tone: HealthTone
    note: str


class RecentCommit(BaseModel):
    sha: str
    message: str
    author: str
    components: list[str]
    impact: CommitImpact
    date: datetime


AiInsightTone = Literal["danger", "warning", "info"]


class AiInsightSchema(BaseModel):
    """One entry of `AnalysisRun.ai_insights` (Phase 8.4) — mirrors
    `insight_service.generate_insights`'s own validated `{tone, title,
    body}` dict shape exactly."""

    tone: AiInsightTone
    title: str
    body: str


class DashboardRead(BaseModel):
    """`analyzed=False` is the full "not analyzed yet" response — every
    other field left at its empty/`None` default rather than a broken
    empty aggregate (per `docs/PHASE_6_PLAN.md`'s 6.3 design decision)."""

    analyzed: bool
    analysis_run_id: uuid.UUID | None = None
    branch: str | None = None
    commit_sha: str | None = None
    completed_at: datetime | None = None
    stats: RepoStats | None = None
    languages: list[LanguageBreakdown] = []
    health_metrics: list[HealthMetric] = []
    recent_commits: list[RecentCommit] = []
    # Phase 8.4: real, LLM-generated insights, cached on `AnalysisRun
    # .ai_insights` and read back verbatim — `[]` is still a real, honest
    # outcome (insight generation failed non-fatally, or the LLM genuinely
    # found nothing worth surfacing), not a placeholder rule anymore.
    ai_insights: list[AiInsightSchema] = []
