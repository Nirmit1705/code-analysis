"""AI Insights generation (Phase 8.4) — the second and last consumer of
8.1's structured-output capability, and the piece that finally gives
`DashboardRead.ai_insights` real content (Phase 6.3's placeholder).

`generate_insights` re-queries already-persisted facts (design decision #8)
rather than threading `analysis/debt.py`'s/`analysis/graph.py`'s pure
dataclass outputs through `persist_analysis_run`'s return signature again:
the top-N `DebtIssue` rows for this run (already committed by the time this
stage runs, wired into `run_repository_analysis` right after indexing), and
the same coupling/circular-dependency read pattern `app/api/v1/dependencies
.py` already established (`analysis_read_service.load_dependency_edges` +
`analyze_dependency_graph()` at read time, not a second hand-rolled graph
pass). The doc-coverage ratio is computed with the exact same query shape
`app/api/v1/dashboard.py::_raw_health_metrics` already uses for its own
"docs" health metric (expressed here as a `[0, 1]` ratio, not that
endpoint's `[0, 100]` display value — `build_insights_prompt` formats it as
a percentage itself).

Unlike `assistant_service.answer_question` (which lets a genuine
`LLMProviderError` propagate to its caller, since an interactive request
needs to distinguish "real infra failure" from "nothing to ground on"),
`generate_insights` catches every failure — bad JSON, wrong shape, or a
real provider error — and returns `[]`, per the plan's own literal
contract ("never raises"). This is a deliberate difference, not an
oversight: insight generation is an offline batch step whose caller
(`run_repository_analysis`) already wraps the whole stage in its own
non-fatal `try/except` (design decision #9) for defense in depth, but
`[]` costs nothing here — the Dashboard already renders a calm empty state
for it — so there is no reason for this function to ever raise instead of
degrading.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.prompts import build_insights_prompt
from app.ai.provider import GroqProvider, LLMProvider, LLMProviderError
from app.analysis.debt import DebtSeverity
from app.analysis.graph import analyze_dependency_graph
from app.models.analysis_run import AnalysisRun
from app.models.debt_issue import DebtIssue
from app.models.file import File
from app.models.symbol import Symbol
from app.services import analysis_read_service

logger = logging.getLogger(__name__)

# Unvalidated round numbers — flagged, not tuned, same "flag don't
# preemptively tune" stance as `analysis/debt.py`'s thresholds (Known Issue
# #13) and every other unvalidated constant introduced across Phases 7-8.
_MAX_DEBT_ISSUES = 5
_MAX_COUPLING_MODULES = 5
_MAX_CIRCULAR_GROUPS = 5

# Duplicated from `analysis/debt.py`'s own `_SEVERITY_RANK` (private by
# convention) — matches this codebase's established "duplicate small
# private constants/helpers" convention (see `app/api/v1/dashboard.py`'s
# own duplicated `_SEVERITY_WEIGHT` for the identical rationale). Needed
# because `DebtIssue.severity` can't be ordered correctly at the SQL level
# (Known Issue #15 — the column stores the enum member's *name*, not its
# value, so a raw `ORDER BY severity DESC` would sort alphabetically:
# CRITICAL, HIGH, LOW, MEDIUM — not severity order); `dashboard.py`'s own
# `_raw_health_metrics` avoids this the same way, by fetching and
# aggregating in Python rather than trusting SQL ordering of this column.
_SEVERITY_RANK: dict[DebtSeverity, int] = {
    DebtSeverity.LOW: 0,
    DebtSeverity.MEDIUM: 1,
    DebtSeverity.HIGH: 2,
    DebtSeverity.CRITICAL: 3,
}

_ALLOWED_TONES = {"danger", "warning", "info"}


class _DebtIssueView:
    """Duck-typed to `app.ai.prompts.DebtIssueSummary` — `issue_type`/
    `severity` are pre-resolved to plain strings (`.value`, not the enum
    member) per that Protocol's own documented contract, and `file_path` is
    resolved from `DebtIssue.file_id` here since the persisted row has no
    `file_path` column of its own (unlike the pure `analysis/debt.py`
    dataclass of the same name)."""

    __slots__ = ("issue_type", "severity", "file_path", "module", "description")

    def __init__(self, *, issue_type: str, severity: str, file_path: str | None, module: str | None, description: str):
        self.issue_type = issue_type
        self.severity = severity
        self.file_path = file_path
        self.module = module
        self.description = description


def generate_insights(db: Session, *, analysis_run: AnalysisRun, provider: LLMProvider | None = None) -> list[dict]:
    """Returns `[]` on any failure — bad/unparseable JSON, a response
    missing the schema's fields, or a real `LLMProviderError` — never
    raises. Never called with an empty repository's worth of facts in
    practice (a `COMPLETED` run always has *some* files/symbols), but every
    branch below degrades gracefully to "nothing to report" regardless."""
    top_issues = _top_debt_issues(db, analysis_run.id)
    edges = analysis_read_service.load_dependency_edges(db, analysis_run.id)
    graph = analyze_dependency_graph(edges)
    coupling_summary = graph.coupling[:_MAX_COUPLING_MODULES]
    circular_groups = graph.cycles[:_MAX_CIRCULAR_GROUPS]
    doc_coverage_ratio = _doc_coverage_ratio(db, analysis_run.id)

    system, messages, response_schema = build_insights_prompt(
        top_issues, coupling_summary, circular_groups, doc_coverage_ratio
    )

    llm = provider or GroqProvider()
    try:
        raw = llm.complete(system=system, messages=messages, response_schema=response_schema)
    except LLMProviderError:
        logger.warning("insight_service.generate_insights: LLM call failed for run %s", analysis_run.id)
        return []

    return _parse_insights_response(raw)


def _top_debt_issues(db: Session, analysis_run_id: uuid.UUID) -> list[_DebtIssueView]:
    issues = db.execute(select(DebtIssue).where(DebtIssue.analysis_run_id == analysis_run_id)).scalars().all()
    if not issues:
        return []

    top = sorted(issues, key=lambda issue: (-_SEVERITY_RANK[issue.severity], -issue.metric_value))[:_MAX_DEBT_ISSUES]

    file_ids = {issue.file_id for issue in top if issue.file_id is not None}
    path_by_file_id: dict[uuid.UUID, str] = {}
    if file_ids:
        path_by_file_id = dict(db.execute(select(File.id, File.path).where(File.id.in_(file_ids))).all())

    return [
        _DebtIssueView(
            issue_type=issue.issue_type.value,
            severity=issue.severity.value,
            file_path=path_by_file_id.get(issue.file_id) if issue.file_id is not None else None,
            module=issue.module,
            description=issue.description,
        )
        for issue in top
    ]


def _doc_coverage_ratio(db: Session, analysis_run_id: uuid.UUID) -> float:
    """Same query shape as `app/api/v1/dashboard.py::_raw_health_metrics`'s
    own `docs_value` — expressed as a `[0, 1]` ratio rather than that
    endpoint's `[0, 100]` display value, since `build_insights_prompt`
    formats it as a percentage itself (`f"{ratio:.0%}"`)."""
    symbol_total, symbol_documented = db.execute(
        select(func.count(), func.count().filter(Symbol.has_docstring.is_(True))).where(
            Symbol.analysis_run_id == analysis_run_id
        )
    ).one()
    return 1.0 if symbol_total == 0 else symbol_documented / symbol_total


def _parse_insights_response(raw: str) -> list[dict]:
    """All-or-nothing validation, matching `assistant_service
    ._parse_qa_response`'s own defensive-parsing style: any single
    malformed item degrades the *whole* response to `[]` rather than
    silently dropping just that one item, since a partially-fabricated
    insight list is arguably worse than an honest empty one."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []

    insights = parsed.get("insights")
    if not isinstance(insights, list):
        return []

    validated: list[dict] = []
    for item in insights:
        if not isinstance(item, dict):
            return []
        tone, title, body = item.get("tone"), item.get("title"), item.get("body")
        if tone not in _ALLOWED_TONES or not isinstance(title, str) or not isinstance(body, str):
            return []
        validated.append({"tone": tone, "title": title, "body": body})
    return validated
