"""SQLAlchemy ORM models. Import every model module here so Alembic's
autogenerate (via Base.metadata) and app startup both see the full set."""

from app.models.user import User

__all__ = ["User"]
