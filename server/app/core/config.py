"""Application settings, read from environment variables / server/.env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    APP_ENV: str = "development"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str

    # Redis / Celery
    REDIS_URL: str

    # Auth
    SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    TOKEN_ENCRYPTION_KEY: str

    # GitHub App
    GITHUB_APP_ID: str = ""
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_PRIVATE_KEY_PATH: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""

    # LLM
    LLM_PROVIDER: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""

    # Embeddings
    EMBEDDING_PROVIDER: str = ""
    EMBEDDING_MODEL: str = ""
    EMBEDDING_DIM: int = 1024

    # Reranker
    RERANKER_PROVIDER: str = ""

    # CORS
    CORS_ALLOWED_ORIGINS: str = "http://localhost:8080"

    # Development — where GitHub-connect redirects (install/callback) send the browser back to.
    FRONTEND_URL: str = "http://localhost:8080"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
