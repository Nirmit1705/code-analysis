"""Password hashing, JWT issue/verify, and encryption for secrets at rest."""

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import bcrypt
import jwt
from cryptography.fernet import Fernet

from app.core.config import get_settings

settings = get_settings()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


# A refresh token's lifetime, and so the longest any token of a session can
# still be valid after it was minted. Also how long a logout's revocation row
# must be kept (`auth_service.revoke_session`).
REFRESH_TOKEN_LIFETIME = timedelta(days=7)


def new_session_id() -> str:
    """Identifies one sign-in. Stamped as `sid` on the access *and* refresh
    token issued together, and kept by `/auth/refresh`, so logging out can
    revoke the whole session at once."""
    return uuid.uuid4().hex


def create_access_token(subject: str, expires_delta: timedelta | None = None, *, sid: str | None = None) -> str:
    """`sid` defaults to a fresh session (a standalone token, e.g. in the
    smoke scripts); pass the session's id when issuing a pair."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "access", "sid": sid or new_session_id()}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, expires_delta: timedelta | None = None, *, sid: str | None = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or REFRESH_TOKEN_LIFETIME)
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "refresh", "sid": sid or new_session_id()}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_token_pair(subject: str, *, sid: str | None = None) -> tuple[str, str]:
    """An access + refresh token of the same session (a new one unless `sid`
    is given — `/auth/refresh` passes the existing one)."""
    sid = sid or new_session_id()
    return create_access_token(subject, sid=sid), create_refresh_token(subject, sid=sid)


def decode_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError (ExpiredSignatureError, InvalidTokenError, ...) on failure."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def decode_token_ignoring_expiry(token: str) -> dict[str, Any]:
    """Like `decode_token` (signature still verified) but accepts an expired
    token — only for logout, which must be able to end the session behind an
    expired access token. Never use it to authorize a request."""
    return jwt.decode(
        token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM], options={"verify_exp": False}
    )


def create_action_token(subject: str, action_type: str, expires_delta: timedelta = timedelta(minutes=10)) -> str:
    """Short-lived, single-purpose JWT for lightweight round-trips like the
    GitHub install `state` param. Carries a distinct `type` claim (the
    caller-supplied `action_type`, e.g. "github_install_state") so it can't
    be replayed as a real access/refresh bearer token even if it leaks along
    the redirect chain through github.com."""
    expire = datetime.now(timezone.utc) + expires_delta
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": action_type}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_github_webhook_signature(payload: bytes, signature_header: str | None, secret: str) -> bool:
    """Checks GitHub's `X-Hub-Signature-256` header (`sha256=<hex>`, HMAC-SHA256
    of the *raw* request body keyed by the webhook secret). Never raises:
    a missing/malformed header, or an unset `secret`, is simply `False` — an
    empty secret must fail closed, since HMAC with an empty key is trivially
    forgeable by anyone. Compared as bytes so a non-ASCII header value can't
    make `hmac.compare_digest` raise `TypeError` (it only accepts ASCII-only
    `str`s), which would surface as a 500 on attacker-controlled input."""
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("utf-8"), signature_header.encode("utf-8"))


@lru_cache
def _fernet() -> Fernet:
    # Lazy + cached rather than built at import time: an unconfigured/invalid
    # TOKEN_ENCRYPTION_KEY should only break the GitHub-connect flow that
    # needs it, not this module's password hashing/JWT functions that every
    # other endpoint (including Phase 1's) depends on.
    return Fernet(settings.TOKEN_ENCRYPTION_KEY.encode("utf-8"))


def encrypt_token(plain: str) -> str:
    """Encrypts a secret (e.g. a GitHub installation id) for storage at rest,
    using Fernet (AES-128-CBC + HMAC, authenticated) with `TOKEN_ENCRYPTION_KEY`."""
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_token(token: str) -> str:
    """Raises cryptography.fernet.InvalidToken if `token` is malformed or was
    encrypted under a different key."""
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
