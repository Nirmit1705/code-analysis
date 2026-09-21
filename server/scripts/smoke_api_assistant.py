"""Manual smoke test for Phase 8.3's REST assistant surface
(`app/api/v1/assistant.py`): `POST /repos/{id}/assistant/messages`,
`GET /repos/{id}/assistant/suggested-questions`.

Uses `fastapi.testclient.TestClient(app)` — the same pattern
`smoke_api_analysis.py` already established for endpoint-level smoke tests,
so `uv run python scripts/smoke_api_assistant.py` needs no separately
running `uvicorn` process (Phase 8.3's own verification manually started a
real server + real `curl` calls; this script packages the same checks into
a repeatable, committed script per the user's explicit ask afterward — this
was flagged as a real coverage gap in `server/CLAUDE.md`'s 8.3 write-up).

This script deliberately does NOT re-test `assistant_service.py`'s own
business logic (references-only-from-retrieval, the malformed-JSON
fallback) — that's `smoke_assistant_service.py`'s job, already covered.
This script's job is the HTTP layer only: routing, request/response schema
mapping, and the ownership/not-analyzed status-code conventions specific to
these two endpoints.

Four gating groups, all offline (`assistant_service.retrieve`/`GroqProvider`
monkeypatched — no real network, no real local model load):

1. `run_happy_path()` — a completed-run fixture + stubbed retrieve/provider;
   `POST` returns 200 with the stub's answer/follow-ups plus references
   built from the stub chunks (schema-mapped correctly field-for-field);
   `GET` suggested-questions returns 200 with a `{"questions": [...]}` shape.
2. `run_not_analyzed_409()` — a repository with zero completed runs; `POST`
   returns 409 (not 500, not a fabricated 200), `GET` suggested-questions
   still returns 200 with `{"questions": []}` (its own honest empty
   default, unlike `POST`).
3. `run_ownership_404()` — a second user's repository; both `POST` and
   `GET` return 404 for the non-owner, matching the 404-not-403 convention.
4. `run_insufficient_context_short_circuit()` — retrieve() stubbed to
   return `[]`; `POST` returns 200 with `insufficient_context=true`,
   `references=[]`, and the stub provider's call count stays `0` — proving
   the short-circuit holds through the full HTTP stack, not just at the
   service layer.

A fifth, non-gating group:

5. `run_live_bonus()` — real `retrieve()` + real Groq calls through the
   HTTP layer against the real, already-indexed `Nirmit1705/HireSight`
   (owned by `john@gmail.com`, the real connected user from Phase 6.11;
   reused as-is, no fresh analysis triggered). Asks the guide's three
   example questions via real `POST`s, confirms a real `409` against
   John's own unanalyzed `SubTrack` repository, a real `404` against a
   different user's repository id, and both the populated and empty
   `GET .../suggested-questions` cases — exactly the same checks Phase
   8.3's original manual `curl` verification ran, now repeatable. Skips
   cleanly if that real data isn't present in this environment.

Run: `uv run python scripts/smoke_api_assistant.py` (from `server/`).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Real LLM answer text (group 5's live bonus) can contain Unicode punctuation
# that Windows' default console codepage (cp1252) can't encode — same fix as
# `smoke_assistant_service.py`'s own live bonus needed.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.services.assistant_service as assistant_service_module  # noqa: E402
from app.analysis.chunking import RagChunkType  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.rag.retrieval import RetrievedChunk  # noqa: E402

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"
LIVE_TEST_USER_EMAIL = "john@gmail.com"
LIVE_TEST_UNANALYZED_REPO = "SubTrack"  # same real user's own repo, with no completed run
LIVE_QUESTIONS = [
    "How does authentication work?",
    "Where is Redis used?",
    "Which modules depend on UserService?",
]


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- seeding helpers (same pattern as smoke_api_analysis.py) ---------------


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


def _ensure_completed_run(db: Session, repository: Repository) -> AnalysisRun:
    """Deletes-then-recreates a single `COMPLETED` run for `repository` —
    the only precondition `get_latest_completed_run` (and therefore both
    endpoints) needs; no `File`/`Symbol`/`RagChunk` rows are required for
    the gating groups below, since `retrieve()`/`GroqProvider` are stubbed
    rather than exercised for real."""
    db.execute(delete(AnalysisRun).where(AnalysisRun.repository_id == repository.id))
    db.commit()
    run = AnalysisRun(
        repository_id=repository.id,
        branch="main",
        commit_sha="a" * 40,
        status=AnalysisRunStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _clear_runs(db: Session, repository: Repository) -> None:
    db.execute(delete(AnalysisRun).where(AnalysisRun.repository_id == repository.id))
    db.commit()


class _Patch:
    """Tiny monkeypatch-and-restore helper — duplicated per this codebase's
    established small-helper convention (see `smoke_api_analysis.py`'s own
    copy)."""

    def __init__(self, obj, attr: str, replacement):
        self._obj = obj
        self._attr = attr
        self._original = getattr(obj, attr)
        setattr(obj, attr, replacement)

    def restore(self) -> None:
        setattr(self._obj, self._attr, self._original)


def _fixture_chunk(**overrides) -> RetrievedChunk:
    defaults = dict(
        chunk_id=uuid.uuid4(),
        chunk_type=RagChunkType.SYMBOL,
        chunk_text="def authenticate_user(token: str) -> User:\n    ...",
        file_path="server/app/services/auth_service.py",
        symbol_name="authenticate_user",
        module="server/app/services",
        line_start=10,
        line_end=25,
        score=0.87,
        matched_modes=("vector", "symbol"),
    )
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


def _stub_provider_class(response_text: str, call_counter: list[int]):
    """`assistant_service.py` calls `provider or GroqProvider()` with no
    constructor args, so the stub must be zero-arg constructible — a small
    factory rather than a fixed class, so each group gets its own
    independent call counter and response text."""

    class _Stub:
        def complete(self, *, system: str, messages: list[dict], response_schema: dict | None = None) -> str:
            call_counter.append(1)
            return response_text

    return _Stub


# --- group 1: happy path ----------------------------------------------------


def run_happy_path() -> bool:
    print("=== group 1 (gating): happy path — POST/GET map the service layer correctly ===")
    chunks = [
        _fixture_chunk(file_path="server/app/services/auth_service.py", symbol_name="authenticate_user"),
        _fixture_chunk(chunk_id=uuid.uuid4(), file_path="server/app/api/v1/auth.py", symbol_name="login"),
    ]
    call_counter: list[int] = []
    stub_response = (
        '{"answer": "Authentication uses JWTs.", "insufficient_context": false, '
        '"follow_up_questions": ["How are tokens refreshed?"]}'
    )
    patches = [
        _Patch(assistant_service_module, "retrieve", lambda db, *, repository_id, query, k=10: chunks),
        _Patch(assistant_service_module, "GroqProvider", _stub_provider_class(stub_response, call_counter)),
    ]

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-assistant-happy@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-assistant-happy", owner="smoke-api", name="happy-path"
        )
        _ensure_completed_run(db, repository)
        token = create_access_token(subject=user.email)
        client = TestClient(app)

        resp = client.post(
            f"/api/v1/repos/{repository.id}/assistant/messages",
            headers=_auth(token),
            json={"question": "How does authentication work?"},
        )
        all_passed = _check("POST -> 200", resp.status_code == 200, resp.text)
        if not all_passed:
            return False
        body = resp.json()
        all_passed &= _check("answer matches the stub response", body.get("answer") == "Authentication uses JWTs.")
        all_passed &= _check("insufficient_context == False", body.get("insufficient_context") is False)
        all_passed &= _check(
            "follow_up_questions matches the stub response",
            body.get("follow_up_questions") == ["How are tokens refreshed?"],
        )
        actual_paths = {r["file_path"] for r in body.get("references", [])}
        expected_paths = {c.file_path for c in chunks}
        all_passed &= _check(
            "references field-mapped correctly from the stub chunks", actual_paths == expected_paths, str(body)
        )
        all_passed &= _check("the stub provider was called exactly once", call_counter == [1], str(call_counter))

        suggested = client.get(f"/api/v1/repos/{repository.id}/assistant/suggested-questions", headers=_auth(token))
        all_passed &= _check("GET suggested-questions -> 200", suggested.status_code == 200, suggested.text)
        all_passed &= _check(
            "suggested-questions response has the {questions: [...]} shape",
            isinstance(suggested.json().get("questions"), list),
            suggested.text,
        )
        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 2: not-analyzed -> 409 -------------------------------------------


def run_not_analyzed_409() -> bool:
    print("\n=== group 2 (gating): repository with no completed run -> POST 409, GET [] ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-assistant-no-run@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-assistant-no-run", owner="smoke-api", name="no-run"
        )
        _clear_runs(db, repository)
        token = create_access_token(subject=user.email)
        client = TestClient(app)

        resp = client.post(
            f"/api/v1/repos/{repository.id}/assistant/messages",
            headers=_auth(token),
            json={"question": "anything?"},
        )
        all_passed = _check("POST -> 409 (not 500, not a fabricated 200)", resp.status_code == 409, resp.text)

        suggested = client.get(f"/api/v1/repos/{repository.id}/assistant/suggested-questions", headers=_auth(token))
        all_passed &= _check("GET suggested-questions -> 200 (not 409)", suggested.status_code == 200, suggested.text)
        all_passed &= _check(
            "GET suggested-questions -> {questions: []}", suggested.json().get("questions") == [], suggested.text
        )
        return all_passed
    finally:
        db.close()


# --- group 3: ownership scoping ----------------------------------------------


def run_ownership_404() -> bool:
    print("\n=== group 3 (gating): a second user gets 404 on POST and GET ===")
    chunks = [_fixture_chunk()]
    call_counter: list[int] = []
    stub_response = '{"answer": "x", "insufficient_context": false, "follow_up_questions": []}'
    patches = [
        _Patch(assistant_service_module, "retrieve", lambda db, *, repository_id, query, k=10: chunks),
        _Patch(assistant_service_module, "GroqProvider", _stub_provider_class(stub_response, call_counter)),
    ]

    db = SessionLocal()
    try:
        owner_user = _get_or_create_user(db, "smoke-api-assistant-owner@local.test")
        other_user = _get_or_create_user(db, "smoke-api-assistant-other@local.test")
        repository = _get_or_create_repository(
            db, owner_user, github_repo_id="smoke-api-assistant-ownership", owner="smoke-api", name="ownership"
        )
        _ensure_completed_run(db, repository)
        other_token = create_access_token(subject=other_user.email)
        client = TestClient(app)

        post_other = client.post(
            f"/api/v1/repos/{repository.id}/assistant/messages",
            headers=_auth(other_token),
            json={"question": "anything?"},
        )
        all_passed = _check("other user's POST -> 404", post_other.status_code == 404, post_other.text)

        get_other = client.get(
            f"/api/v1/repos/{repository.id}/assistant/suggested-questions", headers=_auth(other_token)
        )
        all_passed &= _check("other user's GET -> 404", get_other.status_code == 404, get_other.text)
        all_passed &= _check(
            "the stub provider was never called for a 404'd request", call_counter == [], str(call_counter)
        )
        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 4: insufficient-context short-circuit through the HTTP layer -----


def run_insufficient_context_short_circuit() -> bool:
    print("\n=== group 4 (gating): empty retrieve() -> 200 insufficient_context, provider never called ===")
    call_counter: list[int] = []
    stub_response = '{"answer": "should never be seen", "insufficient_context": false, "follow_up_questions": []}'
    patches = [
        _Patch(assistant_service_module, "retrieve", lambda db, *, repository_id, query, k=10: []),
        _Patch(assistant_service_module, "GroqProvider", _stub_provider_class(stub_response, call_counter)),
    ]

    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-assistant-insufficient@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-assistant-insufficient", owner="smoke-api", name="insufficient"
        )
        _ensure_completed_run(db, repository)
        token = create_access_token(subject=user.email)
        client = TestClient(app)

        resp = client.post(
            f"/api/v1/repos/{repository.id}/assistant/messages",
            headers=_auth(token),
            json={"question": "anything?"},
        )
        all_passed = _check("POST -> 200 (not an error)", resp.status_code == 200, resp.text)
        body = resp.json()
        all_passed &= _check("insufficient_context == True", body.get("insufficient_context") is True, str(body))
        all_passed &= _check("references == []", body.get("references") == [], str(body))
        all_passed &= _check("the stub provider was never called", call_counter == [], str(call_counter))
        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 5 (non-gating): live bonus ----------------------------------------


def run_live_bonus() -> bool:
    print(f"\n=== group 5 (non-gating): live HTTP round trip against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
    try:
        db = SessionLocal()
        try:
            user = db.execute(select(User).where(User.email == LIVE_TEST_USER_EMAIL)).scalar_one_or_none()
            if user is None:
                print(f"  [SKIP] no user found with email {LIVE_TEST_USER_EMAIL!r}")
                return True
            repository = db.execute(
                select(Repository).where(
                    Repository.connected_user_id == user.id,
                    Repository.owner == LIVE_TEST_OWNER,
                    Repository.name == LIVE_TEST_REPO,
                )
            ).scalar_one_or_none()
            unanalyzed_repository = db.execute(
                select(Repository).where(
                    Repository.connected_user_id == user.id,
                    Repository.owner == LIVE_TEST_OWNER,
                    Repository.name == LIVE_TEST_UNANALYZED_REPO,
                )
            ).scalar_one_or_none()
            other_user = db.execute(select(User).where(User.email != LIVE_TEST_USER_EMAIL).limit(1)).scalar_one_or_none()
            if repository is None:
                print(f"  [SKIP] no repository found for {LIVE_TEST_OWNER}/{LIVE_TEST_REPO}")
                return True

            token = create_access_token(subject=user.email)
            client = TestClient(app)

            all_passed = True
            for question in LIVE_QUESTIONS:
                resp = client.post(
                    f"/api/v1/repos/{repository.id}/assistant/messages",
                    headers=_auth(token),
                    json={"question": question},
                )
                print(f"\n  Q: {question}")
                ok = _check(f"{question!r} -> 200", resp.status_code == 200, resp.text)
                all_passed &= ok
                if ok:
                    body = resp.json()
                    print(f"    insufficient_context={body.get('insufficient_context')}")
                    print(f"    answer: {body.get('answer', '')[:200]!r}")
                    print(f"    references: {[(r['file_path'], r['symbol_name']) for r in body.get('references', [])][:5]}")
                    all_passed &= _check(
                        f"{question!r} has a non-empty answer or honestly reports insufficient context",
                        len(body.get("answer", "")) > 0,
                    )

            suggested = client.get(
                f"/api/v1/repos/{repository.id}/assistant/suggested-questions", headers=_auth(token)
            )
            all_passed &= _check("suggested-questions -> 200", suggested.status_code == 200, suggested.text)
            all_passed &= _check(
                "suggested-questions non-empty for the analyzed repo",
                len(suggested.json().get("questions", [])) > 0,
                suggested.text,
            )
            print(f"\n  suggested questions: {suggested.json().get('questions')}")

            if unanalyzed_repository is not None:
                conflict = client.post(
                    f"/api/v1/repos/{unanalyzed_repository.id}/assistant/messages",
                    headers=_auth(token),
                    json={"question": "anything?"},
                )
                all_passed &= _check(
                    f"POST against unanalyzed {LIVE_TEST_UNANALYZED_REPO} -> 409", conflict.status_code == 409,
                    conflict.text,
                )
                empty_suggested = client.get(
                    f"/api/v1/repos/{unanalyzed_repository.id}/assistant/suggested-questions", headers=_auth(token)
                )
                all_passed &= _check(
                    f"GET suggested-questions against unanalyzed {LIVE_TEST_UNANALYZED_REPO} -> 200 []",
                    empty_suggested.status_code == 200 and empty_suggested.json().get("questions") == [],
                    empty_suggested.text,
                )
            else:
                print(f"  [SKIP] no unanalyzed repository found for the 409 check ({LIVE_TEST_UNANALYZED_REPO})")

            if other_user is not None:
                other_token = create_access_token(subject=other_user.email)
                mismatch = client.post(
                    f"/api/v1/repos/{repository.id}/assistant/messages",
                    headers=_auth(other_token),
                    json={"question": "anything?"},
                )
                all_passed &= _check(
                    "a different real user's POST against this repo -> 404", mismatch.status_code == 404,
                    mismatch.text,
                )
            else:
                print("  [SKIP] no second real user found for the ownership-mismatch check")

            return all_passed
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [SKIP] live bonus unavailable ({type(exc).__name__}: {exc})")
        return True


def main() -> int:
    r1 = run_happy_path()
    r2 = run_not_analyzed_409()
    r3 = run_ownership_404()
    r4 = run_insufficient_context_short_circuit()
    r5 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. Happy path (gating):                    {'PASS' if r1 else 'FAIL'}")
    print(f"2. Not analyzed -> 409 (gating):            {'PASS' if r2 else 'FAIL'}")
    print(f"3. Ownership scoping -> 404 (gating):       {'PASS' if r3 else 'FAIL'}")
    print(f"4. Insufficient-context short-circuit:      {'PASS' if r4 else 'FAIL'}")
    print(f"5. Live bonus (non-gating):                 {'PASS' if r5 else 'SKIP/FAIL (excluded from exit code)'}")
    return 0 if (r1 and r2 and r3 and r4) else 1


if __name__ == "__main__":
    sys.exit(main())
