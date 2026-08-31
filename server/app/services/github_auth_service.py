"""Business logic for connecting a user's GitHub App installation to their
account: verifies the installation is real, upserts the user's GitHub
identity, and stores the encrypted credential needed to act on it later.
The httpx calls to GitHub live in `github_service.py`; the FastAPI routes
(install/callback) live in `api/v1/github.py`.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decrypt_token, encrypt_token
from app.models.user import User
from app.services import github_service


class GitHubAuthError(Exception):
    """Raised for GitHub-connect failures the API layer should turn into 4xx responses."""


def get_user_by_github_id(db: Session, github_id: str) -> User | None:
    return db.execute(select(User).where(User.github_id == github_id)).scalar_one_or_none()


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
