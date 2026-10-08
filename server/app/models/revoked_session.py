"""RevokedSession model — sign-in sessions ended by `POST /auth/logout`.

Access and refresh tokens are stateless JWTs; each pair carries a session id
(`sid`, see `core/security.py::new_session_id`). Logging out inserts that `sid`
here, and every place that accepts a token (`api/deps.py::get_current_user`,
`/auth/refresh`, the analysis-progress WebSocket) refuses a revoked one, so a
copied token stops working immediately rather than when it expires.

A row is only needed while a token of that session could still be valid:
`expires_at` = revocation time + the refresh-token lifetime (refresh is refused
once revoked, so no newer token of the session can exist). Expired rows are
pruned whenever another session is revoked.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RevokedSession(Base):
    __tablename__ = "revoked_sessions"

    sid: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
