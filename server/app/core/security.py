"""Password hashing, JWT issue/verify, and encryption for secrets at rest."""

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


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=7))
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError (ExpiredSignatureError, InvalidTokenError, ...) on failure."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def create_action_token(subject: str, action_type: str, expires_delta: timedelta = timedelta(minutes=10)) -> str:
    """Short-lived, single-purpose JWT for lightweight round-trips like the
    GitHub install `state` param. Carries a distinct `type` claim (the
    caller-supplied `action_type`, e.g. "github_install_state") so it can't
    be replayed as a real access/refresh bearer token even if it leaks along
    the redirect chain through github.com."""
    expire = datetime.now(timezone.utc) + expires_delta
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": action_type}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


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
