"""Business logic for connecting a user's GitHub App installation to their
account: verifies the installation is real, upserts the user's GitHub
identity, and stores the encrypted credential needed to act on it later.
The httpx calls to GitHub live in `github_service.py`; the FastAPI routes
(install/callback) live in `api/v1/github.py`.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decrypt_token, encrypt_token
from app.models.user import User
from app.services import github_service


class GitHubAuthError(Exception):
    """Raised for GitHub-connect failures the API layer should turn into 4xx responses."""


def get_user_by_github_id(db: Session, github_id: str) -> User | None:
    """By the *installation account's* id (`User.github_id`)."""
    return db.execute(select(User).where(User.github_id == github_id)).scalar_one_or_none()


def get_user_by_github_user_id(db: Session, github_user_id: str) -> User | None:
    """By the person's own GitHub id (`User.github_user_id`)."""
    return db.execute(select(User).where(User.github_user_id == github_user_id)).scalar_one_or_none()


@dataclass(frozen=True)
class GitHubSignInResult:
    user: User
    created: bool  # a brand-new Substrate account was made for this GitHub person
    needs_install: bool  # no installation of the App is connected yet


def get_installation_token_for_user(user: User) -> str:
    """Decrypts `user`'s stored installation id and mints a fresh
    installation access token from it — the building block every Phase 2.4+
    GitHub call needs. Raises `GitHubAuthError` if the user hasn't connected
    GitHub yet, or if GitHub rejects the (still App-JWT-signed) mint request."""
    if not user.github_access_token_encrypted:
        raise GitHubAuthError("GitHub account not connected")
    installation_id = decrypt_token(user.github_access_token_encrypted)
    try:
        token_info = github_service.get_installation_access_token(installation_id)
    except github_service.GitHubServiceError as exc:
        raise GitHubAuthError(f"Could not obtain a GitHub installation token: {exc}") from exc
    return token_info["token"]


def connect_installation(db: Session, user: User, installation_id: str) -> User:
    """Verifies `installation_id` is real and reachable (mints an
    installation access token, confirming our App's private key and the
    installation line up), records the connected GitHub account's identity
    on `user`, and stores the installation id encrypted at rest.

    The installation id — not a minted installation access token — is what
    gets encrypted and persisted. Installation access tokens expire in about
    an hour by design, so storing one would go stale almost immediately;
    every later phase (repo listing, ingestion, diff fetching) mints a fresh
    token on demand from the installation id plus the App's private key. The
    installation id is the durable credential that actually needs
    protecting at rest, so it fills the `github_access_token_encrypted`
    column provisioned for exactly this in Phase 1.
    """
    try:
        github_service.get_installation_access_token(installation_id)
        installation = github_service.get_installation(installation_id)
    except github_service.GitHubServiceError as exc:
        raise GitHubAuthError(f"Could not verify GitHub installation: {exc}") from exc

    github_id = str(installation["account"]["id"])

    existing = get_user_by_github_id(db, github_id)
    if existing is not None and existing.id != user.id:
        raise GitHubAuthError("This GitHub account is already connected to a different user")

    user.github_id = github_id
    user.github_access_token_encrypted = encrypt_token(str(installation_id))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _verified_primary_email(user_token: str) -> str | None:
    """The person's primary email if GitHub marks it verified, else `None`.
    Also `None` when the App lacks the "Email addresses" permission (GitHub
    refuses the call) — signing in must not depend on it."""
    try:
        emails = github_service.list_user_emails(user_token)
    except github_service.GitHubServiceError:
        return None
    for entry in emails:
        if entry.get("primary") and entry.get("verified") and entry.get("email"):
            return str(entry["email"]).strip().lower()
    return None


def _own_account_installation_id(user_token: str, github_user_id: str) -> str | None:
    """An installation of this App on the person's *own* account, if any.
    Organization installations are deliberately skipped: an installation token
    can read every repository the org granted, not just those this person can
    see, so connecting one needs the explicit install flow."""
    try:
        installations = github_service.list_user_installations(user_token)
    except github_service.GitHubServiceError:
        return None
    app_id = get_settings().GITHUB_APP_ID
    for installation in installations:
        if app_id and str(installation.get("app_id")) != str(app_id):
            continue
        account = installation.get("account") or {}
        if str(account.get("id")) == github_user_id and account.get("type", "User") == "User":
            return str(installation["id"])
    return None


def sign_in_with_github(
    db: Session, code: str, redirect_uri: str, *, link_user: User | None = None
) -> GitHubSignInResult:
    """Finishes the GitHub OAuth flow (Known Issue #4): proves who the person
    is on GitHub, finds / links / creates the Substrate account, and — when no
    installation is connected yet — connects their own-account installation
    straight from GitHub's answer (Known Issue #17), with no install callback.

    `link_user` is the signed-in user in "link" mode (onboarding's Authorize
    GitHub); `None` is "sign in". The GitHub user token is used here and
    dropped, never stored. Raises `GitHubAuthError` for anything the caller
    should show as a failed connection."""
    try:
        user_token = github_service.exchange_oauth_code(code, redirect_uri)
        gh_user = github_service.get_authenticated_user(user_token)
    except github_service.GitHubServiceError as exc:
        raise GitHubAuthError(f"Could not complete GitHub sign-in: {exc}") from exc

    github_user_id = str(gh_user["id"])
    login = str(gh_user.get("login") or github_user_id)

    # Whoever already holds this GitHub person: by the identity column, or —
    # for accounts that connected before this column existed — by having
    # installed the App on this person's own account (an org's id is never a
    # person's id, so this can't match an org install).
    holder = get_user_by_github_user_id(db, github_user_id) or get_user_by_github_id(db, github_user_id)

    created = False
    if link_user is not None:
        if holder is not None and holder.id != link_user.id:
            raise GitHubAuthError("This GitHub account is already connected to a different user")
        user = link_user
    elif holder is not None:
        user = holder
    else:
        email = _verified_primary_email(user_token)
        existing = (
            db.execute(select(User).where(func.lower(User.email) == email)).scalars().first()
            if email
            else None
        )
        if existing is not None:
            if existing.github_user_id and existing.github_user_id != github_user_id:
                raise GitHubAuthError("This email's account is linked to a different GitHub account")
            user = existing
        else:
            user = User(email=email or f"{github_user_id}+{login}@users.noreply.github.com".lower())
            db.add(user)
            created = True

    user.github_user_id = github_user_id
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise GitHubAuthError("Could not create the account — please try again") from exc
    db.refresh(user)

    if not user.github_access_token_encrypted:
        installation_id = _own_account_installation_id(user_token, github_user_id)
        if installation_id is not None:
            connect_installation(db, user, installation_id)

    return GitHubSignInResult(
        user=user, created=created, needs_install=not user.github_access_token_encrypted
    )
