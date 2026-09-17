"""RAG indexing orchestration (Phase 7.5) — the only place Phase 7.2's pure
`RagChunkDraft`s (`app/analysis/chunking.py`, already attached to every
`AnalysisResult.rag_chunks`) actually get embedded and become real
`RagChunk` rows. Lives in `rag/`, not `analysis/` (design decision #3 in
`docs/PHASE_7_PLAN.md`): this module touches the DB and calls a real model,
so it isn't pure the way every `analysis/` module is.

`embed_documents` is imported by name and called bare (`from
app.rag.embeddings import embed_documents`, not `embeddings.embed_documents(...)`)
— matching how `workers/tasks/analysis_tasks.py` already imports
`persist_analysis_run`/`publish_progress_event`. This isn't just style: it's
what makes `scripts/smoke_rag_indexing.py`'s monkeypatching work at all —
patching a module's own imported-by-name attribute (`app.rag.indexing.embed_documents`)
takes effect, patching the origin module (`app.rag.embeddings.embed_documents`)
after this module has already imported it would not.

Ordering matters for the "never end up with fewer chunks than the last
successful run" invariant (design decision #6): `embed_documents(...)` is
called *before* anything is written to the DB, so if it raises (model load
failure, OOM, a forced failure in tests), nothing has been inserted or
pruned yet — a prior run's chunks are left completely untouched. Pruning a
repository's old-run chunks only ever happens in its own second commit,
strictly after the new run's chunks are safely committed — the one
deliberate exception to this codebase's "never delete old rows" convention,
because no feature needs historical embeddings and they're the most
storage-expensive artifact this system produces.

Indexing failure is non-fatal to the run as a whole (design decision #8) —
but that's enforced by the *caller* (`workers/tasks/analysis_tasks.py`
wraps the call to `index_analysis_run` in its own try/except), not here.
This module raises normally on failure; it has no opinion about whether the
surrounding `AnalysisRun` should still reach `COMPLETED`.
"""

import logging
import uuid

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.analysis.pipeline import AnalysisResult
from app.analysis.symbols import Symbol
from app.models.analysis_run import AnalysisRun
from app.models.rag_chunk import RagChunk
from app.rag.embeddings import embed_documents

logger = logging.getLogger(__name__)


def index_analysis_run(
    db: Session,
    *,
    analysis_run: AnalysisRun,
    analysis_result: AnalysisResult,
    file_id_by_path: dict[str, uuid.UUID],
    symbol_id_by_symbol: dict[Symbol, uuid.UUID],
) -> int:
    """Embeds and persists every `RagChunkDraft` already attached to
    `analysis_result.rag_chunks` (Phase 7.2, built inside
    `analysis/pipeline.py::analyze_ingested`), then prunes every other
    `RagChunk` row this repository has from prior runs. Returns the number
    of chunks actually indexed for this run.

    `file_id_by_path`/`symbol_id_by_symbol` are the exact lookup maps
    `persist_analysis_run` (Phase 7.1) already built and returned — no
    second DB query needed to resolve `RagChunk.file_id`/`.symbol_id`.
    `draft.symbol` is guaranteed to be the same `Symbol` instance used to
    build `symbol_id_by_symbol` (both trace back to `pipeline.py`'s own
    `symbols_by_file` dict), so a plain dict lookup resolves it correctly.

    Raises whatever `embed_documents` raises, or an `IntegrityError`/other
    DB exception from either commit below — always after rolling back its
    own half-done transaction first, same shape as `persist_analysis_run`'s
    own try/except. Callers that want indexing failure to not lose the rest
    of an analysis run (Phase 5's Celery task) are expected to catch this
    themselves.
    """
    drafts = analysis_result.rag_chunks
    texts = [draft.chunk_text for draft in drafts]

    # The one call that can fail expensively (model load, OOM, a forced
    # failure in tests). Nothing has touched the DB yet at this point, so a
    # raise here leaves any prior run's chunks completely untouched.
    vectors = embed_documents(texts)

    rows: list[RagChunk] = []
    for draft, vector in zip(drafts, vectors):
        file_id = file_id_by_path.get(draft.file_path)
        if file_id is None:
            # Shouldn't happen — every RagChunkDraft.file_path comes from
            # the same file set file_id_by_path was built from — but never
            # fail the whole indexing pass over one unresolved chunk,
            # matching persist_analysis_run's identical stance on symbols
            # and API routes.
            logger.warning(
                "index_analysis_run: could not resolve file_id for chunk file_path=%r, skipping", draft.file_path
            )
            continue
        symbol_id = symbol_id_by_symbol.get(draft.symbol) if draft.symbol is not None else None
        rows.append(
            RagChunk(
                id=uuid.uuid4(),
                repository_id=analysis_run.repository_id,
                analysis_run_id=analysis_run.id,
                file_id=file_id,
                symbol_id=symbol_id,
                chunk_type=draft.chunk_type,
                chunk_index=draft.chunk_index,
                chunk_count=draft.chunk_count,
                chunk_text=draft.chunk_text,
                line_start=draft.line_start,
                line_end=draft.line_end,
                module=draft.module,
                chunk_metadata=draft.metadata,
                embedding=vector,
            )
        )

    try:
        db.add_all(rows)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Only reached once this run's own chunks are safely committed — prune
    # every other run's RagChunk rows for this repository (design decision
    # #6: at most one run's chunks should ever exist per repository).
    try:
        db.execute(
            delete(RagChunk).where(
                RagChunk.repository_id == analysis_run.repository_id,
                RagChunk.analysis_run_id != analysis_run.id,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return len(rows)
