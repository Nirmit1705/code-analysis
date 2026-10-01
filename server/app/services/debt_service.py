"""Builds the Debt screen's data from one completed run's persisted
`DebtIssue` rows and `AnalysisRun.debt_score` (Phase 10.2). Deterministic only:
no LLM (the per-issue AI recommendation is 10.4), no new analysis.

The pure helpers (`issue_key`, `diff_issue_keys`, `pick_trend_branch`,
`count_severities`, `build_categories`, `build_title`, `make_display_id`) take
plain values so they are testable without a database; `build_debt_read` is the
one DB-touching entry point.

Severity and type are always ordered in Python, never SQL: both enum columns
store the member *name* (Known Issue #15), so a SQL `ORDER BY` would not match
the values' meaning.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.debt import DebtIssueType
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus
from app.models.branch import Branch
from app.models.debt_issue import DebtIssue
from app.models.file import File
from app.models.repository import Repository
from app.schemas.debt import (
    DebtCategory,
    DebtIssueRead,
    DebtPrevious,
    DebtRead,
    DebtTrendPoint,
    SeverityCounts,
)
from app.services import analysis_read_service

MAX_ISSUES = 500
MAX_TREND_POINTS = 30

# Duplicated from `analysis/debt.py::_SEVERITY_WEIGHT`/`_SEVERITY_RANK` per this
# codebase's "duplicate small private constants" convention (see `dashboard.py`).
# The weights are what `AnalysisRun.debt_score` is summed from, so category
# `weighted_score`s add up to the score exactly.
_SEVERITY_WEIGHT: dict[str, float] = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 5.0}
_SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}

CATEGORY_LABELS: dict[str, str] = {
    "high_complexity": "High complexity",
    "large_file": "Large file",
    "large_module": "Large module",
    "excessive_coupling": "Excessive coupling",
    "circular_dependency": "Circular dependency",
    "missing_documentation": "Missing documentation",
    "outdated_dependency": "Outdated dependency",
}

_DISPLAY_PREFIX: dict[str, str] = {
    "high_complexity": "CX",
    "large_file": "LF",
    "large_module": "LM",
    "excessive_coupling": "EC",
    "circular_dependency": "CD",
    "missing_documentation": "MD",
    "outdated_dependency": "OD",
}

# What each *class* of debt costs — fixed copy, not a claim about the specific
# finding (that is the 10.4 AI recommendation's job).
WHY: dict[str, str] = {
    "high_complexity": (
        "Functions with many branches are harder to read and to test exhaustively, and each extra path is "
        "another place for a bug to hide. Splitting them into smaller functions usually makes changes safer."
    ),
    "large_file": (
        "Big files tend to accumulate unrelated responsibilities, which makes them slow to navigate and more "
        "likely to be edited by several people at once."
    ),
    "large_module": (
        "A directory this large is hard to hold in your head; it often hides several distinct responsibilities "
        "that could be separated."
    ),
    "excessive_coupling": (
        "A module that many others depend on, or that depends on many others, turns any change to it into a "
        "change to its neighbours as well."
    ),
    "circular_dependency": (
        "Files that import each other can't be understood, tested or reused on their own, and they make "
        "load-order bugs more likely."
    ),
    "missing_documentation": (
        "Undocumented code costs every new reader time and is easier to misuse. This counts functions, classes "
        "and methods without a docstring."
    ),
    "outdated_dependency": (
        "Falling behind on a major version means missing fixes and facing a larger, riskier upgrade later. This "
        "is a version-age check against a small built-in table, not a vulnerability scan."
    ),
}

IssueKey = tuple[str, str, str, str]


# --- pure helpers ----------------------------------------------------------------


def issue_key(issue_type: str, file_path: str | None, module: str | None, description: str) -> IssueKey:
    """The identity used to diff two runs' findings. Issues have no stable
    cross-run id, so this is `(type, file, module)`; a circular-dependency
    issue has neither file nor module (its file list lives in the description),
    so the description joins its key. Approximate by design: two findings that
    share a key (two complex functions in one file) collapse to one."""
    return (
        issue_type,
        file_path or "",
        module or "",
        description if issue_type == DebtIssueType.CIRCULAR_DEPENDENCY.value else "",
    )


def diff_issue_keys(current: set[IssueKey], previous: set[IssueKey]) -> tuple[int, int]:
    """`(new, resolved)` — keys only in `current`, keys only in `previous`."""
    return len(current - previous), len(previous - current)


def pick_trend_branch(completed_branches_newest_first: list[str], selected_branch: str | None) -> str | None:
    """The branch the trend line follows — the History screen's rule, applied
    server-side: the selected branch when it has at least two completed runs,
    otherwise the branch with the most completed runs (ties go to the branch
    with the newest run, i.e. the first one seen in a newest-first list). Using
    the newest run's branch instead would let one stray side-branch run hide the
    trend of the branch that actually has history."""
    counts: dict[str, int] = defaultdict(int)
    for branch in completed_branches_newest_first:
        counts[branch] += 1
    if selected_branch is not None and counts.get(selected_branch, 0) >= 2:
        return selected_branch
    best, best_count = None, 0
    for branch in completed_branches_newest_first:  # first-seen order == newest first
        if counts[branch] > best_count:
            best, best_count = branch, counts[branch]
    return best


def count_severities(severities: list[str]) -> SeverityCounts:
    counts = SeverityCounts()
    for severity in severities:
        setattr(counts, severity, getattr(counts, severity) + 1)
    return counts


def build_categories(pairs: list[tuple[str, str]]) -> list[DebtCategory]:
    """One entry per `DebtIssueType` (all seven, including empty ones) from
    `(issue_type, severity)` pairs, sorted by `weighted_score` desc then type."""
    counts: dict[str, int] = defaultdict(int)
    weight: dict[str, float] = defaultdict(float)
    worst: dict[str, str] = {}
    for issue_type, severity in pairs:
        counts[issue_type] += 1
        weight[issue_type] += _SEVERITY_WEIGHT[severity]
        if issue_type not in worst or _SEVERITY_RANK[severity] > _SEVERITY_RANK[worst[issue_type]]:
            worst[issue_type] = severity
    categories = [
        DebtCategory(
            issue_type=t.value,
            label=CATEGORY_LABELS[t.value],
            count=counts.get(t.value, 0),
            worst_severity=worst.get(t.value),
            weighted_score=weight.get(t.value, 0.0),
        )
        for t in DebtIssueType
    ]
    categories.sort(key=lambda c: (-c.weighted_score, c.issue_type))
    return categories


def build_title(issue_type: str, file_path: str | None, module: str | None) -> str:
    label = CATEGORY_LABELS[issue_type]
    if file_path:
        return f"{label} · {PurePosixPath(file_path).name}"
    if module:
        return f"{label} · {module}"
    return label


def make_display_id(issue_type: str, issue_id: uuid.UUID) -> str:
    return f"{_DISPLAY_PREFIX[issue_type]}-{issue_id.hex[:6]}"


# --- DB entry point ----------------------------------------------------------------


def _load_issue_rows(db: Session, analysis_run_id: uuid.UUID) -> list[tuple[DebtIssue, str | None]]:
    return [
        (issue, path)
        for issue, path in db.execute(
            select(DebtIssue, File.path)
            .outerjoin(File, File.id == DebtIssue.file_id)
            .where(DebtIssue.analysis_run_id == analysis_run_id)
        ).all()
    ]


def build_debt_read(db: Session, repository: Repository, run: AnalysisRun) -> DebtRead:
    rows = _load_issue_rows(db, run.id)

    pairs = [(issue.issue_type.value, issue.severity.value) for issue, _path in rows]
    ordered = sorted(
        rows,
        key=lambda r: (
            -_SEVERITY_RANK[r[0].severity.value],
            r[0].issue_type.value,
            r[1] or "",
            r[0].description,
            str(r[0].id),
        ),
    )
    issues = [
        DebtIssueRead(
            id=issue.id,
            display_id=make_display_id(issue.issue_type.value, issue.id),
            severity=issue.severity.value,
            issue_type=issue.issue_type.value,
            category=CATEGORY_LABELS[issue.issue_type.value],
            title=build_title(issue.issue_type.value, path, issue.module),
            file_path=path,
            module=issue.module,
            metric_value=issue.metric_value,
            description=issue.description,
            why=WHY[issue.issue_type.value],
        )
        for issue, path in ordered[:MAX_ISSUES]
    ]

    previous_run = analysis_read_service.get_baseline_run(db, run)
    previous: DebtPrevious | None = None
    previous_score: float | None = None
    score_delta: float | None = None
    new_count: int | None = None
    resolved_count: int | None = None
    if previous_run is not None and previous_run.completed_at is not None:
        previous = DebtPrevious(
            analysis_run_id=previous_run.id, commit_sha=previous_run.commit_sha, completed_at=previous_run.completed_at
        )
        previous_score = previous_run.debt_score
        score_delta = round(run.debt_score - previous_run.debt_score, 2)
        current_keys = {
            issue_key(i.issue_type.value, path, i.module, i.description) for i, path in rows
        }
        previous_keys = {
            issue_key(i.issue_type.value, path, i.module, i.description)
            for i, path in _load_issue_rows(db, previous_run.id)
        }
        new_count, resolved_count = diff_issue_keys(current_keys, previous_keys)

    trend_branch, trend = _build_trend(db, repository)

    return DebtRead(
        analyzed=True,
        analysis_run_id=run.id,
        branch=run.branch,
        commit_sha=run.commit_sha,
        debt_score=run.debt_score,
        previous=previous,
        previous_debt_score=previous_score,
        score_delta=score_delta,
        total_issues=len(rows),
        severity_counts=count_severities([severity for _t, severity in pairs]),
        categories=build_categories(pairs),
        issues=issues,
        truncated=len(rows) > MAX_ISSUES,
        new_since_previous=new_count,
        resolved_since_previous=resolved_count,
        trend_branch=trend_branch,
        trend=trend,
    )


def _build_trend(db: Session, repository: Repository) -> tuple[str | None, list[DebtTrendPoint]]:
    completed = db.execute(
        select(AnalysisRun).where(
            AnalysisRun.repository_id == repository.id, AnalysisRun.status == AnalysisRunStatus.COMPLETED
        )
    ).scalars().all()
    if not completed:
        return None, []

    def when(run: AnalysisRun):
        return run.completed_at or run.created_at

    newest_first = sorted(completed, key=when, reverse=True)
    selected = db.execute(
        select(Branch.name).where(Branch.repository_id == repository.id, Branch.is_selected.is_(True))
    ).scalars().first()
    branch = pick_trend_branch([r.branch for r in newest_first], selected)
    if branch is None:
        return None, []

    series = sorted((r for r in completed if r.branch == branch), key=when)[-MAX_TREND_POINTS:]
    return branch, [
        DebtTrendPoint(
            analysis_run_id=r.id, commit_sha=r.commit_sha, completed_at=when(r), debt_score=r.debt_score
        )
        for r in series
    ]
