"""DebtIssue model (Phase 4.1) — one row per rule-based debt finding,
produced by Phase 4.3's `analysis/debt.py` and persisted by Phase 4.4's
`persist_analysis_run`.

`issue_type`/`severity` mirror `analysis/debt.py`'s own `DebtIssueType`/
`DebtSeverity` enums via `Enum(..., native_enum=False)`, matching
`Symbol.kind`/`DependencyEdge.edge_type`'s established pattern — upgraded
from plain `String` columns in a Phase 4.4 follow-up migration (Known Issue
#10 in `docs/BACKEND_IMPLEMENTATION_PLAN.md`) once `analysis/debt.py`
existed to mirror; no data migration was needed since every value it writes
(e.g. `"high_complexity"`, `"critical"`) already matched the new enums'
lowercase `.value`s.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis.debt import DebtIssueType, DebtSeverity
from app.core.database import Base


class DebtIssue(Base):
    __tablename__ = "debt_issues"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    issue_type: Mapped[DebtIssueType] = mapped_column(
        Enum(DebtIssueType, native_enum=False, length=32), nullable=False
    )
    severity: Mapped[DebtSeverity] = mapped_column(
        Enum(DebtSeverity, native_enum=False, length=16), nullable=False
    )
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
