"""AnalysisRun model (Phase 4.1)."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AnalysisRunStatus(str, enum.Enum):
    """The full per-run state machine (distinct from the coarser
    `RepositoryAnalysisStatus`, which only summarizes a repository's most
    recent outcome). Phase 4 only ever writes `QUEUED` and `COMPLETED`/
    `FAILED` (see `persist_analysis_run`'s docstring) — the intermediate
    values exist now so Phase 5's Celery state machine has somewhere to
    write real per-stage transitions without a later migration. Stored as a
    plain VARCHAR (`native_enum=False`), matching `RepositoryAnalysisStatus`'s
    own rationale for the same choice.
    """

    QUEUED = "queued"
    CLONING = "cloning"
    PARSING = "parsing"
    ANALYZING = "analyzing"
    BUILDING_GRAPH = "building_graph"
    INDEXING = "indexing"
    GENERATING_INSIGHTS = "generating_insights"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_runs_repository_id_status", "repository_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[AnalysisRunStatus] = mapped_column(
        Enum(AnalysisRunStatus, native_enum=False, length=32),
        default=AnalysisRunStatus.QUEUED,
        server_default=AnalysisRunStatus.QUEUED.value,
        nullable=False,
    )
    # Set at row creation alongside `status=QUEUED` (see `persist_analysis_run`,
    # Phase 4.4) — defaults to insert time like `created_at` rather than
    # requiring every caller to pass it explicitly. Phase 5's Celery state
    # machine may later want this to instead mark when processing actually
    # began (vs. when it was queued); revisit then if the two need to diverge.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    module_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    api_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    dependency_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
