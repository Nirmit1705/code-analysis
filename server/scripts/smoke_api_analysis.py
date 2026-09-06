"""Manual smoke test for Phase 5.4's REST analysis-run surface
(`app/api/v1/analysis.py`): `POST /repos/{id}/analysis-runs`,
`GET /analysis-runs/{id}`, `GET /repos/{id}/analysis-runs`.

Uses `fastapi.testclient.TestClient(app)` — a pattern already used
informally in this codebase (Phase 5.2's exchange-code-replay verification,
per `server/CLAUDE.md`), packaged into a committed script for the first
time here, following the same `_check`/`run_*()`/PASS-FAIL/exit-code
template every other `smoke_*.py` script uses.

Five gating groups, all offline (no live GitHub, no reachable Celery broker
— `github_service.get_branch`/`get_installation_token_for_user` and the
`run_repository_analysis.delay` method itself are all monkeypatched):

1. `run_happy_path()` — POST creates a `QUEUED` row, `.delay()` is called
   once with the matching id, the response matches `{id, status: "queued",
   ...}`; `GET`-by-id and the repo's `GET`-list both round-trip it.
2. `run_ownership_scoping()` — a second seeded user gets 404 on the `POST`
   and both `GET`s against the first user's repo/run (matches the
   404-not-403 convention every other endpoint in this codebase uses).
3. `run_no_selected_branch()` — a repo with zero `Branch` rows → `POST`
   returns 400.
4. `run_github_error()` — `get_branch` monkeypatched to raise a
   404-flavored `GitHubServiceError` → `POST` returns 404 (reusing
   `select_repo_branch`'s existing status-code mapping).
5. `run_list_ordering()` — two runs for one repo → the list `GET` returns
   them newest-first.

A sixth, non-gating group:

6. `run_live_bonus()` — real GitHub branch resolution and a real `.delay()`
   enqueue against the live `Nirmit1705/SubTrack` installation (reusing the
   precedent from `smoke_analysis_tasks.py`/`smoke_storage.py` — SubTrack is
   Phase 5's sample repo for live verification only). Does not wait for the
   run to complete — just confirms the `POST` still returns immediately
   with a real resolved commit sha and that both `GET`s round-trip it.
   Skips cleanly if the installation isn't available in this environment.

Run: `uv run python scripts/smoke_api_analysis.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.api.v1.analysis as analysis_module  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token, encrypt_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun  # noqa: E402
from app.models.branch import Branch  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import github_service  # noqa: E402

# Phase 5's sample repo for live verification only (matches
# smoke_analysis_tasks.py's/smoke_storage.py's own precedent) — not a claim
# about any later phase's representative repo.
LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "SubTrack"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- seeding helpers (same pattern as smoke_analysis_tasks.py) ------------------


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
            # A placeholder — every gating group below monkeypatches
            # get_installation_token_for_user, so this value is never
            # actually sent to GitHub.
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
    """Resets `repository`'s branches to exactly one, selected — the
    precondition `create_analysis_run` requires. Deletes-then-recreates
    rather than upserting, so reruns are idempotent regardless of what a
    previous run left behind."""
    db.execute(delete(Branch).where(Branch.repository_id == repository.id))
    db.commit()
    branch = Branch(
        repository_id=repository.id, name=name, latest_commit_sha="0" * 40, is_default=True, is_selected=True
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


class _Patch:
    """Tiny monkeypatch-and-restore helper (same as
    `smoke_analysis_tasks.py`'s own) — avoids repeating get/set/restore
    boilerplate for the handful of module-level attributes every group
    below patches."""

    def __init__(self, obj, attr: str, replacement):
        self._obj = obj
        self._attr = attr
        self._original = getattr(obj, attr)
        setattr(obj, attr, replacement)

    def restore(self) -> None:
        setattr(self._obj, self._attr, self._original)


def _offline_patches(recorded_delay_calls: list) -> list[_Patch]:
    """The three monkeypatches every gating group needs: no live GitHub call
    for the installation token or branch lookup, and no real Celery/Redis
    enqueue — `run_repository_analysis.delay` is patched to a no-op recorder
    so every gating group is fully offline, matching the plan's "no live
    GitHub, no reachable Celery broker" requirement."""
    return [
        _Patch(analysis_module, "get_installation_token_for_user", lambda current_user: "fake-token"),
        _Patch(
            github_service,
            "get_branch",
            lambda token, owner, repo, branch_name: {"commit": {"sha": "a" * 40}},
        ),
        _Patch(
            analysis_module.run_repository_analysis,
            "delay",
            lambda analysis_run_id: recorded_delay_calls.append(analysis_run_id),
        ),
    ]


# --- group 1: happy path ---------------------------------------------------------


def run_happy_path() -> bool:
    print("=== group 1: happy path — POST creates QUEUED + enqueues, GET/GET-list round-trip it ===")
    recorded_delay_calls: list[str] = []
    patches = _offline_patches(recorded_delay_calls)

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-analysis-happy@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-analysis-happy", owner="smoke-api", name="happy-path"
        )
        _clear_previous_runs(db, repository)
        _ensure_selected_branch(db, repository)

        token = create_access_token(subject=user.email)
        client = TestClient(app)

        resp = client.post(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
        all_passed = _check("POST returns 200", resp.status_code == 200, resp.text)
        if not all_passed:
            return False
        body = resp.json()
        all_passed &= _check("status == queued", body.get("status") == "queued", str(body))
        all_passed &= _check("commit_sha resolved from GitHub", body.get("commit_sha") == "a" * 40, str(body))
        all_passed &= _check(
            ".delay() called once with the matching id",
            recorded_delay_calls == [body.get("id")],
            str(recorded_delay_calls),
        )

        get_resp = client.get(f"/api/v1/analysis-runs/{body['id']}", headers=_auth(token))
        all_passed &= _check(
            "GET-by-id round-trips", get_resp.status_code == 200 and get_resp.json()["id"] == body["id"], get_resp.text
        )

        list_resp = client.get(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
        all_passed &= _check(
            "GET-list contains exactly this run",
            list_resp.status_code == 200 and [r["id"] for r in list_resp.json()] == [body["id"]],
            list_resp.text,
        )
        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 2: ownership scoping ---------------------------------------------------


def run_ownership_scoping() -> bool:
    print("\n=== group 2: ownership scoping — a second user gets 404 on POST/GET/GET-list ===")
    recorded_delay_calls: list[str] = []
    patches = _offline_patches(recorded_delay_calls)

    db = SessionLocal()
    try:
        owner_user = _get_or_create_user(db, "smoke-api-analysis-owner@local.test")
        other_user = _get_or_create_user(db, "smoke-api-analysis-other@local.test")
        repository = _get_or_create_repository(
            db, owner_user, github_repo_id="smoke-api-analysis-ownership", owner="smoke-api", name="ownership"
        )
        _clear_previous_runs(db, repository)
        _ensure_selected_branch(db, repository)

        owner_token = create_access_token(subject=owner_user.email)
        other_token = create_access_token(subject=other_user.email)
        client = TestClient(app)

        created = client.post(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(owner_token))
        all_passed = _check("owner can create a run", created.status_code == 200, created.text)
        run_id = created.json()["id"]

        post_other = client.post(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(other_token))
        all_passed &= _check("other user's POST -> 404", post_other.status_code == 404, post_other.text)

        get_other = client.get(f"/api/v1/analysis-runs/{run_id}", headers=_auth(other_token))
        all_passed &= _check("other user's GET-by-id -> 404", get_other.status_code == 404, get_other.text)

        list_other = client.get(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(other_token))
        all_passed &= _check("other user's GET-list -> 404", list_other.status_code == 404, list_other.text)

        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 3: no selected branch --------------------------------------------------


def run_no_selected_branch() -> bool:
    print("\n=== group 3: repository with no selected branch -> 400 ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-analysis-no-branch@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-analysis-no-branch", owner="smoke-api", name="no-branch"
        )
        _clear_previous_runs(db, repository)
        db.execute(delete(Branch).where(Branch.repository_id == repository.id))
        db.commit()

        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.post(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
        return _check("POST -> 400 (no branch selected)", resp.status_code == 400, resp.text)
    finally:
        db.close()


# --- group 4: GitHub error mapping -------------------------------------------------


def run_github_error() -> bool:
    print("\n=== group 4: GitHub get_branch raises 404 -> POST returns 404 ===")

    def _raising_get_branch(token, owner, repo, branch_name):
        raise github_service.GitHubServiceError(404, "branch not found")

    patches = [
        _Patch(analysis_module, "get_installation_token_for_user", lambda current_user: "fake-token"),
        _Patch(github_service, "get_branch", _raising_get_branch),
    ]
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-analysis-github-error@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-analysis-github-error", owner="smoke-api", name="github-error"
        )
        _clear_previous_runs(db, repository)
        _ensure_selected_branch(db, repository)

        token = create_access_token(subject=user.email)
        client = TestClient(app)
        resp = client.post(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
        return _check("POST -> 404 (GitHub 404 mapped through)", resp.status_code == 404, resp.text)
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 5: list ordering ---------------------------------------------------------


def run_list_ordering() -> bool:
    print("\n=== group 5: GET-list returns runs newest-first ===")
    recorded_delay_calls: list[str] = []
    patches = _offline_patches(recorded_delay_calls)

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-analysis-ordering@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-analysis-ordering", owner="smoke-api", name="ordering"
        )
        _clear_previous_runs(db, repository)
        _ensure_selected_branch(db, repository)

        token = create_access_token(subject=user.email)
        client = TestClient(app)

        first = client.post(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
        second = client.post(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
        all_passed = _check("both POSTs succeed", first.status_code == 200 and second.status_code == 200)
        id1, id2 = first.json()["id"], second.json()["id"]

        listed = client.get(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
        all_passed &= _check("GET-list -> 200", listed.status_code == 200, listed.text)
        ids_in_order = [r["id"] for r in listed.json()]
        all_passed &= _check("exactly 2 entries", len(ids_in_order) == 2, str(ids_in_order))
        all_passed &= _check("newest-first order", ids_in_order == [id2, id1], str(ids_in_order))
        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 6 (non-gating): live bonus -------------------------------------------------


def run_live_bonus() -> bool:
    print(f"\n=== group 6 (non-gating): live POST/GET round trip against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
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
            user = _get_or_create_user(db, "smoke-api-analysis-live@local.test")
            # The endpoint resolves its installation token from the
            # *current user's* encrypted credential (get_installation_token_for_user),
            # not from Repository.installation_id (that's only what the
            # Celery task itself uses, with no User context) — so this row
            # needs a real encrypted installation id, not the Repository's.
            user.github_access_token_encrypted = encrypt_token(str(installation["id"]))
            db.add(user)
            db.commit()

            repository = _get_or_create_repository(
                db, user,
                github_repo_id=f"smoke-api-analysis-live-{LIVE_TEST_OWNER}-{LIVE_TEST_REPO}".lower(),
                owner=LIVE_TEST_OWNER, name=LIVE_TEST_REPO,
            )
            repository.installation_id = str(installation["id"])
            db.add(repository)
            db.commit()

            _clear_previous_runs(db, repository)
            _ensure_selected_branch(db, repository, name="main")

            token = create_access_token(subject=user.email)
            client = TestClient(app)

            # Real GitHub call, real Celery .delay() enqueue — not mocked,
            # unlike every gating group above. Not waiting for the run to
            # actually complete; a worker may or may not be running.
            resp = client.post(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
            all_passed = _check("POST -> 200", resp.status_code == 200, resp.text)
            if not all_passed:
                return False
            body = resp.json()
            all_passed &= _check("status == queued", body.get("status") == "queued", str(body))
            all_passed &= _check(
                "commit_sha looks like a real sha (40 hex chars)",
                len(body.get("commit_sha", "")) == 40, str(body),
            )

            get_resp = client.get(f"/api/v1/analysis-runs/{body['id']}", headers=_auth(token))
            all_passed &= _check("GET-by-id round-trips", get_resp.status_code == 200, get_resp.text)

            list_resp = client.get(f"/api/v1/repos/{repository.id}/analysis-runs", headers=_auth(token))
            all_passed &= _check(
                "GET-list contains it", list_resp.status_code == 200 and len(list_resp.json()) >= 1, list_resp.text
            )

            print(f"  queued a real run: id={body['id']} commit_sha={body['commit_sha']}")
            return all_passed
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [ERROR] live bonus test failed: {exc!r}")
        return False


def main() -> int:
    r1 = run_happy_path()
    r2 = run_ownership_scoping()
    r3 = run_no_selected_branch()
    r4 = run_github_error()
    r5 = run_list_ordering()
    r6 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. Happy path:              {'PASS' if r1 else 'FAIL'}")
    print(f"2. Ownership scoping:       {'PASS' if r2 else 'FAIL'}")
    print(f"3. No selected branch:      {'PASS' if r3 else 'FAIL'}")
    print(f"4. GitHub error mapping:    {'PASS' if r4 else 'FAIL'}")
    print(f"5. List ordering:           {'PASS' if r5 else 'FAIL'}")
    print(f"6. Live bonus (non-gating): {'PASS' if r6 else 'FAIL (see above — may be a network/credentials issue)'}")
    # Matches smoke_analysis_tasks.py's/smoke_storage.py's own precedent:
    # the live network/credentials check doesn't gate the suite's exit code.
    return 0 if (r1 and r2 and r3 and r4 and r5) else 1


if __name__ == "__main__":
    sys.exit(main())
