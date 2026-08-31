"""Branch model."""

import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("repository_id", "name", name="uq_branches_repository_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    latest_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    # Whether this is GitHub's actual default branch for the repo (i.e. it
    # matches `Repository.default_branch`) — a fact synced from GitHub, not a
    # user choice. Recomputed on every branch sync in `repo_service.upsert_branches`.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # Whether this is the branch the user has picked to analyze — independent
    # of `is_default`. Owned exclusively by `repo_service.select_branch`
    # (and seeded to match `is_default` on first sync of a repo with no
    # selection yet). At most one branch per repository should have this set;
    # enforced at the application layer, not a DB constraint.
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
