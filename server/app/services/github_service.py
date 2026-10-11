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
from urllib.parse import urlencode

import httpx
import jwt

from app.core.config import get_settings

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
REQUEST_TIMEOUT = 15.0
ARCHIVE_DOWNLOAD_TIMEOUT = 120.0  # tarball downloads run far longer than a metadata call
ARCHIVE_CHUNK_SIZE = 1024 * 1024
APP_JWT_TTL_SECONDS = 9 * 60  # GitHub caps App JWTs at 10 minutes; stay under it.
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}

# Shared connection pool for the process lifetime, rather than opening a new
# TCP/TLS connection per call.
_client = httpx.Client(base_url=GITHUB_API_BASE, timeout=REQUEST_TIMEOUT)


class GitHubServiceError(Exception):
    """Raised when the GitHub API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"GitHub API error ({status_code}): {message}")


class GitHubArchiveTooLargeError(Exception):
    """Raised by `download_repository_archive` when the archive exceeds the
    caller-supplied byte cap. Checked against actual streamed bytes as they
    arrive, not a `Content-Length` header (which may be absent or wrong)."""

    def __init__(self, limit_bytes: int):
        self.limit_bytes = limit_bytes
        super().__init__(f"repository archive exceeded the {limit_bytes}-byte cap while downloading")


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


GITHUB_OAUTH_BASE = "https://github.com/login/oauth"


def build_authorize_url(state: str, redirect_uri: str) -> str:
    """The github.com page that asks the person to authorize this App as
    themselves (user-to-server OAuth). GitHub Apps take no `scope`; what the
    resulting token can do is set by the App's own permissions."""
    settings = get_settings()
    query = urlencode(
        {"client_id": settings.GITHUB_CLIENT_ID, "redirect_uri": redirect_uri, "state": state}
    )
    return f"{GITHUB_OAUTH_BASE}/authorize?{query}"


def exchange_oauth_code(code: str, redirect_uri: str) -> str:
    """POST github.com/login/oauth/access_token — trades the `code` GitHub
    appended to the callback for a user access token. GitHub answers 200 with
    an `error` field for a bad/used code, so the body is checked too. The token
    is used for the identity reads below and then dropped, never stored."""
    settings = get_settings()
    response = httpx.post(
        f"{GITHUB_OAUTH_BASE}/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.GITHUB_CLIENT_ID,
            "client_secret": settings.GITHUB_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code >= 400:
        raise GitHubServiceError(response.status_code, response.text)
    body = response.json()
    token = body.get("access_token")
    if not token:
        raise GitHubServiceError(400, str(body.get("error_description") or body.get("error") or "no access token"))
    return token


def get_authenticated_user(user_token: str) -> dict[str, Any]:
    """GET /user (user-token-authed) — the signed-in GitHub person (`id`, `login`)."""
    return _request("GET", "/user", token=user_token).json()


def list_user_emails(user_token: str) -> list[dict[str, Any]]:
    """GET /user/emails (user-token-authed) — needs the App's "Email addresses"
    account permission; GitHub answers 403/404 without it. Each entry has
    `email`, `primary`, `verified`."""
    return _request("GET", "/user/emails", token=user_token).json()


def list_user_installations(user_token: str) -> list[dict[str, Any]]:
    """GET /user/installations (user-token-authed) — installations of this App
    the person can access, personal and organization alike."""
    return _get_all_pages("/user/installations", token=user_token, items_key="installations")


def get_app() -> dict[str, Any]:
    """GET /app (App-JWT-authed) — this GitHub App's own metadata (id, slug,
    name, ...). `slug` is what the install URL
    (`https://github.com/apps/{slug}/installations/new`) is built from."""
    return _request("GET", "/app", use_app_jwt=True).json()


def get_installation(installation_id: str) -> dict[str, Any]:
    """GET /app/installations/{id} (App-JWT-authed) — details of a single
    installation, including the connected `account` (login/id/type)."""
    return _request("GET", f"/app/installations/{installation_id}", use_app_jwt=True).json()


def get_repository_installation(owner: str, repo: str) -> dict[str, Any]:
    """GET /repos/{owner}/{repo}/installation (App-JWT-authed) — this App's
    installation covering that repository. Raises `GitHubServiceError(404)`
    when the App is not installed on it (uninstalled, or the repository was
    removed from the installation's selected repositories)."""
    return _request("GET", f"/repos/{owner}/{repo}/installation", use_app_jwt=True).json()


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


def list_commits(
    installation_token: str, owner: str, repo: str, *, sha: str | None = None, per_page: int = 10
) -> list[dict[str, Any]]:
    """GET /repos/{owner}/{repo}/commits — the most recent commits reachable
    from `sha` (a branch name, tag, or commit SHA; GitHub's default branch
    if omitted). A single bounded page, deliberately **not** run through
    `_get_all_pages` (unlike `list_contributors` below) — a dashboard's
    "recent commits" panel (Phase 6.3) wants a small, recent, bounded list,
    not a full paginated commit history."""
    params: dict[str, Any] = {"per_page": per_page}
    if sha:
        params["sha"] = sha
    return _request("GET", f"/repos/{owner}/{repo}/commits", token=installation_token, params=params).json()


def list_contributors(installation_token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    """GET /repos/{owner}/{repo}/contributors — every contributor, paged via
    `_get_all_pages` since Phase 6.3's `repo.stats.contributors` wants an
    accurate total count, not just the first page."""
    return _get_all_pages(f"/repos/{owner}/{repo}/contributors", token=installation_token)


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


def download_repository_archive(
    *,
    installation_token: str,
    owner: str,
    repo: str,
    ref: str,
    dest_path: Path,
    max_bytes: int | None = None,
) -> int:
    """Downloads the tarball archive of `repo` at `ref` (a commit SHA, in
    Phase 3's usage — always an exact commit, never a mutable branch name) to
    `dest_path`, streamed straight to disk rather than buffered in memory.
    Used by `analysis/ingestion.py` (Phase 3.1) instead of `git clone`, which
    would need git configured in the worker and would put credentials in a
    URL.

    GitHub's tarball endpoint (`GET /repos/{owner}/{repo}/tarball/{ref}`)
    responds with a redirect to a signed, time-limited codeload.github.com
    URL. httpx's automatic redirect handling strips the `Authorization`
    header on a cross-origin redirect — the same protection `requests` has —
    which would silently break downloads of private repositories, since
    codeload requires the installation token too. So the redirect is
    followed manually here, re-attaching the same Authorization header
    explicitly rather than trusting automatic redirect handling to do it.

    Raises `GitHubServiceError` for a non-2xx/non-redirect API response, and
    `GitHubArchiveTooLargeError` if `max_bytes` is exceeded — checked against
    actual streamed bytes, not a declared `Content-Length`.

    Returns the number of bytes written. Leaves nothing behind at
    `dest_path` if the download fails partway through.
    """
    headers = _headers(token=installation_token)
    redirect_response = _client.get(
        f"/repos/{owner}/{repo}/tarball/{ref}", headers=headers, follow_redirects=False
    )
    if redirect_response.status_code >= 400:
        raise GitHubServiceError(redirect_response.status_code, redirect_response.text)
    location = redirect_response.headers.get("location")
    if redirect_response.status_code not in REDIRECT_STATUS_CODES or not location:
        raise GitHubServiceError(
            redirect_response.status_code, "expected a redirect to codeload.github.com"
        )

    written = 0
    succeeded = False
    try:
        with httpx.stream(
            "GET", location, headers=headers, timeout=ARCHIVE_DOWNLOAD_TIMEOUT
        ) as response:
            if response.status_code >= 400:
                raise GitHubServiceError(response.status_code, response.read().decode(errors="replace"))
            with open(dest_path, "wb") as fh:
                for chunk in response.iter_bytes(chunk_size=ARCHIVE_CHUNK_SIZE):
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise GitHubArchiveTooLargeError(max_bytes)
                    fh.write(chunk)
        succeeded = True
        return written
    finally:
        if not succeeded:
            dest_path.unlink(missing_ok=True)
