"""Celery application (Phase 5.2) — the async task queue Phase 5.3's
`run_repository_analysis` task will run on. Broker and result backend are
both Redis, matching Phase 0's locked infrastructure choice and the same
`settings.REDIS_URL` `core/redis.py`'s clients use.

`include` points at `app.workers.tasks.analysis_tasks` ahead of time so task
discovery is wired up now — that module is currently just a placeholder
(Phase 5.3 adds `run_repository_analysis` to it, not this turn).

No autoretry/backoff/`max_retries` configured — deliberately out of MVP scope
(see `docs/BACKEND_IMPLEMENTATION_PLAN.md`'s "What Does NOT Get Built" list:
"Automatic stuck-job recovery/retry policy for Celery — FAILED-state
surfacing only, no auto-retry").

On native Windows, Celery's default `prefork` pool doesn't work — start the
worker with `--pool=solo`:
    celery -A app.workers.celery_app worker --loglevel=info --pool=solo
"""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "codebase_intelligence",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks.analysis_tasks"],
)

celery_app.conf.update(
    task_track_started=True,
)


@celery_app.task(name="ping")
def ping() -> str:
    """Trivial built-in health-check task — not part of the analysis
    pipeline. Exists so a worker's boot and its Redis round-trip can be
    smoke-tested (`ping.delay().get()`) without needing Phase 5.3's actual
    analysis task to exist yet."""
    return "pong"
