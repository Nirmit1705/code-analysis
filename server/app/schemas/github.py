"""GitHub-connect request/response schemas."""

from pydantic import BaseModel


class GitHubInstallResponse(BaseModel):
    install_url: str


class GitHubAuthorizeResponse(BaseModel):
    """`GET /auth/github/authorize[/link]`: where to send the browser, plus the
    `state` nonce. The frontend keeps `state` in `sessionStorage` and refuses a
    sign-in return whose `oauth_state` differs, which ties the flow to the
    browser that started it (login-CSRF protection; there are no cookies)."""

    authorize_url: str
    state: str


class GitHubExchangeRequest(BaseModel):
    """Phase 2.5: the one-time code the `/callback` redirect hands the
    frontend in place of raw JWTs — traded here for the real token pair."""

    code: str
