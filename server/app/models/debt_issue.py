"""DebtIssue model (Phase 4.1) — one row per rule-based debt finding,
produced by Phase 4.3's (not yet built) `analysis/debt.py` and persisted by
Phase 4.4's `persist_analysis_run`.

`issue_type`/`severity` are plain strings, not `Enum(..., native_enum=False)`
like `Symbol.kind`/`DependencyEdge.edge_type` — those mirror an existing
Python enum already defined in `analysis/`, but `analysis/debt.py` doesn't
exist yet (Phase 4.3), so there is no enum to mirror. Revisit once 4.3 lands
its own closed-set type for these, for consistency with the rest of this table's siblings.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DebtIssue(Base):
    __tablename__ = "debt_issues"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nullable + SET NULL: a module-level finding (e.g. circular deps,
    # excessive coupling) has no single owning file, only `module` below.
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # Nullable per the plan: a file-scoped finding (e.g. one function's
    # complexity) has no single owning module beyond its file's own.
    module: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
