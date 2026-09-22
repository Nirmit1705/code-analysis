"""Per-file AI purpose/explanation (Phase 8.7) — fills the Codebase
Explorer's `FileInsightRead.purpose`/`.explanation`, hardcoded `None` since
Phase 6.7/6.8 (`purpose`/`explanation` were always deferred to "Phase 8's
job", but this specific gap was never actually part of the approved 8.1-8.6
plan — a real scope gap found and scoped as its own sub-phase after 8.5's
live verification, see `docs/PHASE_8_PLAN.md` 8.7).

Grounds itself in the file's own already-persisted `RagChunk` rows
(`WHERE file_id = ... AND analysis_run_id = ...`), never a new
`rag/retrieval.py::retrieve()` call (design decision #1) — strictly better
grounding for "explain this exact file" than a generic hybrid search, and
avoids adding a second consumer of the local embedding/reranker CUDA models
on this dev machine's already OOM-prone 6GB-VRAM GPU (Known Issue #23).

`get_or_generate_file_insight` is the first GET-triggered `db.commit()` in
this codebase (design decision #5) — every other `api/v1/*.py` read path is
pure. Lazy + cached directly on the `File` row (design decision #2): a
cache hit (`file.purpose` already set) returns instantly with zero provider
calls; a file with no `RagChunk` rows at all short-circuits to `(None,
None)` without an LLM call (design decision #3); a failed or malformed LLM
response also degrades to `(None, None)` and is never persisted, so it's
retried on the next view rather than permanently stuck (design decisions
#4/#6 — a known, accepted limitation, not a bug: a file whose generation
keeps failing is retried on every view rather than backing off)."""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import FileChunkSummary, build_file_insight_prompt
from app.ai.provider import GroqProvider, LLMProvider, LLMProviderError
from app.analysis.chunking import RagChunkType
from app.models.file import File
from app.models.rag_chunk import RagChunk

logger = logging.getLogger(__name__)

# Unvalidated round number — flagged, not tuned, same "flag don't
# preemptively tune" stance as every other constant introduced since Phase
# 7.2's splitter constants (Known Issue #13's own precedent).
_MAX_CONTEXT_CHARS = 8000


class _ChunkView:
    """Duck-typed to `app.ai.prompts.FileChunkSummary`."""

    __slots__ = ("chunk_type", "chunk_text", "line_start", "line_end")

    def __init__(self, *, chunk_type: str, chunk_text: str, line_start: int, line_end: int):
        self.chunk_type = chunk_type
        self.chunk_text = chunk_text
        self.line_start = line_start
        self.line_end = line_end


def get_or_generate_file_insight(
    db: Session, *, file: File, provider: LLMProvider | None = None
) -> tuple[str | None, str | None]:
    """Returns `(purpose, explanation)`. See this module's own docstring for
    the cache-hit / zero-chunk / malformed-response contracts."""
    if file.purpose is not None:
        return file.purpose, file.explanation

    chunks = _load_file_chunks(db, file)
    if not chunks:
        return None, None

    system, messages, response_schema = build_file_insight_prompt(file.path, chunks)

    llm = provider or GroqProvider()
    try:
        raw = llm.complete(system=system, messages=messages, response_schema=response_schema)
    except LLMProviderError:
        logger.warning("file_insight_service: LLM call failed for file %s", file.id)
        return None, None

    purpose, explanation = _parse_response(raw)
    if purpose is None or explanation is None:
        return None, None

    file.purpose = purpose
    file.explanation = explanation
    db.commit()
    return purpose, explanation


def _load_file_chunks(db: Session, file: File) -> list[FileChunkSummary]:
    """FILE_OVERVIEW first (the single most useful chunk for "what is this
    file" — a synthesized structural summary), then the rest in their
    existing `chunk_index` order, capped at `_MAX_CONTEXT_CHARS` (design
    decision #1). A file with zero rows here (config/lockfile-style, no
    symbols, not on the manifest/doc allowlist — see `analysis/chunking.py`)
    returns `[]`, the caller's zero-chunk short-circuit signal."""
    rows = db.execute(
        select(RagChunk)
        .where(RagChunk.file_id == file.id, RagChunk.analysis_run_id == file.analysis_run_id)
        .order_by(RagChunk.chunk_index)
    ).scalars().all()
    if not rows:
        return []

    rows.sort(key=lambda r: (r.chunk_type is not RagChunkType.FILE_OVERVIEW, r.chunk_index))

    views: list[FileChunkSummary] = []
    total_chars = 0
    for row in rows:
        if total_chars >= _MAX_CONTEXT_CHARS:
            break
        views.append(
            _ChunkView(
                chunk_type=row.chunk_type.value,
                chunk_text=row.chunk_text,
                line_start=row.line_start,
                line_end=row.line_end,
            )
        )
        total_chars += len(row.chunk_text)
    return views


def _parse_response(raw: str) -> tuple[str | None, str | None]:
    """Defensive parsing matching `insight_service._parse_insights_response`'s
    own style — any shape mismatch degrades to `(None, None)` rather than
    raising or persisting a partial/fabricated result."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(parsed, dict):
        return None, None

    purpose, explanation = parsed.get("purpose"), parsed.get("explanation")
    if not isinstance(purpose, str) or not isinstance(explanation, str):
        return None, None
    return purpose, explanation
