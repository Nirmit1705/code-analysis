"""GitHub App webhook receiver (Phase 9.5): `POST /webhooks/github`.

Real flow: GitHub (or, in local dev, GitHub -> Smee.io -> `smee-client`) POSTs
a signed delivery here; a verified `push` to a repository's currently
selected branch (`Branch.is_selected`) queues a `WEBHOOK`-tagged
`AnalysisRun` and enqueues the exact same `run_repository_analysis` Celery
task the manual endpoint uses — no new task.

Auth is the HMAC signature alone — there is no bearer token/`User` on a
GitHub delivery. So the endpoint takes the raw `Request` (not a Pydantic body
model) and verifies `X-Hub-Signature-256` over the exact raw bytes *before*
any JSON parsing; a bad/missing signature is the only `4xx` this route ever
returns. Everything else that isn't an actionable push — a different event
type, a malformed-but-signed body, an unconnected repository, a push to an
untracked branch, a repository set to manual analysis (Phase 10.11's
`Repository.analysis_mode`), a branch/tag deletion, a tag push — is an honest
`200 {"status": "ignored", "reason": ...}` (design decision #11): GitHub
treats non-2xx as a failed delivery, and a verified-but-irrelevant delivery
is not an error.
"""

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.security import verify_github_webhook_signature
from app.models.analysis_run import AnalysisRunTriggerSource
from app.models.branch import Branch
from app.models.repository import AnalysisMode
from app.services import repo_service
from app.workers.tasks.analysis_tasks import run_repository_analysis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

_BRANCH_REF_PREFIX = "refs/heads/"
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# GitHub's `after` on a branch-deletion push.
_NULL_SHA = "0" * 40


def _ignored(reason: str) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ignored", "reason": reason})


def _handle_push(db: Session, payload: dict[str, Any]) -> JSONResponse:
    """Sync (DB + Redis I/O) — run via `run_in_threadpool` so it never blocks
    the event loop."""
    repository_payload = payload.get("repository")
    ref = payload.get("ref")
    after = payload.get("after")
    if not isinstance(repository_payload, dict) or not isinstance(ref, str) or not isinstance(after, str):
        return _ignored("malformed push payload")

    # GitHub sends `repository.id` as a JSON integer; `Repository.github_repo_id`
    # is stored as a string, so an un-coerced lookup would silently never match.
    raw_repo_id = repository_payload.get("id")
    if isinstance(raw_repo_id, bool) or not isinstance(raw_repo_id, (int, str)):
        return _ignored("malformed push payload")
    github_repo_id = str(raw_repo_id)

    if not ref.startswith(_BRANCH_REF_PREFIX):
        return _ignored("push is not to a branch")
    branch_name = ref[len(_BRANCH_REF_PREFIX):]

    if payload.get("deleted") is True or after == _NULL_SHA:
        return _ignored("branch deletion")
    if not _COMMIT_SHA_RE.match(after):
        return _ignored("malformed push payload")

    # Fan out to *every* connected copy of this GitHub repo (Known Issue #28):
    # `github_repo_id` is only unique per Substrate user, and a delivery
    # carries no user, so each user who connected it gets their own run.
    repositories = repo_service.list_repositories_by_github_repo_id(db, github_repo_id)
    if not repositories:
        return _ignored("repository not connected")

    queued_run_ids: list[str] = []
    skip_reasons: list[str] = []
    enqueue_failed = False
    for repository in repositories:
        # Phase 10.11: a copy set to `MANUAL` only analyzes from the "Analyze"
        # button. Checked per copy — other users' copies still fan out.
        if repository.analysis_mode is AnalysisMode.MANUAL:
            skip_reasons.append("analysis mode is manual")
            continue
        selected_branch = db.execute(
            select(Branch).where(Branch.repository_id == repository.id, Branch.is_selected == True)  # noqa: E712
        ).scalar_one_or_none()
        if selected_branch is None or selected_branch.name != branch_name:
            skip_reasons.append("push is not to the tracked branch")
            continue

        # Idempotency: a redelivered/duplicate push for a commit that already
        # has a (non-failed) run must not queue a second one. The advisory
        # lock closes the race between a redelivery and the original — it's
        # released by `create_queued_analysis_run`'s commit, or by the
        # rollback on the skip path.
        repo_service.lock_analysis_run_slot(
            db, repository_id=repository.id, branch=branch_name, commit_sha=after
        )
        if repo_service.has_analysis_run_for_commit(
            db, repository_id=repository.id, branch=branch_name, commit_sha=after
        ):
            db.rollback()
            skip_reasons.append("commit already analyzed or queued")
            continue

        analysis_run = repo_service.create_queued_analysis_run(
            db,
            repository_id=repository.id,
            branch=branch_name,
            commit_sha=after,
            trigger_source=AnalysisRunTriggerSource.WEBHOOK,
        )
        try:
            run_repository_analysis.delay(str(analysis_run.id))
        except Exception as exc:  # noqa: BLE001 — broker down: don't leave the row stuck at QUEUED forever
            logger.exception("webhook: failed to enqueue analysis run %s", analysis_run.id)
            repo_service.mark_analysis_run_failed(
                db, analysis_run=analysis_run, error_message=f"Failed to enqueue analysis task: {exc}"
            )
            enqueue_failed = True
            continue
        queued_run_ids.append(str(analysis_run.id))

    if enqueue_failed:
        # 5xx (not 200): GitHub marks the delivery failed and it can be
        # redelivered — the dedupe above skips every copy that did queue and
        # retries only the one whose row is now FAILED.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "reason": "could not enqueue analysis", "analysis_run_ids": queued_run_ids},
        )
    if not queued_run_ids:
        return _ignored("; ".join(dict.fromkeys(skip_reasons)))
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "queued", "analysis_run_ids": queued_run_ids},
    )


@router.post("/webhooks/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    body = await request.body()
    if not verify_github_webhook_signature(
        body, request.headers.get("X-Hub-Signature-256"), get_settings().GITHUB_WEBHOOK_SECRET
    ):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid webhook signature"}
        )

    event = request.headers.get("X-GitHub-Event")
    if event != "push":
        return _ignored(f"event '{event}' not handled")

    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return _ignored("malformed JSON body")
    if not isinstance(payload, dict):
        return _ignored("malformed push payload")

    return await run_in_threadpool(_handle_push, db, payload)
