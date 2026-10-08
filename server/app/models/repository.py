"""Repository model."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class AnalysisMode(str, enum.Enum):
    """When a repository is re-analyzed without the user asking (Phase 10.11).
    `ON_PUSH`: a GitHub `push` to the selected branch queues a run (the Phase
    9.5 webhook). `MANUAL`: the webhook ignores pushes; runs only start from
    the "Analyze" button. The manual endpoint works in both modes. Stored by
    member *name* (`"ON_PUSH"`), like every other enum here (Known Issue #15)."""

    ON_PUSH = "on_push"
    MANUAL = "manual"


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
    # Phase 6.1: `GitHubRepoSummary.private` was already fetched from GitHub
    # at connect time (GET /github/repos) but dropped on the floor before
    # this column existed — now persisted by `POST /repos`.
    private: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
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
    # Phase 10.11. `server_default` is the member *name* — it backfills every
    # pre-10.11 row, so a lowercase literal would make those rows unreadable
    # (`LookupError`, Known Issue #15 / the 9.5 lesson).
    analysis_mode: Mapped[AnalysisMode] = mapped_column(
        Enum(AnalysisMode, native_enum=False, length=16),
        default=AnalysisMode.ON_PUSH,
        server_default=AnalysisMode.ON_PUSH.name,
        nullable=False,
    )
    # Phase 10.11: the user's own ignore patterns, applied by the Celery task
    # *on top of* `ingestion.DEFAULT_IGNORE_PATTERNS` (never instead of them)
    # from the next run on. Already normalised and validated by
    # `schemas/repository_settings.py` (`ingestion.normalize_ignore_patterns`).
    ignore_patterns: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
