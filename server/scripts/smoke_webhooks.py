"""Manual smoke test for Phase 9.5's GitHub webhook endpoint
(`app/api/v1/webhooks.py`: `POST /api/v1/webhooks/github`) and its two
supporting pieces (`security.verify_github_webhook_signature`,
`repo_service.get_repository_by_github_repo_id`).

Uses `fastapi.testclient.TestClient(app)` (same pattern as
`smoke_api_analysis.py`). Fully offline: `run_repository_analysis.delay` is
replaced with a recorder (no Celery/Redis) and `get_settings` (as seen by the
webhook module) with a fixed test secret, so this never depends on the real
`.env` secret.

Gating groups:

1. `run_signature_helper()` — the pure HMAC helper: valid, wrong secret,
   tampered body, missing/empty/malformed/non-ASCII header (must be `False`,
   never raise), empty secret (fails closed even for a "valid" HMAC over an
   empty key).
2. `run_valid_push()` — valid signature + push to the tracked branch -> 202,
   exactly one `WEBHOOK`-tagged `QUEUED` row (branch/commit from the payload),
   `.delay()` called once with that id, and the payload's *integer*
   `repository.id` resolved against the string `github_repo_id` column.
3. `run_bad_signatures()` — missing header, wrong secret, tampered body,
   wrong prefix, non-ASCII header -> 401 every time, zero rows created, zero
   enqueues (checked by DB count, not just the response).
4. `run_ignored_deliveries()` — every verified-but-irrelevant delivery is a
   200 `ignored` no-op with no row and no enqueue: push to an untracked
   branch, non-`push` event (`ping`, `issues`), tag push, branch deletion,
   an unconnected repository, malformed JSON, non-object JSON, a payload
   missing required fields, a non-hex `after`.
5. `run_enqueue_failure()` — `.delay()` raising (broker down) -> 503, and the
   just-created row is marked `FAILED` rather than left stuck at `QUEUED`.
6. `run_manual_trigger_source()` — the pre-existing manual endpoint's call
   call site now passes `trigger_source=MANUAL` explicitly (row check).
7. `run_duplicate_deliveries()` — Known Issue #28: an identical redelivery is a
   `200 ignored` (also once the run is `COMPLETED`), a new commit is not a
   duplicate, and a `FAILED` run is retried rather than blocking.
8. `run_concurrent_duplicates()` — 8 simultaneous deliveries of one commit
   queue exactly one run (the advisory lock, not just the existence check).
9. `run_multi_user_fanout()` — Known Issue #28: one GitHub repo connected by
   several users -> the push queues a run for every copy that tracks the
   pushed branch, and none for one that tracks a different branch.

Run: `uv run python scripts/smoke_webhooks.py` (from `server/`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.api.v1.webhooks as webhooks_module  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token, verify_github_webhook_signature  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus, AnalysisRunTriggerSource  # noqa: E402
from app.models.branch import Branch  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.services import repo_service  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

TEST_SECRET = "smoke-webhook-test-secret"
GITHUB_REPO_ID = 987654321  # GitHub sends this as a JSON *integer*
TRACKED_BRANCH = "main"
COMMIT_A = "a1" * 20
COMMIT_B = "b2" * 20
URL = "/api/v1/webhooks/github"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _sign(body: bytes, secret: str = TEST_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _push_payload(*, ref: str = f"refs/heads/{TRACKED_BRANCH}", after: str = COMMIT_A, repo_id=GITHUB_REPO_ID, **extra) -> dict:
    return {"ref": ref, "after": after, "before": "0" * 40, "repository": {"id": repo_id}, **extra}


def _post(client: TestClient, body: bytes, *, event: str = "push", signature: str | None = "auto"):
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if signature == "auto":
        headers["X-Hub-Signature-256"] = _sign(body)
    elif signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post(URL, content=body, headers=headers)


def _post_json(client: TestClient, payload: dict, **kwargs):
    return _post(client, json.dumps(payload).encode(), **kwargs)


class _Env:
    """Seeds the connected repo + selected branch, patches the webhook
    module's settings and Celery enqueue; `close()` restores everything."""

    def __init__(self, db: Session):
        self.db = db
        self.delay_calls: list[str] = []
        user = _get_or_create_user(db, "smoke-webhooks@local.test")
        self.user = user
        # Drop any *other* users' copies of this GitHub repo left behind by an
        # aborted earlier run of group 9 — the webhook fans out to every copy,
        # so a stale one would make unrelated groups queue extra runs.
        db.execute(
            delete(Repository).where(
                Repository.github_repo_id == str(GITHUB_REPO_ID), Repository.connected_user_id != user.id
            )
        )
        db.commit()
        self.repository = _get_or_create_repository(
            db, user, github_repo_id=str(GITHUB_REPO_ID), owner="smoke-webhooks", name="repo"
        )
        _clear_previous_runs(db, self.repository)
        db.execute(delete(Branch).where(Branch.repository_id == self.repository.id))
        db.add(Branch(repository_id=self.repository.id, name=TRACKED_BRANCH, latest_commit_sha="0" * 40, is_default=True, is_selected=True))
        db.add(Branch(repository_id=self.repository.id, name="feature/other", latest_commit_sha="0" * 40, is_default=False, is_selected=False))
        db.commit()

        self._orig_get_settings = webhooks_module.get_settings
        webhooks_module.get_settings = lambda: SimpleNamespace(GITHUB_WEBHOOK_SECRET=TEST_SECRET)
        self._orig_delay = webhooks_module.run_repository_analysis.delay
        webhooks_module.run_repository_analysis.delay = lambda run_id: self.delay_calls.append(run_id)

    def run_count(self) -> int:
        self.db.expire_all()
        return self.db.execute(
            select(func.count()).select_from(AnalysisRun).where(AnalysisRun.repository_id == self.repository.id)
        ).scalar_one()

    def close(self) -> None:
        webhooks_module.get_settings = self._orig_get_settings
        webhooks_module.run_repository_analysis.delay = self._orig_delay
        _clear_previous_runs(self.db, self.repository)


# --- group 1 -------------------------------------------------------------------------


def run_signature_helper() -> bool:
    print("=== group 1: verify_github_webhook_signature ===")
    body = b'{"hello":"world"}'
    good = _sign(body)
    ok = _check("valid signature -> True", verify_github_webhook_signature(body, good, TEST_SECRET) is True)
    ok &= _check("wrong secret -> False", verify_github_webhook_signature(body, _sign(body, "other"), TEST_SECRET) is False)
    ok &= _check("tampered body -> False", verify_github_webhook_signature(body + b" ", good, TEST_SECRET) is False)
    ok &= _check("missing header (None) -> False", verify_github_webhook_signature(body, None, TEST_SECRET) is False)
    ok &= _check("empty header -> False", verify_github_webhook_signature(body, "", TEST_SECRET) is False)
    ok &= _check("no 'sha256=' prefix -> False", verify_github_webhook_signature(body, good[len("sha256="):], TEST_SECRET) is False)
    try:
        non_ascii = verify_github_webhook_signature(body, "sha256=ünïcödé", TEST_SECRET)
        ok &= _check("non-ASCII header -> False, no TypeError", non_ascii is False)
    except Exception as exc:  # noqa: BLE001
        ok &= _check("non-ASCII header -> False, no TypeError", False, repr(exc))
    empty_key_sig = "sha256=" + hmac.new(b"", body, hashlib.sha256).hexdigest()
    ok &= _check(
        "empty secret fails closed even for a 'valid' HMAC over an empty key",
        verify_github_webhook_signature(body, empty_key_sig, "") is False,
    )
    return ok


# --- group 2 -------------------------------------------------------------------------


def run_valid_push() -> bool:
    print("=== group 2: valid signature + push to the tracked branch -> 202 + WEBHOOK/QUEUED row ===")
    db = SessionLocal()
    env = _Env(db)
    try:
        client = TestClient(app)
        resp = _post_json(client, _push_payload())
        ok = _check("202", resp.status_code == 202, resp.text)
        if not ok:
            return False
        body = resp.json()
        ok &= _check("response status == queued", body.get("status") == "queued", str(body))

        db.expire_all()
        runs = db.execute(select(AnalysisRun).where(AnalysisRun.repository_id == env.repository.id)).scalars().all()
        ok &= _check("exactly one row created", len(runs) == 1, str(len(runs)))
        run = runs[0]
        ok &= _check("trigger_source == WEBHOOK", run.trigger_source is AnalysisRunTriggerSource.WEBHOOK, str(run.trigger_source))
        ok &= _check("status == QUEUED", run.status is AnalysisRunStatus.QUEUED, str(run.status))
        ok &= _check("branch from payload ref (prefix stripped)", run.branch == TRACKED_BRANCH, run.branch)
        ok &= _check("commit_sha == payload.after", run.commit_sha == COMMIT_A, run.commit_sha)
        ok &= _check("response ids match the row", body.get("analysis_run_ids") == [str(run.id)], str(body))
        ok &= _check(".delay() called once with that id", env.delay_calls == [str(run.id)], str(env.delay_calls))

        # A second push (new commit) queues a second, distinct run.
        resp2 = _post_json(client, _push_payload(after=COMMIT_B))
        ok &= _check("second push -> 202", resp2.status_code == 202, resp2.text)
        ok &= _check("second push -> second row", env.run_count() == 2, str(env.run_count()))

        # repo_service lookup resolves the string id; unknown id -> [].
        ok &= _check(
            "list_repositories_by_github_repo_id resolves",
            [r.id for r in repo_service.list_repositories_by_github_repo_id(db, str(GITHUB_REPO_ID))]
            == [env.repository.id],
        )
        ok &= _check(
            "list_repositories_by_github_repo_id unknown -> []",
            repo_service.list_repositories_by_github_repo_id(db, "no-such") == [],
        )
        return ok
    finally:
        env.close()
        db.close()


# --- group 3 -------------------------------------------------------------------------


def run_bad_signatures() -> bool:
    print("=== group 3: bad/missing signature -> 401, nothing touched ===")
    db = SessionLocal()
    env = _Env(db)
    try:
        client = TestClient(app)
        body = json.dumps(_push_payload()).encode()
        good = _sign(body)
        cases = {
            "missing header": dict(body=body, signature=None),
            "wrong secret": dict(body=body, signature=_sign(body, "not-the-secret")),
            "tampered body": dict(body=body + b" ", signature=good),
            "no sha256= prefix": dict(body=body, signature=good[len("sha256="):]),
            "empty header": dict(body=body, signature=""),
            # Raw latin-1 bytes, not a `str`: httpx refuses to encode a
            # non-ASCII `str` header client-side, but a real HTTP server
            # (h11/uvicorn) hands the app a latin-1-decoded `str` — this is
            # what actually reaches the endpoint from an attacker.
            "non-ASCII header": dict(body=body, signature="sha256=ünïcödé".encode("latin-1")),
        }
        ok = True
        for label, kwargs in cases.items():
            try:
                resp = _post(client, kwargs["body"], signature=kwargs["signature"])
                ok &= _check(f"{label} -> 401", resp.status_code == 401, f"{resp.status_code} {resp.text}")
            except Exception as exc:  # noqa: BLE001 — a server-side crash on attacker input is a failure too
                ok &= _check(f"{label} -> 401", False, repr(exc))
        # An unsigned *non-push* event must be rejected before it is even classified.
        resp = _post(client, body, event="ping", signature=None)
        ok &= _check("unsigned non-push event -> 401 (not 200 ignored)", resp.status_code == 401, str(resp.status_code))
        ok &= _check("zero rows created", env.run_count() == 0, str(env.run_count()))
        ok &= _check("zero enqueues", env.delay_calls == [], str(env.delay_calls))
        return ok
    finally:
        env.close()
        db.close()


# --- group 4 -------------------------------------------------------------------------


def run_ignored_deliveries() -> bool:
    print("=== group 4: verified-but-irrelevant deliveries -> 200 ignored, no row, no enqueue ===")
    db = SessionLocal()
    env = _Env(db)
    try:
        client = TestClient(app)
        ok = True

        def expect_ignored(label: str, resp) -> None:
            nonlocal ok
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            ok &= _check(
                f"{label} -> 200 ignored",
                resp.status_code == 200 and body.get("status") == "ignored",
                f"{resp.status_code} {resp.text}",
            )

        expect_ignored("push to untracked branch", _post_json(client, _push_payload(ref="refs/heads/feature/other")))
        expect_ignored("push to a branch with no Branch row", _post_json(client, _push_payload(ref="refs/heads/nope")))
        expect_ignored("event=ping", _post_json(client, {"zen": "Keep it logically awesome."}, event="ping"))
        expect_ignored("event=issues", _post_json(client, _push_payload(), event="issues"))
        expect_ignored("tag push", _post_json(client, _push_payload(ref="refs/tags/v1.0.0")))
        expect_ignored("branch deletion (deleted=true)", _post_json(client, _push_payload(deleted=True)))
        expect_ignored("branch deletion (after all zeros)", _post_json(client, _push_payload(after="0" * 40)))
        expect_ignored("unconnected repository", _post_json(client, _push_payload(repo_id=111222333)))
        expect_ignored("malformed JSON body", _post(client, b"{not json"))
        expect_ignored("non-object JSON body", _post(client, b"[1, 2, 3]"))
        expect_ignored("payload missing required fields", _post_json(client, {"ref": "refs/heads/main"}))
        expect_ignored("non-hex `after`", _post_json(client, _push_payload(after="z" * 40)))
        expect_ignored("boolean repository.id", _post_json(client, _push_payload(repo_id=True)))

        ok &= _check("zero rows created", env.run_count() == 0, str(env.run_count()))
        ok &= _check("zero enqueues", env.delay_calls == [], str(env.delay_calls))
        return ok
    finally:
        env.close()
        db.close()


# --- group 5 -------------------------------------------------------------------------


def run_enqueue_failure() -> bool:
    print("=== group 5: broker down (.delay raises) -> 503 and the row is FAILED, not stuck QUEUED ===")
    db = SessionLocal()
    env = _Env(db)
    try:
        def boom(run_id: str) -> None:
            raise ConnectionError("redis down")

        webhooks_module.run_repository_analysis.delay = boom
        client = TestClient(app)
        resp = _post_json(client, _push_payload())
        ok = _check("503", resp.status_code == 503, f"{resp.status_code} {resp.text}")
        db.expire_all()
        runs = db.execute(select(AnalysisRun).where(AnalysisRun.repository_id == env.repository.id)).scalars().all()
        ok &= _check("one row exists", len(runs) == 1, str(len(runs)))
        if runs:
            ok &= _check("row is FAILED with an error_message", runs[0].status is AnalysisRunStatus.FAILED and bool(runs[0].error_message), str(runs[0].status))
            ok &= _check("row is still WEBHOOK-tagged", runs[0].trigger_source is AnalysisRunTriggerSource.WEBHOOK)
        return ok
    finally:
        env.close()
        db.close()


# --- group 7 (Known Issue #28: duplicate deliveries) ---------------------------------


def run_duplicate_deliveries() -> bool:
    print("=== group 7: redelivered push is idempotent; a FAILED run is retried ===")
    db = SessionLocal()
    env = _Env(db)
    try:
        client = TestClient(app)
        ok = _check("first delivery -> 202", _post_json(client, _push_payload()).status_code == 202)
        resp = _post_json(client, _push_payload())
        body = resp.json()
        ok &= _check(
            "identical redelivery -> 200 ignored (already analyzed or queued)",
            resp.status_code == 200 and body.get("status") == "ignored" and "already" in body.get("reason", ""),
            f"{resp.status_code} {resp.text}",
        )
        ok &= _check("still exactly one row, one enqueue", env.run_count() == 1 and len(env.delay_calls) == 1)

        # A different commit on the same branch is not a duplicate.
        ok &= _check("new commit -> 202", _post_json(client, _push_payload(after=COMMIT_B)).status_code == 202)
        ok &= _check("now two rows", env.run_count() == 2, str(env.run_count()))

        # Duplicate detection also holds once the run has progressed/completed.
        db.expire_all()
        run_a = db.execute(
            select(AnalysisRun).where(AnalysisRun.commit_sha == COMMIT_A, AnalysisRun.repository_id == env.repository.id)
        ).scalar_one()
        run_a.status = AnalysisRunStatus.COMPLETED
        db.add(run_a)
        db.commit()
        resp = _post_json(client, _push_payload())
        ok &= _check("redelivery after COMPLETED -> still ignored", resp.status_code == 200 and env.run_count() == 2)

        # A FAILED run does not block: the same commit is retried.
        run_a.status = AnalysisRunStatus.FAILED
        db.add(run_a)
        db.commit()
        resp = _post_json(client, _push_payload())
        ok &= _check("redelivery after FAILED -> 202 (retried)", resp.status_code == 202, f"{resp.status_code} {resp.text}")
        ok &= _check("a fresh row was queued", env.run_count() == 3, str(env.run_count()))
        return ok
    finally:
        env.close()
        db.close()


def run_concurrent_duplicates() -> bool:
    print("=== group 8: simultaneous deliveries of the same commit queue exactly one run ===")
    import threading  # noqa: PLC0415

    db = SessionLocal()
    env = _Env(db)
    try:
        n = 8
        barrier = threading.Barrier(n)
        statuses: list[int] = []
        errors: list[str] = []
        body = json.dumps(_push_payload()).encode()

        def worker() -> None:
            try:
                client = TestClient(app)
                barrier.wait()
                statuses.append(_post(client, body).status_code)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok = _check("no request crashed", not errors, str(errors))
        ok &= _check("exactly one 202", statuses.count(202) == 1, str(sorted(statuses)))
        ok &= _check("the rest are 200 ignored", statuses.count(200) == n - 1, str(sorted(statuses)))
        ok &= _check("exactly one row", env.run_count() == 1, str(env.run_count()))
        ok &= _check("exactly one enqueue", len(env.delay_calls) == 1, str(env.delay_calls))
        return ok
    finally:
        env.close()
        db.close()


# --- group 9 (Known Issue #28: same GitHub repo connected by several users) ----------


def run_multi_user_fanout() -> bool:
    print("=== group 9: one GitHub repo connected by several users -> a push fans out to each ===")
    db = SessionLocal()
    env = _Env(db)
    extra: list[Repository] = []
    try:
        for suffix, selected in (("b", TRACKED_BRANCH), ("c", "feature/other")):
            user = _get_or_create_user(db, f"smoke-webhooks-{suffix}@local.test")
            repo = _get_or_create_repository(
                db, user, github_repo_id=str(GITHUB_REPO_ID), owner="smoke-webhooks", name=f"repo-{suffix}"
            )
            _clear_previous_runs(db, repo)
            db.execute(delete(Branch).where(Branch.repository_id == repo.id))
            db.add(Branch(repository_id=repo.id, name=selected, latest_commit_sha="0" * 40, is_default=True, is_selected=True))
            db.commit()
            extra.append(repo)

        client = TestClient(app)
        resp = _post_json(client, _push_payload())
        body = resp.json()
        ok = _check("202", resp.status_code == 202, f"{resp.status_code} {resp.text}")
        ok &= _check(
            "two runs queued (users A and B track `main`; C tracks another branch)",
            len(body.get("analysis_run_ids", [])) == 2,
            str(body),
        )

        def count(repo: Repository) -> int:
            db.expire_all()
            return db.execute(
                select(func.count()).select_from(AnalysisRun).where(AnalysisRun.repository_id == repo.id)
            ).scalar_one()

        ok &= _check("user A got a run", env.run_count() == 1)
        ok &= _check("user B got a run", count(extra[0]) == 1)
        ok &= _check("user C (different tracked branch) got none", count(extra[1]) == 0)
        ok &= _check("two enqueues", len(env.delay_calls) == 2, str(env.delay_calls))

        # Redelivery: every copy is deduped independently.
        resp = _post_json(client, _push_payload())
        ok &= _check(
            "redelivery -> 200 ignored", resp.status_code == 200 and resp.json().get("status") == "ignored", resp.text
        )
        ok &= _check("no extra runs", env.run_count() == 1 and count(extra[0]) == 1)
        return ok
    finally:
        # Delete the extra users' repositories outright (not just their runs):
        # they share GITHUB_REPO_ID, so leaving them would leak fan-out
        # targets into every other group and every later run.
        for repo in extra:
            db.execute(delete(Repository).where(Repository.id == repo.id))
        db.commit()
        env.close()
        db.close()


# --- group 6 -------------------------------------------------------------------------


def run_manual_trigger_source() -> bool:
    print("=== group 6: existing manual endpoint stores trigger_source=MANUAL ===")
    import app.api.v1.analysis as analysis_module  # noqa: PLC0415
    from app.services import github_service  # noqa: PLC0415

    db = SessionLocal()
    env = _Env(db)
    orig_token = analysis_module.get_installation_token_for_user
    orig_branch = github_service.get_branch
    orig_delay = analysis_module.run_repository_analysis.delay
    try:
        analysis_module.get_installation_token_for_user = lambda current_user: "fake-token"
        github_service.get_branch = lambda token, owner, repo, branch_name: {"commit": {"sha": COMMIT_A}}
        analysis_module.run_repository_analysis.delay = lambda run_id: None
        client = TestClient(app)
        token = create_access_token(subject=env.user.email)
        resp = client.post(f"/api/v1/repos/{env.repository.id}/analysis-runs", headers={"Authorization": f"Bearer {token}"})
        ok = _check("POST -> 200", resp.status_code == 200, resp.text)
        db.expire_all()
        runs = db.execute(select(AnalysisRun).where(AnalysisRun.repository_id == env.repository.id)).scalars().all()
        ok &= _check("row stored with trigger_source == MANUAL", len(runs) == 1 and runs[0].trigger_source is AnalysisRunTriggerSource.MANUAL, str([r.trigger_source for r in runs]))
        return ok
    finally:
        analysis_module.get_installation_token_for_user = orig_token
        github_service.get_branch = orig_branch
        analysis_module.run_repository_analysis.delay = orig_delay
        env.close()
        db.close()


def main() -> int:
    results = {
        "signature helper": run_signature_helper(),
        "valid push": run_valid_push(),
        "bad signatures": run_bad_signatures(),
        "ignored deliveries": run_ignored_deliveries(),
        "enqueue failure": run_enqueue_failure(),
        "manual trigger_source": run_manual_trigger_source(),
        "duplicate deliveries": run_duplicate_deliveries(),
        "concurrent duplicates": run_concurrent_duplicates(),
        "multi-user fan-out": run_multi_user_fanout(),
    }
    print("\n=== summary ===")
    for name, passed in results.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
