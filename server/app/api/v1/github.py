"""GET /auth/github/install, GET /auth/github/callback — connects a user's
real GitHub account via the GitHub App installation flow. Repository/branch
listing and selection (`GET /github/repos`, `POST /repos`, `GET
/repos/{id}/branches`, `POST /repos/{id}/branches/{branch}/select`) are the
Phase 2.4 endpoints on `repos_router` below, in this same file per the plan.
"""

import uuid
from datetime import timedelta
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_action_token,
    create_refresh_token,
    decode_token,
    decrypt_token,
)
from app.models.branch import Branch
from app.models.repository import Repository
from app.models.user import User
from app.schemas.auth import TokenResponse
from app.schemas.github import GitHubExchangeRequest, GitHubInstallResponse
from app.schemas.repository import BranchRead, GitHubRepoSummary, RepoSelectRequest, RepositoryRead
from app.services import github_service, repo_service
from app.services.auth_service import get_user_by_email
from app.services.github_auth_service import (
    GitHubAuthError,
    connect_installation,
    get_installation_token_for_user,
)

router = APIRouter(prefix="/auth/github", tags=["github"])

# Distinguishes the install `state` param from a real access/refresh token
# when decoded — see create_action_token's docstring.
STATE_TOKEN_TYPE = "github_install_state"

# Phase 2.5: distinguishes the callback's one-time exchange code from a real
# access/refresh token, and is deliberately short-lived (60s — just long
# enough for the browser to load `/onboarding` and fire the exchange call).
EXCHANGE_TOKEN_TYPE = "github_callback_exchange"
EXCHANGE_TOKEN_TTL = timedelta(seconds=60)


@router.get("/install", response_model=GitHubInstallResponse)
def github_install(current_user: User = Depends(get_current_user)) -> GitHubInstallResponse:
    """Returns the GitHub App installation URL for the current user.

    Returned as JSON rather than issued as a raw HTTP redirect: this app's
    auth is bearer-JWT-only (no cookies/sessions per Phase 1), so only a
    `fetch` call that can attach the `Authorization` header — not a plain
    top-level browser navigation — can reach an authenticated endpoint. The
    frontend calls this, then does `window.location = install_url` itself to
    actually navigate the browser to GitHub.
    """
    app_info = github_service.get_app()
    state = create_action_token(current_user.email, STATE_TOKEN_TYPE)
    install_url = (
        f"https://github.com/apps/{app_info['slug']}/installations/new?"
        + urlencode({"state": state})
    )
    return GitHubInstallResponse(install_url=install_url)


@router.get("/callback")
def github_callback(
    installation_id: str | None = Query(default=None),
    setup_action: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """GitHub redirects here after the user completes (or cancels) the App
    install. Unlike `/install`, this is a genuine top-level navigation
    initiated by GitHub itself, so it can't carry an Authorization header —
    the `state` param minted by `/install` is what ties this request back to
    the user who started the flow. On success, the frontend is handed a
    short-lived one-time `exchange_code` (not raw tokens — see
    `EXCHANGE_TOKEN_TYPE`'s docstring) which it immediately trades for a
    real access/refresh pair via `POST /auth/github/exchange`.
    """
    settings = get_settings()
    error_url = f"{settings.FRONTEND_URL}/onboarding?github_error=1"

    if not state:
        return RedirectResponse(error_url, status_code=302)
    try:
        decoded = decode_token(state)
    except jwt.PyJWTError:
        return RedirectResponse(error_url, status_code=302)
    if decoded.get("type") != STATE_TOKEN_TYPE or not decoded.get("sub"):
        return RedirectResponse(error_url, status_code=302)

    if not installation_id or setup_action == "request":
        # `setup_action=request` means an org member without install rights
        # asked an admin to install it — nothing to connect yet.
        return RedirectResponse(error_url, status_code=302)

    user = get_user_by_email(db, decoded["sub"])
    if user is None:
        return RedirectResponse(error_url, status_code=302)

    try:
        connect_installation(db, user, installation_id)
    except GitHubAuthError:
        return RedirectResponse(error_url, status_code=302)

    exchange_code = create_action_token(user.email, EXCHANGE_TOKEN_TYPE, EXCHANGE_TOKEN_TTL)
    success_params = {"installation_id": installation_id, "exchange_code": exchange_code}
    success_url = f"{settings.FRONTEND_URL}/onboarding?" + urlencode(success_params)
    return RedirectResponse(success_url, status_code=302)


@router.post("/exchange", response_model=TokenResponse)
def github_exchange(payload: GitHubExchangeRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Trades the callback's one-time `exchange_code` for a real
    access/refresh token pair, returned in the JSON response body — never in
    a URL. Replaces the Phase 2.3 approach of putting raw JWTs directly in
    the `/callback` redirect's query string (browser history + any
    server/proxy access logs between github.com's redirect and the browser
    would otherwise carry live tokens for as long as they're valid, the
    refresh token especially since it's long-lived).

    Not enforced as strictly single-use (no server-side redemption tracking
    — would need a store, and Redis isn't wired into the app yet outside
    Celery's Phase 5 config) — mitigated by the 60s expiry on
    `EXCHANGE_TOKEN_TYPE` tokens instead, an explicit MVP simplification in
    the same spirit as the debt-detection heuristics elsewhere in this
    codebase. Revisit once Redis is wired in for Phase 5.
    """
    try:
        decoded = decode_token(payload.code)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired exchange code")
    if decoded.get("type") != EXCHANGE_TOKEN_TYPE or not decoded.get("sub"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired exchange code")

    user = get_user_by_email(db, decoded["sub"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired exchange code")

    return TokenResponse(
        access_token=create_access_token(subject=user.email),
        refresh_token=create_refresh_token(subject=user.email),
    )


# --- Phase 2.4: repository & branch APIs -----------------------------------
# No shared prefix (unlike `router` above) since these paths are `/github/repos`,
# `/repos`, and `/repos/{id}/branches...` — not all under `/auth/github`.
repos_router = APIRouter(tags=["github"])


def _installation_token_or_400(current_user: User) -> str:
    try:
        return get_installation_token_for_user(current_user)
    except GitHubAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


@repos_router.get("/github/repos", response_model=list[GitHubRepoSummary])
def list_github_repos(current_user: User = Depends(get_current_user)) -> list[GitHubRepoSummary]:
    """Lists the repos accessible to the current user's connected GitHub App
    installation — live from GitHub, not yet persisted as `Repository` rows."""
    token = _installation_token_or_400(current_user)
    try:
        gh_repos = github_service.list_installation_repositories(token)
    except github_service.GitHubServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub error: {exc}"
        ) from exc

    return [
        GitHubRepoSummary(
            github_repo_id=str(repo["id"]),
            owner=repo["owner"]["login"],
            name=repo["name"],
            full_name=repo["full_name"],
            private=repo["private"],
            language=repo.get("language"),
            default_branch=repo["default_branch"],
            stargazers_count=repo.get("stargazers_count", 0),
            size_kb=repo.get("size", 0),
            pushed_at=repo.get("pushed_at"),
        )
        for repo in gh_repos
    ]


@repos_router.post("/repos", response_model=RepositoryRead)
def select_repo(
    payload: RepoSelectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Repository:
    """The user picks one repo out of `GET /github/repos`'s list; upserts it
    as a `Repository` row scoped to the current user. The installation id is
    derived server-side from the user's own encrypted credential, never
    trusted from the request body."""
    if not current_user.github_access_token_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub account not connected")
    installation_id = decrypt_token(current_user.github_access_token_encrypted)
    return repo_service.upsert_repository(
        db,
        user=current_user,
        installation_id=installation_id,
        github_repo_id=payload.github_repo_id,
        owner=payload.owner,
        name=payload.name,
        full_name=payload.full_name,
        default_branch=payload.default_branch,
    )


@repos_router.get("/repos/{repository_id}/branches", response_model=list[BranchRead])
def list_repo_branches(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Branch]:
    """Live branch list from GitHub for a connected repo, synced into
    `Branch` rows (see `repo_service.upsert_branches`)."""
    repository = _repository_or_404(db, repository_id, current_user)
    token = _installation_token_or_400(current_user)
    try:
        gh_branches = github_service.list_branches(token, repository.owner, repository.name)
    except github_service.GitHubServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub error: {exc}"
        ) from exc
    return repo_service.upsert_branches(db, repository, gh_branches)


@repos_router.post(
    "/repos/{repository_id}/branches/{branch_name:path}/select", response_model=BranchRead
)
def select_repo_branch(
    repository_id: uuid.UUID,
    branch_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Branch:
    """Marks `branch_name` as the branch to analyze for this repo, fetching
    its current head commit sha fresh from GitHub. `{branch_name:path}` so
    branch names containing `/` (e.g. "release/2.4") round-trip correctly."""
    repository = _repository_or_404(db, repository_id, current_user)
    token = _installation_token_or_400(current_user)
    try:
        gh_branch = github_service.get_branch(token, repository.owner, repository.name, branch_name)
    except github_service.GitHubServiceError as exc:
        not_found = exc.status_code == status.HTTP_404_NOT_FOUND
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if not_found else status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub error: {exc}",
        ) from exc
    commit_sha = gh_branch["commit"]["sha"]
    return repo_service.select_branch(db, repository, branch_name, commit_sha)
