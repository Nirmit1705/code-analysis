"""RagChunk model (Phase 7.3) — one row per chunk drafted by Phase 7.2's
`app.analysis.chunking.build_rag_chunks`. This sub-phase only creates the
table; nothing populates it yet — that's Phase 7.5's `index_analysis_run`,
once Phase 7.4's local embedding model exists to fill `embedding`.

`RagChunkType` is imported straight from the analysis layer rather than
redefined here, matching `Symbol.language`/`Symbol.kind` and
`ApiEndpoint.framework`'s existing precedent for closed-vocabulary enums.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis.chunking import RagChunkType
from app.core.database import Base

# Qwen3-Embedding-0.6B, 1024-dim — docs/PHASE_7_PLAN.md design decision #1.
# Fixed at migration time: changing this later requires a new migration
# that rebuilds the column (and re-indexing every existing chunk).
EMBEDDING_DIM = 1024


class RagChunk(Base):
    __tablename__ = "rag_chunks"
    __table_args__ = (
        Index("ix_rag_chunks_repository_id_analysis_run_id", "repository_id", "analysis_run_id"),
        Index(
            "ix_rag_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_rag_chunks_text_search", "text_search", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Denormalized from `analysis_run_id`'s parent `AnalysisRun.repository_id`
    # — see `Symbol.repository_id`'s identical rationale.
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Every `RagChunkDraft.file_path` resolves through `file_id_by_path`
    # (Phase 7.1) — `compute_file_metrics` runs unconditionally for every
    # ingested file, including README/docs, so a `File` row always exists.
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Nullable + SET NULL, same rationale as `ApiEndpoint.handler_symbol_id`:
    # only SYMBOL chunks and API_ENDPOINT chunks with a resolved handler
    # have one; FILE_OVERVIEW/DOCUMENTATION chunks never do.
    symbol_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("symbols.id", ondelete="SET NULL"), index=True, nullable=True
    )

    chunk_type: Mapped[RagChunkType] = mapped_column(
        Enum(RagChunkType, native_enum=False, length=16), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # "Containing directory" convention — always populated, same as
    # `Symbol.module`/`ApiEndpoint.module`.
    module: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Named `chunk_metadata`, not `metadata` — `metadata` is reserved on
    # `DeclarativeBase` (SQLAlchemy's own schema registry). JSONB, matching
    # `RagChunkDraft.metadata`'s own "JSON-primitive values only" contract.
    chunk_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # NOT NULL: a chunk is only ever inserted once its embedding is known —
    # indexing is wrapped in its own try/except at the call site (design
    # decision #8) and old chunks are only pruned after the new run's
    # chunks are safely committed (decision #6), so there's no code path
    # meant to leave a chunk without a vector.
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    # Generated column (STORED) — real Postgres full-text search, not
    # `ILIKE`. Always derived from `chunk_text`, never written directly.
    text_search: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', chunk_text)", persisted=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
