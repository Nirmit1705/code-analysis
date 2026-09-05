"""Redis pub/sub progress events for an `AnalysisRun` (Phase 5.3).

Lives in `services/`, not `workers/`, deliberately: both
`workers/tasks/analysis_tasks.py` (the publisher, Phase 5.3) and the future
`api/ws/analysis_progress.py` (the subscriber, Phase 5.4) need to agree on
the exact channel name — putting it here avoids an awkward workers->api or
api->workers import direction, matching this codebase's existing `services/`
layer (shared business logic reachable from both `api/` and `workers/`,
e.g. `github_service.py`/`repo_service.py`).
"""

from app.core.redis import get_redis_client
from app.schemas.analysis import AnalysisProgressEvent


def progress_channel_name(analysis_run_id: str) -> str:
    """The single source of truth for this channel's name. Phase 5.4's
    WebSocket handler must import this same function rather than
    re-deriving the string, so the publisher and subscriber never drift out
    of sync on naming."""
    return f"analysis-progress:{analysis_run_id}"


def publish_progress_event(analysis_run_id: str, event: AnalysisProgressEvent) -> None:
    """Publishes `event` as JSON on `analysis_run_id`'s progress channel.

    Fire-and-forget: Redis pub/sub, not a queue — if nothing is subscribed
    (e.g. before Phase 5.4's WebSocket handler exists, or simply no browser
    connected for this run), the message is delivered to zero subscribers
    and dropped; there is nothing to replay later. Uses the synchronous
    Redis client (`core/redis.py::get_redis_client`) since every caller of
    this function (the Celery task) is itself synchronous — the async
    client stays reserved for Phase 5.4's WebSocket subscriber.
    """
    get_redis_client().publish(progress_channel_name(analysis_run_id), event.model_dump_json())
