"""User model."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The *installation account's* id (can be an org) — set by
    # `connect_installation`. Not the person's own GitHub identity; that is
    # `github_user_id` below.
    github_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # The person's own GitHub user id, proven by GitHub sign-in / link
    # (`github_auth_service.sign_in_with_github`). Known Issue #4.
    github_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    github_access_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
