"""GitHub-connect request/response schemas."""

from pydantic import BaseModel


class GitHubInstallResponse(BaseModel):
    install_url: str


class GitHubExchangeRequest(BaseModel):
    """Phase 2.5: the one-time code the `/callback` redirect hands the
    frontend in place of raw JWTs — traded here for the real token pair."""

    code: str
