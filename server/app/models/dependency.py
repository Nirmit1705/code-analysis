"""DependencyEdge model (Phase 4.1) — one row per file-level import/require
edge extracted by Phase 3.7 (`app.analysis.dependencies`), persisted plus
`is_circular` computed post-hoc by Phase 4.2's `analysis/graph.py`.

Distinct from `app.analysis.dependencies.DependencyEdge`, the pure Phase 3
dataclass this table is populated from — that one is an in-memory fact,
this one is the persisted row. Table name is plural (`dependency_edges`,
not `dependencies`) to avoid colliding with that module's own name and to
match this app's `<noun>_<noun>s` table-naming pattern (`api_endpoints`,
`debt_issues`).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis.dependencies import DependencyEdgeType
from app.core.database import Base


class DependencyEdge(Base):
    __tablename__ = "dependency_edges"
    __table_args__ = (
        Index("ix_dependency_edges_repository_id_analysis_run_id", "repository_id", "analysis_run_id"),
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
    # Nullable + SET NULL rather than CASCADE: a `File`/`Symbol` row being
    # removed shouldn't silently delete the dependency-edge fact itself.
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # Always null from today's file-grain `DependencyEdge` facts (Known
    # Issue #7, `docs/BACKEND_IMPLEMENTATION_PLAN.md`) — column exists now
    # so a future per-symbol import graph doesn't need a migration to add it.
    source_symbol_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("symbols.id", ondelete="SET NULL"), index=True, nullable=True
    )
    target_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # Set when `edge_type` is EXTERNAL; null for an INTERNAL edge (which
    # instead sets `target_file_id`) — mirrors the Phase 3 dataclass's own
    # `target_file`/`target_package` mutual-exclusivity.
    target_package_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    edge_type: Mapped[DependencyEdgeType] = mapped_column(
        Enum(DependencyEdgeType, native_enum=False, length=16), nullable=False
    )
    is_circular: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
