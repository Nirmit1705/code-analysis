"""Registration/login business logic, and the one token check every
authenticated entry point uses (`authenticate_token`) plus logout
(`revoke_session`)."""

import uuid
from datetime import datetime, timezone
from typing import Any

import jwt
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.security import (
    REFRESH_TOKEN_LIFETIME,
    decode_token,
    decode_token_ignoring_expiry,
    hash_password,
    verify_password,
)
from app.models.revoked_session import RevokedSession
from app.models.user import User


class AuthError(Exception):
    """Raised for auth failures the API layer should turn into 4xx responses."""


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def register_user(db: Session, email: str, password: str) -> User:
    if get_user_by_email(db, email) is not None:
        raise AuthError("A user with this email already exists")

    user = User(email=email, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    user = get_user_by_email(db, email)
    if user is None or user.hashed_password is None:
        raise AuthError("Incorrect email or password")
    if not verify_password(password, user.hashed_password):
        raise AuthError("Incorrect email or password")
    return user


def is_session_revoked(db: Session, sid: str) -> bool:
    return db.get(RevokedSession, sid) is not None


def authenticate_token(db: Session, token: str, expected_type: str) -> tuple[User, str] | None:
    """The token check shared by the API dependency, `/auth/refresh` and the
    WebSocket: valid signature and not expired, the expected `type`, a session
    id (`sid`) that has not been logged out, and an existing user. Returns
    `(user, sid)`, or `None` on any failure (callers answer 401 / close 1008).

    A token without a `sid` is refused: every token minted since server-side
    logout existed carries one, so a `sid`-less token predates it and could
    never be revoked."""
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        return None
    sid = payload.get("sid")
    email = payload.get("sub")
    if payload.get("type") != expected_type or not isinstance(sid, str) or not sid or not email:
        return None
    if is_session_revoked(db, sid):
        return None
    user = get_user_by_email(db, email)
    if user is None:
        return None
    return user, sid


def session_of(token: str) -> dict[str, Any] | None:
    """The claims of an access or refresh token *even if it has expired*
    (signature still verified) — logout must be able to end the session of an
    expired access token, whose refresh token may still be valid. `None` for a
    forged, malformed or `sid`-less token."""
    try:
        payload = decode_token_ignoring_expiry(token)
    except jwt.PyJWTError:
        return None
    if payload.get("type") not in ("access", "refresh") or not isinstance(payload.get("sid"), str):
        return None
    return payload


def revoke_session(db: Session, sid: str, user_id: uuid.UUID) -> None:
    """Ends a session: every token carrying `sid` is refused from now on.
    Idempotent. The row is kept for the refresh-token lifetime (no token of
    the session can outlive that, since refresh is refused once revoked), and
    rows past that are pruned here."""
    now = datetime.now(timezone.utc)
    db.execute(delete(RevokedSession).where(RevokedSession.expires_at < now))
    db.execute(
        insert(RevokedSession)
        .values(sid=sid, user_id=user_id, expires_at=now + REFRESH_TOKEN_LIFETIME)
        .on_conflict_do_nothing(index_elements=["sid"])
    )
    db.commit()
