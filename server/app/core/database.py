"""SQLAlchemy engine, session factory, and declarative base.

`get_db` (the FastAPI dependency built on top of `SessionLocal`) lives in
`app.api.deps`, alongside `get_current_user`, per the Phase 1 layout.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass
