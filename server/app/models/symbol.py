"""Symbol model (Phase 4.1) — one row per function/class/method/component
extracted by Phase 3.3-3.5."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis.language_detect import Language
from app.analysis.symbols import SymbolKind
from app.core.database import Base


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        Index("ix_symbols_repository_id_analysis_run_id", "repository_id", "analysis_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Denormalized from `analysis_run_id`'s parent `AnalysisRun.repository_id`
    # (not part of the plan's literal field list for this table) solely so
    # the plan's own "(repository_id, analysis_run_id)" index can exist
    # without an extra join through `analysis_runs` on every repo-scoped
    # query. Always equal to the owning `AnalysisRun.repository_id`; not
    # independently settable.
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[SymbolKind] = mapped_column(Enum(SymbolKind, native_enum=False, length=16), nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # "Containing directory" convention — always populated, same as
    # `analysis.symbols.Symbol.module` (root-level files get ".").
    module: Mapped[str] = mapped_column(String(1024), nullable=False)
    language: Mapped[Language] = mapped_column(Enum(Language, native_enum=False, length=16), nullable=False)
    has_docstring: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
