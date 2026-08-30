"""Shared FastAPI dependencies: DB session, current user."""

from collections.abc import Generator

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import decode_token
from app.models.user import User
from app.services.auth_service import get_user_by_email

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

    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise unauthorized from None

    if payload.get("type") != "access":
        raise unauthorized

    email = payload.get("sub")
    if not email:
        raise unauthorized

    user = get_user_by_email(db, email)
    if user is None:
        raise unauthorized

    return user
