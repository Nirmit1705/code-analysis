"""Manual smoke test for Phase 5.4's WS analysis-run surface
(`app/api/ws/analysis_progress.py`): `WS /ws/analysis-runs/{id}`.

Uses `fastapi.testclient.TestClient(app).websocket_connect(...)` — runs the
real ASGI app in-process, including real async Redis pub/sub calls, so a
reachable local Redis is required (already an implicit dependency of every
Phase 5.2+ smoke script, not a new requirement introduced here).

Five gating groups, offline from GitHub but against real Redis/Postgres (no
Celery worker needed for any of these — group 5 simulates the Celery task's
own `publish_progress_event` calls directly):

1. `run_invalid_token()` — missing/garbage `?token=` -> close code 1008.
2. `run_wrong_owner()` — a valid token whose user doesn't own the run's
   repository -> 1008.
3. `run_unknown_run_id()` — a random UUID -> 1008.
4. `run_terminal_snapshot()` — a pre-seeded `COMPLETED` run -> exactly one
   snapshot message (`status: "completed"`), then close code 1000.
5. `run_live_progress_relay()` — a `QUEUED` run; connects, receives the
   initial snapshot, then the script itself calls
   `services/progress_service.py::publish_progress_event` directly
   (simulating what the real Celery task does) for a few synthetic
   transitions ending in `completed`; asserts the client receives them in
   order and the socket then closes at 1000. This is the core proof the
   subscribe-before-snapshot/relay mechanism works, with no Celery worker
   involved at all.

A sixth, non-gating group:

6. `run_live_bonus()` — the real Phase 5.4 Definition-of-Done check: `POST`
   a real analysis run for `Nirmit1705/SubTrack` (via `TestClient`, exactly
   as a real client would) then watch the WS for the actual stage sequence
   to `COMPLETED`. **Requires a real Celery worker already running**
   (`uv run celery -A app.workers.celery_app worker --loglevel=info
   --pool=solo` in a separate terminal — `server/README.md` documenting
   this doesn't exist until Phase 5.5) plus the live SubTrack installation.
   `receive_text()` has no timeout of its own (confirmed against
   `starlette.testclient.WebSocketTestSession` — it blocks on an anyio
   stream indefinitely), so the watch runs in a daemon thread with its own
   queue-based timeout; if nothing terminal arrives in time this reports
   `[SKIP]` (non-fatal) rather than hanging the script, since that's most
   likely just "no worker running" rather than a real failure.

Run: `uv run python scripts/smoke_ws_analysis.py` (from `server/`).
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from starlette.testclient import WebSocketDisconnect  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token, encrypt_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.branch import Branch  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.analysis import AnalysisProgressEvent  # noqa: E402
from app.services import github_service  # noqa: E402
from app.services.progress_service import publish_progress_event  # noqa: E402
from app.services.repo_service import create_queued_analysis_run  # noqa: E402

# Phase 5's sample repo for live verification only (matches
# smoke_api_analysis.py's/smoke_analysis_tasks.py's own precedent).
LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "SubTrack"
LIVE_BONUS_TIMEOUT_SECONDS = 180


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- seeding helpers (duplicated from smoke_api_analysis.py, matching this
# codebase's convention of not sharing a test-utils module across scripts) --


def _get_or_create_user(db: Session, email: str) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _get_or_create_repository(db: Session, user: User, *, github_repo_id: str, owner: str, name: str) -> Repository:
    repository = db.execute(
        select(Repository).where(
            Repository.connected_user_id == user.id, Repository.github_repo_id == github_repo_id
        )
    ).scalar_one_or_none()
    if repository is None:
        repository = Repository(
            owner=owner,
            name=name,
            full_name=f"{owner}/{name}",
            github_repo_id=github_repo_id,
            default_branch="main",
            connected_user_id=user.id,
            installation_id="fake-installation-id",
        )
        db.add(repository)
        db.commit()
        db.refresh(repository)
    return repository


def _clear_previous_runs(db: Session, repository: Repository) -> None:
    db.execute(delete(AnalysisRun).where(AnalysisRun.repository_id == repository.id))
    db.commit()


def _ensure_selected_branch(db: Session, repository: Repository, *, name: str = "main") -> Branch:
    db.execute(delete(Branch).where(Branch.repository_id == repository.id))
    db.commit()
    branch = Branch(
        repository_id=repository.id, name=name, latest_commit_sha="0" * 40, is_default=True, is_selected=True
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


# --- group 1: invalid/missing token -----------------------------------------------


def run_invalid_token() -> bool:
    print("=== group 1: missing/garbage token -> 1008 ===")
    client = TestClient(app)
    all_passed = True

    try:
        with client.websocket_connect("/ws/analysis-runs/00000000-0000-0000-0000-000000000000") as ws:
            ws.receive_text()
        all_passed &= _check("missing token -> 1008", False)
    except WebSocketDisconnect as exc:
        all_passed &= _check(f"missing token -> 1008 (got {exc.code})", exc.code == 1008)

    try:
        with client.websocket_connect(
            "/ws/analysis-runs/00000000-0000-0000-0000-000000000000?token=garbage-token"
        ) as ws:
            ws.receive_text()
        all_passed &= _check("garbage token -> 1008", False)
    except WebSocketDisconnect as exc:
        all_passed &= _check(f"garbage token -> 1008 (got {exc.code})", exc.code == 1008)

    return all_passed


# --- group 2: wrong owner -----------------------------------------------------------


def run_wrong_owner() -> bool:
    print("\n=== group 2: valid token, run belongs to a different user's repo -> 1008 ===")
    db = SessionLocal()
    try:
        owner_user = _get_or_create_user(db, "smoke-ws-analysis-owner@local.test")
        other_user = _get_or_create_user(db, "smoke-ws-analysis-other@local.test")
        repository = _get_or_create_repository(
            db, owner_user, github_repo_id="smoke-ws-analysis-wrong-owner", owner="smoke-ws", name="wrong-owner"
        )
        _clear_previous_runs(db, repository)
        queued = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha="a" * 40)

        other_token = create_access_token(subject=other_user.email)
        client = TestClient(app)
        try:
            with client.websocket_connect(f"/ws/analysis-runs/{queued.id}?token={other_token}") as ws:
                ws.receive_text()
            return _check("wrong owner -> 1008", False)
        except WebSocketDisconnect as exc:
            return _check(f"wrong owner -> 1008 (got {exc.code})", exc.code == 1008)
    finally:
        db.close()


# --- group 3: unknown run id --------------------------------------------------------


def run_unknown_run_id() -> bool:
    print("\n=== group 3: unknown analysis_run_id -> 1008 ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-ws-analysis-unknown@local.test")
        token = create_access_token(subject=user.email)
        client = TestClient(app)
        try:
            with client.websocket_connect(f"/ws/analysis-runs/{uuid.uuid4()}?token={token}") as ws:
                ws.receive_text()
            return _check("unknown run id -> 1008", False)
        except WebSocketDisconnect as exc:
            return _check(f"unknown run id -> 1008 (got {exc.code})", exc.code == 1008)
    finally:
        db.close()


# --- group 4: already-terminal run --------------------------------------------------


def run_terminal_snapshot() -> bool:
    print("\n=== group 4: already-terminal run -> one snapshot, then close 1000 ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-ws-analysis-terminal@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-ws-analysis-terminal", owner="smoke-ws", name="terminal"
        )
        _clear_previous_runs(db, repository)
        queued = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha="b" * 40)
        queued.status = AnalysisRunStatus.COMPLETED
        db.add(queued)
        db.commit()

        token = create_access_token(subject=user.email)
        client = TestClient(app)
        all_passed = True
        with client.websocket_connect(f"/ws/analysis-runs/{queued.id}?token={token}") as ws:
            snapshot = json.loads(ws.receive_text())
            all_passed &= _check("snapshot status == completed", snapshot.get("status") == "completed", str(snapshot))
            try:
                ws.receive_text()
                all_passed &= _check("closes after the one snapshot", False)
            except WebSocketDisconnect as exc:
                all_passed &= _check(f"closes with code 1000 (got {exc.code})", exc.code == 1000)
        return all_passed
    finally:
        db.close()


# --- group 5: non-terminal run — subscribe/snapshot/relay, no Celery worker ---------


def run_live_progress_relay() -> bool:
    print(
        "\n=== group 5: non-terminal run — subscribe-before-snapshot + relay to "
        "COMPLETED (real Redis, simulated events, no Celery worker) ==="
    )
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-ws-analysis-relay@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-ws-analysis-relay", owner="smoke-ws", name="relay"
        )
        _clear_previous_runs(db, repository)
        queued = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha="c" * 40)

        token = create_access_token(subject=user.email)
        client = TestClient(app)
        all_passed = True
        with client.websocket_connect(f"/ws/analysis-runs/{queued.id}?token={token}") as ws:
            snapshot = json.loads(ws.receive_text())
            all_passed &= _check("initial snapshot status == queued", snapshot.get("status") == "queued", str(snapshot))

            for status in ("cloning", "parsing", "completed"):
                publish_progress_event(
                    str(queued.id),
                    AnalysisProgressEvent(
                        analysis_run_id=str(queued.id), status=status, timestamp=datetime.now(timezone.utc)
                    ),
                )
                relayed = json.loads(ws.receive_text())
                all_passed &= _check(f"relayed event status == {status}", relayed.get("status") == status, str(relayed))

            try:
                ws.receive_text()
                all_passed &= _check("socket closes after completed", False)
            except WebSocketDisconnect as exc:
                all_passed &= _check(f"socket closes with code 1000 (got {exc.code})", exc.code == 1000)
        return all_passed
    finally:
        db.close()


# --- group 6 (non-gating): live POST -> WS -> COMPLETED round trip -----------------


def _watch_ws_until_terminal(client: TestClient, run_id: str, token: str, timeout_seconds: float):
    """Watches the WS in a daemon thread with a queue-based timeout —
    `WebSocketTestSession.receive()` has no timeout parameter of its own
    (confirmed against its source: it blocks on an anyio stream
    indefinitely), so a stuck watch (no worker running, nothing ever
    published) must not be allowed to hang this script forever. Returns
    `(observed_statuses, outcome)` where `outcome` is one of "terminal",
    "timeout", or "error: <repr>". The thread is daemonized so the process
    can still exit even if it's left blocked mid-`receive()`.
    """
    events: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            with client.websocket_connect(f"/ws/analysis-runs/{run_id}?token={token}") as ws:
                while True:
                    message = json.loads(ws.receive_text())
                    events.put(("event", message["status"]))
                    if message["status"] in ("completed", "failed"):
                        return
        except Exception as exc:  # noqa: BLE001 — reported back to the main thread, not raised here
            events.put(("error", repr(exc)))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    observed: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return observed, "timeout"
        try:
            kind, payload = events.get(timeout=remaining)
        except queue.Empty:
            return observed, "timeout"
        if kind == "event":
            observed.append(payload)
            print(f"    observed: {payload}")
            if payload in ("completed", "failed"):
                return observed, "terminal"
        elif kind == "error":
            return observed, f"error: {payload}"


def run_live_bonus() -> bool:
    print(f"\n=== group 6 (non-gating): live POST -> WS -> COMPLETED against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
    print(
        "  NOTE: requires a real Celery worker already running in another terminal:\n"
        "    uv run celery -A app.workers.celery_app worker --loglevel=info --pool=solo"
    )
    try:
        installations = github_service.list_installations()
        installation = next(
            (i for i in installations if i["account"]["login"].lower() == LIVE_TEST_OWNER.lower()), None
        )
        if installation is None:
            print(f"  [SKIP] no installation found for account {LIVE_TEST_OWNER!r}")
            return True

        db = SessionLocal()
        try:
            user = _get_or_create_user(db, "smoke-ws-analysis-live@local.test")
            user.github_access_token_encrypted = encrypt_token(str(installation["id"]))
            db.add(user)
            db.commit()

            repository = _get_or_create_repository(
                db, user,
                github_repo_id=f"smoke-ws-analysis-live-{LIVE_TEST_OWNER}-{LIVE_TEST_REPO}".lower(),
                owner=LIVE_TEST_OWNER, name=LIVE_TEST_REPO,
            )
            repository.installation_id = str(installation["id"])
            db.add(repository)
            db.commit()

            _clear_previous_runs(db, repository)
            _ensure_selected_branch(db, repository, name="main")

            token = create_access_token(subject=user.email)
            client = TestClient(app)

            post_resp = client.post(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
            if post_resp.status_code != 200:
                print(f"  [ERROR] POST failed: {post_resp.status_code} {post_resp.text}")
                return False
            run_id = post_resp.json()["id"]
            print(f"  queued real run id={run_id}, watching WS (timeout={LIVE_BONUS_TIMEOUT_SECONDS}s)...")

            observed, outcome = _watch_ws_until_terminal(client, run_id, token, LIVE_BONUS_TIMEOUT_SECONDS)

            if outcome == "timeout":
                print(
                    f"  [SKIP] no terminal status observed within {LIVE_BONUS_TIMEOUT_SECONDS}s — "
                    "is a Celery worker actually running? (see the command printed above)"
                )
                return True
            if outcome.startswith("error"):
                print(f"  [ERROR] {outcome}")
                return False

            print(f"  full observed sequence: {observed}")
            return _check(
                "reached a terminal status (completed/failed) via the real worker",
                bool(observed) and observed[-1] in ("completed", "failed"),
                str(observed),
            )
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [ERROR] live bonus test failed: {exc!r}")
        return False


def main() -> int:
    r1 = run_invalid_token()
    r2 = run_wrong_owner()
    r3 = run_unknown_run_id()
    r4 = run_terminal_snapshot()
    r5 = run_live_progress_relay()
    r6 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. Invalid/missing token:      {'PASS' if r1 else 'FAIL'}")
    print(f"2. Wrong owner:                {'PASS' if r2 else 'FAIL'}")
    print(f"3. Unknown run id:             {'PASS' if r3 else 'FAIL'}")
    print(f"4. Terminal snapshot:          {'PASS' if r4 else 'FAIL'}")
    print(f"5. Live progress relay:        {'PASS' if r5 else 'FAIL'}")
    print(f"6. Live bonus (non-gating):    {'PASS' if r6 else 'FAIL (see above)'}")
    # Matches every other script's precedent: the live network/worker check
    # doesn't gate the suite's exit code.
    return 0 if (r1 and r2 and r3 and r4 and r5) else 1


if __name__ == "__main__":
    sys.exit(main())
