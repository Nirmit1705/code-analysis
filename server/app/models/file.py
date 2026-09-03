"""File model (Phase 4.1) — one row per file ingested by an AnalysisRun."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis.language_detect import Language
from app.core.database import Base


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_repository_id_analysis_run_id", "repository_id", "analysis_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Null for a file outside Phase 3's parsed languages (`detect_language`
    # returns `None`) — still counted toward file totals, per
    # `metrics.compute_file_metrics`'s own docstring, just never parsed.
    language: Mapped[Language | None] = mapped_column(
        Enum(Language, native_enum=False, length=16), nullable=True
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # "Containing directory" convention, same as `Symbol.module` —
    # `metrics.FileMetrics.module` is always populated (root files get ".").
    module: Mapped[str] = mapped_column(String(1024), nullable=False)
    loc: Mapped[int] = mapped_column(Integer, nullable=False)
    # Aggregate of this file's function-level complexities (Phase 3's
    # `FunctionComplexity`, keyed by `file_path`) — null when the file has no
    # function-like symbols, mirroring `ModuleMetrics.avg_complexity`'s own
    # None-when-empty convention. The exact aggregation (sum vs. average) is
    # decided by `persist_analysis_run` (Phase 4.4), not fixed by this schema.
    complexity_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_analyzed_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
