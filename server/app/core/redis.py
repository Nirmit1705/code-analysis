"""Redis client factories (Phase 5.2).

Two cached singletons, both backed by `settings.REDIS_URL` — the same broker/
result-backend URL `workers/celery_app.py` configures Celery with directly
(Celery manages its own connection pool internally; these are for
application code that needs to talk to Redis itself):

- `get_redis_client()` — a synchronous client. Used by the exchange-code
  redemption check in `api/v1/github.py` (Known Issue #2), and generally by
  any other sync code path (this app's routes and services are sync per
  Phase 1's "sync SQLAlchemy + sync FastAPI routes" decision).
- `get_async_redis_client()` — an asynchronous client, for Phase 5.4's
  `/ws/analysis-runs/{id}` WebSocket handler, which subscribes to a Redis
  pub/sub channel from FastAPI's async event loop and can't block on the
  sync client above. Not used yet as of Phase 5.2 — wired up ahead of time
  the same way `celery_app.py`'s `include=[...]` wires up task discovery
  ahead of Phase 5.3.

Both are lazy + `lru_cache`d singletons, matching `core/security.py`'s
existing `_fernet()` pattern for external clients: built on first use, not at
import time, so an unreachable Redis doesn't break importing this module.
"""

from functools import lru_cache

import redis
import redis.asyncio as redis_asyncio

from app.core.config import get_settings


@lru_cache
def get_redis_client() -> redis.Redis:
    """Cached synchronous Redis client."""
    settings = get_settings()
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


@lru_cache
def get_async_redis_client() -> redis_asyncio.Redis:
    """Cached asynchronous Redis client."""
    settings = get_settings()
    return redis_asyncio.Redis.from_url(settings.REDIS_URL, decode_responses=True)
