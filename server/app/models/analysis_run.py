"""AnalysisRun model (Phase 4.1)."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    # Added in Phase 4.4 (not in Phase 4.1's original field list — see
    # `docs/BACKEND_IMPLEMENTATION_PLAN.md`'s Known Issues list): Phase 4.3's
    # aggregate `DebtAnalysis.score` for this run, set once by
    # `persist_analysis_run`. Exists so Phase 10's debt-trend time series has
    # something to query per run without re-summing every `DebtIssue` row.
    debt_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    # Added for the Architecture screen's real "Database" component
    # (`docs/PHASE_6_PLAN.md` 6.5/6.6 follow-up): Prisma's own raw
    # datasource-provider value (e.g. "postgresql"), from
    # `analysis/datasource.py::detect_database_provider`. Nullable — most
    # runs will have no Prisma schema at all, and that's a real "no
    # evidence" outcome, not a data gap to backfill.
    database_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Phase 8.4: a plain JSONB list of `{tone, title, body}` dicts, generated
    # once per run by `insight_service.py::generate_insights` (design
    # decision #7 — no separate `AiInsight` child table, same precedent as
    # `debt_score` above). `[]` both as the Python-level default for a
    # freshly-constructed row and as the DB-level default for every
    # pre-Phase-8 row this migration touches.
    ai_insights: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
