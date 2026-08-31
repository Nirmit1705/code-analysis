"""GitHub API client: GitHub App JWT auth, installation access tokens, and read
access to repos/branches/commits/contents/diffs.

This is a pure `httpx` wrapper around GitHub's REST API — no FastAPI routes, no
database access, and no session/identity logic. Every read call below is scoped
by an installation access token (minted here via `get_installation_access_token`)
rather than a user OAuth token. Turning this into working endpoints — the
`/auth/github/install` and `/auth/github/callback` flow that obtains an
`installation_id` in the first place and persists it against a `User`/`Repository`
row — is Phase 2.3/2.4's job, not this module's.
"""

import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import jwt

from app.core.config import get_settings

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
REQUEST_TIMEOUT = 15.0
APP_JWT_TTL_SECONDS = 9 * 60  # GitHub caps App JWTs at 10 minutes; stay under it.

# Shared connection pool for the process lifetime, rather than opening a new
# TCP/TLS connection per call.
_client = httpx.Client(base_url=GITHUB_API_BASE, timeout=REQUEST_TIMEOUT)


class GitHubServiceError(Exception):
    """Raised when the GitHub API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"GitHub API error ({status_code}): {message}")


@lru_cache
def _private_key_bytes() -> bytes:
    settings = get_settings()
    if not settings.GITHUB_PRIVATE_KEY_PATH:
        raise RuntimeError("GITHUB_PRIVATE_KEY_PATH is not configured")
    return Path(settings.GITHUB_PRIVATE_KEY_PATH).read_bytes()


def generate_app_jwt() -> str:
    """Signs a short-lived JWT identifying the GitHub App itself (RS256, per
    GitHub's App authentication scheme), used to mint installation access
    tokens and to call App-level endpoints like `/app/installations`."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "iat": now - 60,  # backdated a minute to tolerate clock drift
        "exp": now + APP_JWT_TTL_SECONDS,
        "iss": settings.GITHUB_APP_ID,
    }
    return jwt.encode(payload, _private_key_bytes(), algorithm="RS256")


def _headers(*, token: str | None = None, use_app_jwt: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    if use_app_jwt:
        headers["Authorization"] = f"Bearer {generate_app_jwt()}"
    elif token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    use_app_jwt: bool = False,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    response = _client.request(
        method, path, headers=_headers(token=token, use_app_jwt=use_app_jwt), params=params
    )
    if response.status_code >= 400:
        raise GitHubServiceError(response.status_code, response.text)
    return response


def _get_all_pages(
    path: str,
    *,
    token: str | None = None,
    use_app_jwt: bool = False,
    items_key: str | None = None,
) -> list[dict[str, Any]]:
    """Follows GitHub's `page`/`per_page` pagination to collect every item.
    `items_key` unwraps endpoints that nest the list (e.g.
    `{"total_count": N, "repositories": [...]}`) rather than returning a bare
    JSON array."""
    items: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    while True:
        response = _request(
            "GET", path, token=token, use_app_jwt=use_app_jwt, params={"per_page": per_page, "page": page}
        )
        payload = response.json()
        page_items = payload[items_key] if items_key else payload
        items.extend(page_items)
        if len(page_items) < per_page:
            return items
        page += 1


def get_app() -> dict[str, Any]:
    """GET /app (App-JWT-authed) — this GitHub App's own metadata (id, slug,
    name, ...). `slug` is what the install URL
    (`https://github.com/apps/{slug}/installations/new`) is built from."""
    return _request("GET", "/app", use_app_jwt=True).json()


def get_installation(installation_id: str) -> dict[str, Any]:
    """GET /app/installations/{id} (App-JWT-authed) — details of a single
    installation, including the connected `account` (login/id/type)."""
    return _request("GET", f"/app/installations/{installation_id}", use_app_jwt=True).json()


def get_installation_access_token(installation_id: str) -> dict[str, str]:
    """POST /app/installations/{id}/access_tokens — mints a short-lived
    (~1 hour) installation access token, authenticated with the App JWT.
    Returns `{"token": ..., "expires_at": ...}`."""
    response = _request(
        "POST", f"/app/installations/{installation_id}/access_tokens", use_app_jwt=True
    )
    data = response.json()
    return {"token": data["token"], "expires_at": data["expires_at"]}


def list_installations() -> list[dict[str, Any]]:
    """GET /app/installations (App-JWT-authed) — every account/org that has
    installed this GitHub App."""
    return _get_all_pages("/app/installations", use_app_jwt=True)


def list_installation_repositories(installation_token: str) -> list[dict[str, Any]]:
    """GET /installation/repositories (installation-token-authed) — repos
    accessible to the connected installation."""
    return _get_all_pages(
        "/installation/repositories", token=installation_token, items_key="repositories"
    )


def list_branches(installation_token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    """GET /repos/{owner}/{repo}/branches."""
    return _get_all_pages(f"/repos/{owner}/{repo}/branches", token=installation_token)


def get_branch(installation_token: str, owner: str, repo: str, branch: str) -> dict[str, Any]:
    """GET /repos/{owner}/{repo}/branches/{branch} — a single branch's
    current head commit info. GitHub's API accepts branch names containing
    `/` (e.g. "release/2.4") directly in the path, no encoding needed."""
    return _request(
        "GET", f"/repos/{owner}/{repo}/branches/{branch}", token=installation_token
    ).json()


def get_commit(installation_token: str, owner: str, repo: str, sha: str) -> dict[str, Any]:
    """GET /repos/{owner}/{repo}/commits/{sha}."""
    return _request(
        "GET", f"/repos/{owner}/{repo}/commits/{sha}", token=installation_token
    ).json()


def get_file_contents(
    installation_token: str, owner: str, repo: str, path: str, ref: str | None = None
) -> dict[str, Any]:
    """GET /repos/{owner}/{repo}/contents/{path} — file content (base64-encoded
    by GitHub) or directory listing, at `ref` (branch/tag/commit SHA) if given."""
    params = {"ref": ref} if ref else None
    return _request(
        "GET", f"/repos/{owner}/{repo}/contents/{path}", token=installation_token, params=params
    ).json()


def get_tree(
    installation_token: str, owner: str, repo: str, tree_sha: str, recursive: bool = True
) -> dict[str, Any]:
    """GET /repos/{owner}/{repo}/git/trees/{tree_sha} — the file tree at a
    commit/tree SHA. Note GitHub silently truncates very large recursive
    trees (`"truncated": true` in the response); callers needing the full
    tree of a huge repo must page via non-recursive calls."""
    params = {"recursive": "1"} if recursive else None
    return _request(
        "GET", f"/repos/{owner}/{repo}/git/trees/{tree_sha}", token=installation_token, params=params
    ).json()


def get_diff(installation_token: str, owner: str, repo: str, base: str, head: str) -> dict[str, Any]:
    """GET /repos/{owner}/{repo}/compare/{base}...{head} — used later in
    Phase 9 for commit/push-driven change analysis."""
    return _request(
        "GET", f"/repos/{owner}/{repo}/compare/{base}...{head}", token=installation_token
    ).json()
