"""POST /auth/register, POST /auth/login, POST /auth/refresh, POST /auth/logout, GET /auth/me."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api.deps import bearer_scheme, get_current_user, get_db
from app.core.security import create_token_pair
from app.models.user import User
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserRead
from app.services.auth_service import (
    AuthError,
    authenticate_token,
    authenticate_user,
    get_user_by_email,
    register_user,
    revoke_session,
    session_of,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(email: str, sid: str | None = None) -> TokenResponse:
    """A token pair for a new session, or for `sid` when refreshing one."""
    access_token, refresh_token = create_token_pair(email, sid=sid)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


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
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """A new pair for the *same* session (same `sid`), so a later logout ends
    every token it ever minted. Refused once the session is logged out."""
    authenticated = authenticate_token(db, payload.refresh_token, "refresh")
    if authenticated is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    user, sid = authenticated
    return _issue_tokens(user.email, sid=sid)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: LogoutRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Response:
    """Ends the session of the refresh token in the body and/or the bearer
    access token: every token of that session — including copies elsewhere —
    is refused from now on. An *expired* token still ends its session (its
    refresh token may be alive). Always 204: logging out is idempotent, and a
    forged or unknown token reveals nothing (it simply ends no session)."""
    tokens = [t for t in (payload.refresh_token, credentials.credentials if credentials else None) if t]
    for token in tokens:
        claims = session_of(token)
        if claims is None:
            continue
        user = get_user_by_email(db, claims.get("sub") or "")
        if user is not None:
            revoke_session(db, claims["sid"], user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
