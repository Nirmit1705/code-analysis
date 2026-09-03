"""ApiEndpoint model (Phase 4.1) — one row per route extracted by Phase
3.6's framework-aware API extractors (`app.analysis.apis`)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis.apis import Framework
from app.core.database import Base


class ApiEndpoint(Base):
    __tablename__ = "api_endpoints"
    __table_args__ = (
        Index("ix_api_endpoints_repository_id_analysis_run_id", "repository_id", "analysis_run_id"),
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
    # Upper-case HTTP method, plain string like `ApiRoute.method` (not an
    # enum — the analysis layer itself doesn't constrain this to a closed set).
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Nullable + SET NULL: an unresolved handler (`ApiRoute.handler_symbol
    # is None` — e.g. an inline Express callback) is a real, expected case,
    # not an error; a `Symbol` row disappearing shouldn't delete the route fact.
    handler_symbol_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("symbols.id", ondelete="SET NULL"), index=True, nullable=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # "Containing directory" convention — always populated, same as `ApiRoute.module`.
    module: Mapped[str] = mapped_column(String(1024), nullable=False)
    framework: Mapped[Framework] = mapped_column(Enum(Framework, native_enum=False, length=16), nullable=False)
    auth_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
