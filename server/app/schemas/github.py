"""GitHub-connect request/response schemas."""

from pydantic import BaseModel


class GitHubInstallResponse(BaseModel):
    install_url: str
