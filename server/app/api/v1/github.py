"""GET /auth/github/install, GET /auth/github/callback — connects a user's
real GitHub account via the GitHub App installation flow. Repository/branch
listing and selection (`GET /github/repos`, `POST /repos`, `GET
/repos/{id}/branches`, `POST /repos/{id}/branches/{branch}/select`) are the
Phase 2.4 endpoints on `repos_router` below, in this same file per the plan.
"""

import hashlib
import json
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.core.redis import get_redis_client
from app.core.security import (
    create_action_token,
    create_token_pair,
    decode_token,
    decrypt_token,
)
from app.models.branch import Branch
from app.models.repository import Repository
from app.models.user import User
from app.schemas.auth import TokenResponse
from app.schemas.github import GitHubAuthorizeResponse, GitHubExchangeRequest, GitHubInstallResponse
from app.schemas.repository import (
    BranchRead,
    GitHubRepoSummary,
    RepoSelectRequest,
    RepositoryListItem,
    RepositoryRead,
)
from app.services import github_service, repo_service
from app.services.auth_service import get_user_by_email
from app.services.github_auth_service import (
    GitHubAuthError,
    connect_installation,
    get_installation_token_for_user,
    sign_in_with_github,
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

# Phase 5.2 (Known Issue #2): Redis key prefix for the exchange-code
# redemption check below — keyed on sha256(code), never the raw code itself,
# so a Redis `KEYS`/dump doesn't hand out live exchange codes.
EXCHANGE_REDEMPTION_KEY_PREFIX = "github_exchange_redeemed:"


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
    error: str | None = Query(default=None),
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

    if error and state:
        # Declining on github.com's *sign-in* authorize page is sent here (the
        # App's first registered Callback URL), not to `/login/callback`, with
        # the sign-in flow's state nonce. Send the person back to the page that
        # started that flow instead of to onboarding.
        saved = _consume_oauth_state(state)
        if saved is not None:
            return RedirectResponse(
                f"{settings.FRONTEND_URL}/{_oauth_return_page(saved['mode'])}?github_error=1", status_code=302
            )
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


# Known Issue #4/#17: GitHub *identity* flow (OAuth as the person), used both
# to sign in with GitHub and to link GitHub from onboarding. State is a random
# nonce held in Redis (single-use, short-lived) rather than a JWT, because the
# sign-in mode has no user yet to put in `sub`.
OAUTH_STATE_KEY_PREFIX = "github_oauth_state:"
OAUTH_STATE_TTL_SECONDS = 600
OAUTH_MODE_SIGNIN = "signin"
OAUTH_MODE_LINK = "link"


def _oauth_state_key(nonce: str) -> str:
    return OAUTH_STATE_KEY_PREFIX + hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def _oauth_redirect_uri() -> str:
    return f"{get_settings().BACKEND_URL.rstrip('/')}/api/v1/auth/github/login/callback"


def _consume_oauth_state(nonce: str) -> dict | None:
    """Spends an OAuth state nonce (GETDEL: usable exactly once, whatever
    happens next) and returns what was saved with it, or `None` if it is
    unknown, expired or already used."""
    raw = get_redis_client().getdel(_oauth_state_key(nonce))
    if raw is None:
        return None
    try:
        saved = json.loads(raw)
        return saved if saved.get("mode") else None
    except (ValueError, AttributeError):
        return None


def _oauth_return_page(mode: str) -> str:
    return "onboarding" if mode == OAUTH_MODE_LINK else "login"


def _start_oauth(mode: str, email: str | None = None) -> GitHubAuthorizeResponse:
    settings = get_settings()
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GitHub sign-in is not configured")
    nonce = secrets.token_urlsafe(32)
    get_redis_client().set(
        _oauth_state_key(nonce),
        json.dumps({"mode": mode, "email": email}),
        ex=OAUTH_STATE_TTL_SECONDS,
    )
    return GitHubAuthorizeResponse(
        authorize_url=github_service.build_authorize_url(nonce, _oauth_redirect_uri()), state=nonce
    )


@router.get("/authorize", response_model=GitHubAuthorizeResponse)
def github_authorize() -> GitHubAuthorizeResponse:
    """Starts "Continue with GitHub" for a visitor who isn't signed in. JSON
    rather than a redirect for the same reason as `/install`."""
    return _start_oauth(OAUTH_MODE_SIGNIN)


@router.get("/authorize/link", response_model=GitHubAuthorizeResponse)
def github_authorize_link(current_user: User = Depends(get_current_user)) -> GitHubAuthorizeResponse:
    """Starts the same flow for a signed-in user (onboarding's Authorize
    GitHub), so the GitHub person is attached to *this* account."""
    return _start_oauth(OAUTH_MODE_LINK, current_user.email)


@router.get("/login/callback")
def github_login_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """GitHub redirects the browser here after the person authorizes (or
    declines). Consumes the state nonce, finishes the flow in
    `github_auth_service.sign_in_with_github`, and hands the frontend a
    one-time `exchange_code` (traded at `/exchange`, like the install flow) —
    never tokens in a URL. Every failure is a redirect with `github_error=1`."""
    settings = get_settings()
    login_error = f"{settings.FRONTEND_URL}/login?github_error=1"
    if not state:
        return RedirectResponse(login_error, status_code=302)

    saved = _consume_oauth_state(state)
    if saved is None:
        return RedirectResponse(login_error, status_code=302)
    mode = saved["mode"]

    page = _oauth_return_page(mode)
    error_url = f"{settings.FRONTEND_URL}/{page}?github_error=1"
    if not code:  # the person declined on github.com (`error=access_denied`)
        return RedirectResponse(error_url, status_code=302)

    link_user = None
    if mode == OAUTH_MODE_LINK:
        link_user = get_user_by_email(db, saved.get("email") or "")
        if link_user is None:
            return RedirectResponse(error_url, status_code=302)

    try:
        result = sign_in_with_github(db, code, _oauth_redirect_uri(), link_user=link_user)
    except GitHubAuthError:
        return RedirectResponse(error_url, status_code=302)

    params = {
        "exchange_code": create_action_token(result.user.email, EXCHANGE_TOKEN_TYPE, EXCHANGE_TOKEN_TTL),
        "oauth_state": state,
    }
    if result.needs_install:
        params["needs_install"] = "1"
    return RedirectResponse(f"{settings.FRONTEND_URL}/{page}?" + urlencode(params), status_code=302)


@router.post("/exchange", response_model=TokenResponse)
def github_exchange(payload: GitHubExchangeRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Trades the callback's one-time `exchange_code` for a real
    access/refresh token pair, returned in the JSON response body — never in
    a URL. Replaces the Phase 2.3 approach of putting raw JWTs directly in
    the `/callback` redirect's query string (browser history + any
    server/proxy access logs between github.com's redirect and the browser
    would otherwise carry live tokens for as long as they're valid, the
    refresh token especially since it's long-lived).

    **Phase 5.2 (Known Issue #2, resolved): server-side single-use
    enforcement.** A Redis `SETNX`-style redemption key (`sha256(code)`,
    never the raw code, TTL matching `EXCHANGE_TOKEN_TTL`) is checked right
    after the decode/expiry/type validation below — a second exchange
    attempt with the exact same code is rejected with the same generic 400
    an invalid/expired code gets, well within the code's own 60s window, not
    just once it actually expires. Previously (Phase 2.5) this was only
    mitigated by that 60s expiry, with no redemption tracking at all, since
    Redis wasn't wired into the app yet.
    """
    try:
        decoded = decode_token(payload.code)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired exchange code")
    if decoded.get("type") != EXCHANGE_TOKEN_TYPE or not decoded.get("sub"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired exchange code")

    redemption_key = EXCHANGE_REDEMPTION_KEY_PREFIX + hashlib.sha256(payload.code.encode("utf-8")).hexdigest()
    first_use = get_redis_client().set(
        redemption_key, "1", nx=True, ex=int(EXCHANGE_TOKEN_TTL.total_seconds())
    )
    if not first_use:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired exchange code")

    user = get_user_by_email(db, decoded["sub"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired exchange code")

    access_token, refresh_token = create_token_pair(user.email)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


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


@repos_router.get("/repos", response_model=list[RepositoryListItem])
def list_repos(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[RepositoryListItem]:
    """Lists the current user's already-connected `Repository` rows (i.e.
    ones previously persisted via `POST /repos` below), newest first — not
    the live `GET /github/repos` list. New in Phase 6.1: resolves "which
    repository is `/app/*` currently showing" on a fresh page load, since
    nothing before this phase needed to list back what a user had already
    connected."""
    repositories = repo_service.list_repositories_for_user(db, current_user.id)
    last_analyzed = repo_service.get_last_completed_analysis_times(db, [r.id for r in repositories])
    return [
        RepositoryListItem.model_validate(r).model_copy(
            update={"last_completed_analysis_at": last_analyzed.get(r.id)}
        )
        for r in repositories
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
        private=payload.private,
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
