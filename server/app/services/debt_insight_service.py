"""Per-finding AI recommendation for the Debt screen (Phase 10.4) — fills the
"Recommended fix" slot that 10.3 left as an honest placeholder.

Same shape as Phase 8.7's `file_insight_service`: lazy (generated the first
time a finding is expanded, never during analysis), cached on the row
(`DebtIssue.ai_recommendation`, success only), and grounded in persisted facts.
The facts are the finding itself (category, severity, measured value, the
analyzer's sentence, file, module) plus — for a file-scoped finding — that
file's own already-persisted `RagChunk` rows, never a new `retrieve()` call
(avoids a third consumer of the OOM-prone local models, Known Issue #23).

**Never raises.** Any failure — a provider error, a misconfigured provider, a
malformed or empty response — returns `None` and persists nothing, so the next
view retries (an accepted limitation: a finding whose generation keeps failing
is retried on every expand, with no back-off). The API layer therefore never
turns a model problem into a 5xx.

A finding with no chunks (a module-level finding, or a file that produced none)
still gets a recommendation, but the prompt tells the model it is advising from
the finding alone. For a high-complexity finding, the chunks overlapping the
function's own line range (the rule's fixed "(lines a-b)" sentence) are put
first, so the model sees the actual function and not just whatever fits first.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import FileChunkSummary, build_debt_recommendation_prompt
from app.ai.provider import GroqProvider, LLMProvider, LLMProviderError
from app.analysis.chunking import RagChunkType
from app.analysis.debt import DebtIssueType
from app.models.debt_issue import DebtIssue
from app.models.file import File
from app.models.rag_chunk import RagChunk
from app.services.debt_service import CATEGORY_LABELS

logger = logging.getLogger(__name__)

# Unvalidated round number, same stance as `file_insight_service._MAX_CONTEXT_CHARS`.
MAX_CONTEXT_CHARS = 8000

# `analysis/debt.py::_check_high_complexity`'s fixed sentence: "... (lines 17-524) has ...".
_LINES_RE = re.compile(r"\(lines (\d+)-(\d+)\)")


class _ChunkView:
    """Duck-typed to `app.ai.prompts.FileChunkSummary`."""

    __slots__ = ("chunk_type", "chunk_text", "line_start", "line_end")

    def __init__(self, *, chunk_type: str, chunk_text: str, line_start: int, line_end: int):
        self.chunk_type = chunk_type
        self.chunk_text = chunk_text
        self.line_start = line_start
        self.line_end = line_end


@dataclass
class _Facts:
    """Duck-typed to `app.ai.prompts.DebtRecommendationInput`."""

    category: str
    severity: str
    metric_value: float
    description: str
    file_path: str | None
    module: str | None
    excerpts: list[FileChunkSummary] = field(default_factory=list)


def get_or_generate_recommendation(
    db: Session, *, issue: DebtIssue, provider: LLMProvider | None = None
) -> str | None:
    """The finding's recommendation text, or `None` if it could not be
    generated. See the module docstring for the contract."""
    if issue.ai_recommendation is not None:
        return issue.ai_recommendation

    try:
        facts = _build_facts(db, issue)
        system, messages, response_schema = build_debt_recommendation_prompt(facts)
        llm = provider or GroqProvider()
        raw = llm.complete(system=system, messages=messages, response_schema=response_schema)
    except LLMProviderError as exc:
        logger.warning("debt_insight_service: LLM call failed for issue %s: %s", issue.id, exc)
        return None
    except Exception:  # noqa: BLE001 — incl. a misconfigured provider's RuntimeError; never a 5xx
        logger.warning("debt_insight_service: generation failed for issue %s", issue.id, exc_info=True)
        return None

    recommendation = _parse_response(raw)
    if recommendation is None:
        logger.warning("debt_insight_service: unusable model response for issue %s", issue.id)
        return None

    try:
        issue.ai_recommendation = recommendation
        db.commit()
    except Exception:  # noqa: BLE001 — a cache-write failure must not lose a good answer
        logger.warning("debt_insight_service: could not cache the recommendation for %s", issue.id, exc_info=True)
        db.rollback()
    return recommendation


def _build_facts(db: Session, issue: DebtIssue) -> _Facts:
    file_path: str | None = None
    excerpts: list[FileChunkSummary] = []
    if issue.file_id is not None:
        file = db.get(File, issue.file_id)
        if file is not None:
            file_path = file.path
            excerpts = _load_excerpts(db, issue, file)
    return _Facts(
        category=CATEGORY_LABELS[issue.issue_type.value],
        severity=issue.severity.value,
        metric_value=issue.metric_value,
        description=issue.description,
        file_path=file_path,
        module=issue.module,
        excerpts=excerpts,
    )


def _load_excerpts(db: Session, issue: DebtIssue, file: File) -> list[FileChunkSummary]:
    """The file's own chunks, capped at `MAX_CONTEXT_CHARS` (the last one is cut
    to fit, so the cap is real). Order: chunks overlapping a high-complexity
    finding's function range, then the FILE_OVERVIEW, then the rest by index."""
    rows = db.execute(
        select(RagChunk)
        .where(RagChunk.file_id == file.id, RagChunk.analysis_run_id == issue.analysis_run_id)
        .order_by(RagChunk.chunk_index)
    ).scalars().all()
    if not rows:
        return []

    span: tuple[int, int] | None = None
    if issue.issue_type is DebtIssueType.HIGH_COMPLEXITY:
        m = _LINES_RE.search(issue.description)
        if m:
            span = (int(m.group(1)), int(m.group(2)))

    def overlaps(row: RagChunk) -> bool:
        # Only chunks of actual code count: a FILE_OVERVIEW spans the whole file, so
        # it "overlaps" every function and would outrank the chunk that holds it.
        return (
            span is not None
            and row.chunk_type is not RagChunkType.FILE_OVERVIEW
            and row.line_start <= span[1]
            and row.line_end >= span[0]
        )

    rows = sorted(rows, key=lambda r: (not overlaps(r), r.chunk_type is not RagChunkType.FILE_OVERVIEW, r.chunk_index))

    views: list[FileChunkSummary] = []
    remaining = MAX_CONTEXT_CHARS
    for row in rows:
        if remaining <= 0:
            break
        text = row.chunk_text[:remaining]
        views.append(
            _ChunkView(chunk_type=row.chunk_type.value, chunk_text=text, line_start=row.line_start, line_end=row.line_end)
        )
        remaining -= len(text)
    return views


def _parse_response(raw: str) -> str | None:
    """Defensive parse: any shape mismatch, a non-string or an empty/whitespace
    recommendation yields `None` — never a partial or fabricated result."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    recommendation = parsed.get("recommendation")
    if not isinstance(recommendation, str):
        return None
    recommendation = recommendation.strip()
    return recommendation or None
