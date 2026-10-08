"""Shared FastAPI dependencies: DB session, current user."""

from collections.abc import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.user import User
from app.services.auth_service import authenticate_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    # Signature, expiry, type, a session that hasn't been logged out, and an
    # existing user — the same check `/auth/refresh` and the WebSocket use.
    authenticated = authenticate_token(db, credentials.credentials, "access")
    if authenticated is None:
        raise unauthorized
    return authenticated[0]
