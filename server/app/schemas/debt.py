"""`GET /repos/{id}/debt` response schemas (Phase 10.2).

One source of truth: the persisted `DebtIssue` rows and `AnalysisRun.debt_score`
of the latest completed run. Nothing is invented (docs/PHASE_10_PLAN.md
decisions #2/#4): no 0-100 category scores, no CVE counts, no code-duplication
category, no "resolved in 30 days" figure. A category's figure is its issue
`count`, its worst severity and its summed severity weight (`debt.py`'s
1/2/3/5, the same weights the aggregate score uses), so the category weights
add up to `debt_score` exactly.

`why` is fixed, per-issue-type copy about what that *class* of debt costs; it
is deliberately not an AI claim about the specific finding (that is 10.4).

Field names snake_case end to end, matching every other screen schema.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SeverityLiteral = Literal["low", "medium", "high", "critical"]
IssueTypeLiteral = Literal[
    "high_complexity",
    "large_file",
    "large_module",
    "excessive_coupling",
    "circular_dependency",
    "missing_documentation",
    "outdated_dependency",
]


class SeverityCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class DebtCategory(BaseModel):
    """One of the seven real `DebtIssueType`s. All seven are always returned
    (a type with no findings has `count=0`, `worst_severity=None`,
    `weighted_score=0`), sorted by `weighted_score` desc then type."""

    issue_type: IssueTypeLiteral
    label: str
    count: int
    worst_severity: SeverityLiteral | None = None
    # Sum of the severity weights (low 1, medium 2, high 3, critical 5) of this
    # category's issues — the category's share of `debt_score`.
    weighted_score: float


class DebtIssueRead(BaseModel):
    id: uuid.UUID
    # Type prefix + first 6 hex chars of the row id (`CX-3fa9c1`). Stable for a
    # row, NOT stable across analysis runs (issues have no cross-run identity).
    display_id: str
    severity: SeverityLiteral
    issue_type: IssueTypeLiteral
    # The category's display label ("High complexity").
    category: str
    # Category label + the file's basename (or the module); never parsed out of
    # `description`.
    title: str
    file_path: str | None = None
    module: str | None = None
    metric_value: float
    # The rule's own sentence, shown as-is.
    description: str
    why: str


class DebtTrendPoint(BaseModel):
    analysis_run_id: uuid.UUID
    commit_sha: str
    completed_at: datetime
    debt_score: float


class DebtPrevious(BaseModel):
    """The run `score_delta` / `new_since_previous` / `resolved_since_previous`
    are measured against: the newest completed run on the same branch at a
    *different commit* (a manual re-analysis of the same commit is skipped, or
    every comparison would read as "no change")."""

    analysis_run_id: uuid.UUID
    commit_sha: str
    completed_at: datetime


class DebtRead(BaseModel):
    """`analyzed=False` mirrors every other screen endpoint's "not analyzed
    yet" convention — every other field left at its empty default."""

    analyzed: bool
    analysis_run_id: uuid.UUID | None = None
    branch: str | None = None
    commit_sha: str | None = None
    debt_score: float = 0.0
    previous: DebtPrevious | None = None
    previous_debt_score: float | None = None
    score_delta: float | None = None
    total_issues: int = 0
    severity_counts: SeverityCounts = SeverityCounts()
    categories: list[DebtCategory] = []
    # Sorted severity-desc (then type, file, description), capped at
    # `MAX_ISSUES`; counts/categories above are always over *all* issues.
    issues: list[DebtIssueRead] = []
    truncated: bool = False
    # Distinct issue keys (type + file + module, plus the description for
    # circular-dependency issues) present now but not in `previous` / present in
    # `previous` but not now. `None` when there is no `previous`. Approximate by
    # design: two findings sharing a key (two complex functions in one file)
    # count once.
    new_since_previous: int | None = None
    resolved_since_previous: int | None = None
    # The branch the trend line follows, and its completed runs oldest -> newest
    # (last `MAX_TREND_POINTS`). Same branch rule as the History screen.
    trend_branch: str | None = None
    trend: list[DebtTrendPoint] = []


class DebtRecommendationRead(BaseModel):
    """`GET /repos/{id}/debt/issues/{issue_id}/recommendation` (Phase 10.4).
    `recommendation` is `None` when the model call failed or returned nothing
    usable — never an error, so the screen degrades to a calm "couldn't
    generate" line with a Retry. Generated lazily on first view and then cached
    on the finding; a failure is never cached."""

    issue_id: uuid.UUID
    recommendation: str | None = None
