"""WS /ws/analysis-runs/{id} — live progress socket for a single `AnalysisRun`
(Phase 5.4). Subscribes to Phase 5.3's Redis pub/sub channel
(`services/progress_service.py::progress_channel_name`) and relays events to
the browser verbatim, using the same `AnalysisProgressEvent` wire format the
Celery task already publishes (`app/schemas/analysis.py`) for both the
relayed events themselves and the initial/terminal snapshot messages this
handler builds on its own — there is no second, WS-specific event shape.
Phase 5.3's files are not modified by this module.

Auth is a `?token=` query param, not the `Authorization` header
`get_current_user` reads from HTTP requests — a real browser `WebSocket`
constructor can't set arbitrary headers. The token is decoded with the exact
same primitives (`core/security.py::decode_token`,
`services/auth_service.py::get_user_by_email`) — this is the existing JWT
scheme applied to a new transport, not a new auth mechanism. Every
auth/ownership/not-found failure closes the socket with the same code (1008,
WS "Policy Violation") — the WS equivalent of this codebase's established
404-not-403 convention: never reveal *why* a run isn't visible to this
caller.

DB session is opened directly via `SessionLocal()` (not `Depends(get_db)`),
matching `workers/tasks/analysis_tasks.py`'s own pattern for the same
reason — explicit control inside an otherwise-uncommon `async def` in a
sync-everywhere-else codebase. This handler and the Celery task are the two
deliberate exceptions: this one is async because FastAPI's WebSocket support
requires it, and it's the reserved consumer of `core/redis.py`'s async
client, unused since Phase 5.2.
"""

import logging
import uuid
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.database import SessionLocal
from app.core.redis import get_async_redis_client
from app.core.security import decode_token
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus
from app.schemas.analysis import AnalysisProgressEvent
from app.services.auth_service import get_user_by_email
from app.services.progress_service import progress_channel_name
from app.services.repo_service import get_repository_for_user

logger = logging.getLogger(__name__)

router = APIRouter()

# Plain WS close-code integers rather than importing starlette.status's
# WS_*_* constants — these are standard RFC 6455 codes (1008 Policy
# Violation, 1000 Normal Closure), not framework-specific values.
_WS_POLICY_VIOLATION = 1008
_WS_NORMAL_CLOSURE = 1000

_TERMINAL_STATUS_VALUES = {AnalysisRunStatus.COMPLETED.value, AnalysisRunStatus.FAILED.value}


def _snapshot_event(analysis_run: AnalysisRun) -> AnalysisProgressEvent:
    """Builds an `AnalysisProgressEvent` from the row's *current* state —
    used for the first message sent on every connection (and the only
    message sent when the run is already terminal). Not a real published
    event (no corresponding Redis publish happened for this exact instant),
    so `timestamp` is "now", not any real stage-transition time."""
    return AnalysisProgressEvent(
        analysis_run_id=str(analysis_run.id),
        status=analysis_run.status.value,
        timestamp=datetime.now(timezone.utc),
        error_message=analysis_run.error_message,
    )


@router.websocket("/ws/analysis-runs/{analysis_run_id}")
async def analysis_progress_ws(
    websocket: WebSocket, analysis_run_id: str, token: str | None = Query(default=None)
) -> None:
    """Streams `AnalysisProgressEvent`s for one `AnalysisRun` until it
    reaches a terminal status, then closes.

    `analysis_run_id` is typed as a plain `str` (not `uuid.UUID`) and parsed
    manually below — keeping every failure mode (bad token, malformed id,
    unknown id, wrong owner) inside this function's own uniform 1008-close
    handling, rather than some of them being rejected earlier by FastAPI's
    own path/query validation with different, framework-decided behavior.

    A close code is only ever sent after `accept()` — 1008 (Policy
    Violation) is itself a post-accept application-level close, not a
    handshake-level rejection.
    """
    await websocket.accept()
    db = SessionLocal()
    try:
        if token is None:
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        try:
            payload = decode_token(token)
        except jwt.PyJWTError:
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        if payload.get("type") != "access":
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        email = payload.get("sub")
        if not email:
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        user = get_user_by_email(db, email)
        if user is None:
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return

        try:
            run_uuid = uuid.UUID(analysis_run_id)
        except ValueError:
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        analysis_run = db.get(AnalysisRun, run_uuid)
        if analysis_run is None:
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        # AnalysisRun has no direct user FK — ownership is transitive
        # through repository_id, same as the REST layer's
        # `_analysis_run_or_404` (app/api/v1/analysis.py).
        if get_repository_for_user(db, analysis_run.repository_id, user.id) is None:
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return

        if analysis_run.status.value in _TERMINAL_STATUS_VALUES:
            # Already done by the time the client connected — one snapshot,
            # then close. Nothing to subscribe to: a terminal run will never
            # publish again.
            await websocket.send_text(_snapshot_event(analysis_run).model_dump_json())
            await websocket.close(code=_WS_NORMAL_CLOSURE)
            return

        pubsub = get_async_redis_client().pubsub()
        try:
            # Subscribe BEFORE reading the snapshot: an event published in
            # the gap between the ownership check above and this line is
            # still caught, because the `db.refresh()` right below re-reads
            # the row's actual current state rather than depending on having
            # received that specific pub/sub message.
            await pubsub.subscribe(progress_channel_name(str(analysis_run.id)))

            db.refresh(analysis_run)
            await websocket.send_text(_snapshot_event(analysis_run).model_dump_json())

            if analysis_run.status.value in _TERMINAL_STATUS_VALUES:
                # Reached terminal in the gap between the first check above
                # and subscribing — nothing more will ever publish on this
                # channel, so stop here instead of listening forever.
                await websocket.close(code=_WS_NORMAL_CLOSURE)
                return

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                # Relayed verbatim — the publisher's own
                # `event.model_dump_json()` output, no re-serialization.
                await websocket.send_text(message["data"])
                event = AnalysisProgressEvent.model_validate_json(message["data"])
                if event.status in _TERMINAL_STATUS_VALUES:
                    break

            await websocket.close(code=_WS_NORMAL_CLOSURE)
        except WebSocketDisconnect:
            logger.info("analysis_progress_ws: client disconnected for run %s", analysis_run_id)
        except Exception:  # noqa: BLE001 — never let a relay-loop error crash the connection ungracefully
            logger.exception("analysis_progress_ws: relay loop failed for run %s", analysis_run_id)
        finally:
            # Cleanup itself must never raise — e.g. if `subscribe()` above
            # never actually connected (Redis unreachable), `unsubscribe()`/
            # `close()` would otherwise try that same failing connection
            # again and escape this `finally`, masking whatever exception
            # (if any) was already being handled above.
            try:
                await pubsub.unsubscribe()
                await pubsub.close()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "analysis_progress_ws: pubsub cleanup failed for run %s", analysis_run_id
                )
    finally:
        db.close()
