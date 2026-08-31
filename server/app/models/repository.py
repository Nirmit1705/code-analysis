"""Repository model."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RepositoryAnalysisStatus(str, enum.Enum):
    """Coarse connect-time status for a Repository row.

    Phase 4/5 introduce `AnalysisRun.status` with the full per-run state
    machine (QUEUED/CLONING/.../COMPLETED/FAILED); this field just tracks
    whether *this* repository has ever completed an analysis, for use by
    screens that only need a summary (e.g. the repo list). Stored as a plain
    VARCHAR (`native_enum=False`) rather than a Postgres native enum type, so
    adding statuses later doesn't require an `ALTER TYPE` migration.
    """

    NOT_ANALYZED = "not_analyzed"
    QUEUED = "queued"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("connected_user_id", "github_repo_id", name="uq_repositories_user_github_repo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(510), nullable=False)
    # GitHub's numeric repo id, kept as a string to match the `User.github_id`
    # convention from Phase 1 rather than mixing id representations.
    github_repo_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    connected_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # GitHub App installation covering this repo; needed later (Phase 2.3) to
    # mint installation access tokens without re-deriving it from the repo.
    installation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_analyzed_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    analysis_status: Mapped[RepositoryAnalysisStatus] = mapped_column(
        Enum(RepositoryAnalysisStatus, native_enum=False, length=32),
        default=RepositoryAnalysisStatus.NOT_ANALYZED,
        server_default=RepositoryAnalysisStatus.NOT_ANALYZED.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
