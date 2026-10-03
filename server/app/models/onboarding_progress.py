"""OnboardingProgress model (Phase 10.10) — one row per onboarding step a user
has marked done for a repository. A step that is not done has no row (unticking
deletes it), so the table only ever holds completions.

`step_id` is the Architecture component id the step is built from (e.g.
`"server/routes"`, `"client/src"`, `"other:service"`), not an analysis-run-scoped
row id, so a tick survives re-analysis as long as the component still exists.
An id that is no longer part of the current path is simply ignored when the
path is read (never counted, never deleted), so it comes back if the component
does.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OnboardingProgress(Base):
    __tablename__ = "onboarding_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "repository_id", "step_id", name="uq_onboarding_progress_user_repository_step"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Same width as `ApiEndpoint.module`/`DebtIssue.module`: a component id is a
    # module path prefix at most.
    step_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
