"""POST /auth/register, POST /auth/login, POST /auth/refresh, GET /auth/me."""

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserRead
from app.services.auth_service import AuthError, authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(email: str) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(subject=email),
        refresh_token=create_refresh_token(subject=email),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        user = register_user(db, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _issue_tokens(user.email)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        user = authenticate_user(db, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    return _issue_tokens(user.email)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest) -> TokenResponse:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
    )
    try:
        decoded = decode_token(payload.refresh_token)
    except jwt.PyJWTError:
        raise unauthorized from None

    if decoded.get("type") != "refresh" or not decoded.get("sub"):
        raise unauthorized

    return _issue_tokens(decoded["sub"])


@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
