"""Manual smoke test for Phase 8.2's grounded Q&A orchestration + suggested
questions (`app/services/assistant_service.py`).

Five groups, run in this order:

1. `run_references_from_retrieval_only()` (gating, DoD (a)) — a
   monkeypatched `retrieve()` returns known fixture chunks; a stub
   `LLMProvider` returns an answer whose *text* mentions a fabricated file
   path. Asserts `references` exactly match the fixture chunks — proving
   references are built only from retrieval, never parsed out of the LLM's
   own text.
2. `run_insufficient_context_short_circuit()` (gating, DoD (b)) — an empty
   `retrieve()` result, and a result where every chunk's score is below the
   relevance floor, both short-circuit to the fixed insufficient-context
   answer with the stub provider's `complete()` call count staying `0`.
3. `run_malformed_json_fallback()` (gating, DoD (c)) — a stub provider
   returning non-JSON text, and separately well-formed JSON missing a
   required key, both degrade to the plain-text fallback (`answer == raw`,
   `insufficient_context is False`, `follow_up_questions == []`) without
   raising; references still come from the real retrieved chunks.
4. `run_suggested_questions_empty_repo()` (gating, extra coverage beyond
   the plan's own DoD list) — `list_suggested_questions` returns `[]` for a
   real repository with no completed analysis run.
5. `run_live_bonus()` (non-gating) — real `Nirmit1705/HireSight` (already
   analyzed + indexed as of Phase 7's own signoff — reused as-is rather
   than re-triggering a fresh ~10+ minute analysis run, since this
   sub-phase is testing Q&A orchestration, not indexing), the guide's three
   example questions through the real `answer_question` (real `retrieve()`
   + a real Groq call each), plus `list_suggested_questions` against the
   same repo — eyeballed for plausible output. Whole body in one
   `try/except Exception` -> `[SKIP]`, mirroring every other live-bonus
   group in this codebase.

Run: `uv run python scripts/smoke_assistant_service.py` (from `server/`).
Groups 1-4 make zero real network/model/DB-write calls (group 4 does one
real read-only DB round trip to confirm the "no completed run" case).
Group 5 makes real Groq + local-model calls against the real dev DB.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Real LLM answer text (group 5's live bonus) can contain Unicode punctuation
# (curly quotes, en/em-dashes) that Windows' default console codepage
# (cp1252) can't encode — reconfigure stdout to UTF-8 with a safe fallback so
# printing a real answer never crashes the script itself.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.analysis.chunking import RagChunkType  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.rag.retrieval import RetrievedChunk  # noqa: E402
from app.services import assistant_service  # noqa: E402
from app.services.analysis_read_service import get_latest_completed_run  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"
LIVE_TEST_USER_EMAIL = "john@gmail.com"  # the real, already-GitHub-linked user established in Phase 6.11
LIVE_QUESTIONS = [
    "How does authentication work?",
    "Where is Redis used?",
    "Which modules depend on UserService?",
]


class _Patch:
    """Tiny monkeypatch-and-restore helper — duplicated per this codebase's
    established small-helper convention (see `smoke_ai_provider.py`'s own
    copy)."""

    def __init__(self, obj, attr: str, replacement):
        self._obj = obj
        self._attr = attr
        self._original = getattr(obj, attr)
        setattr(obj, attr, replacement)

    def restore(self) -> None:
        setattr(self._obj, self._attr, self._original)


class _StubProvider:
    """A fake `LLMProvider` returning a fixed response string and counting
    how many times `complete()` was actually invoked."""

    def __init__(self, response_text: str):
        self._response_text = response_text
        self.call_count = 0

    def complete(self, *, system: str, messages: list[dict], response_schema: dict | None = None) -> str:
        self.call_count += 1
        return self._response_text


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


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


def run_references_from_retrieval_only() -> bool:
    print("=== group 1 (gating): references come only from retrieve(), never the LLM's text ===")
    all_passed = True

    chunks = [
        _fixture_chunk(file_path="server/app/services/auth_service.py", symbol_name="authenticate_user"),
        _fixture_chunk(chunk_id=uuid.uuid4(), file_path="server/app/api/v1/auth.py", symbol_name="login"),
    ]
    stub_provider = _StubProvider(
        '{"answer": "See totally/fake/nonexistent-path.py:1-1 for details.", '
        '"insufficient_context": false, "follow_up_questions": []}'
    )

    patch = _Patch(assistant_service, "retrieve", lambda db, *, repository_id, query, k=10: chunks)
    try:
        result = assistant_service.answer_question(
            None, repository_id=uuid.uuid4(), question="How does authentication work?", provider=stub_provider
        )
    finally:
        patch.restore()

    expected_paths = {c.file_path for c in chunks}
    actual_paths = {r.file_path for r in result.references}
    all_passed &= _check("references count matches retrieved chunk count", len(result.references) == len(chunks))
    all_passed &= _check(
        "reference file_paths exactly match retrieved chunks' file_paths", actual_paths == expected_paths
    )
    all_passed &= _check(
        "the LLM's fabricated file path never appears in references",
        "totally/fake/nonexistent-path.py" not in actual_paths,
    )
    all_passed &= _check(
        "reference symbol_name/line_start/line_end come from the chunk, not the LLM",
        all(
            any(
                r.symbol_name == c.symbol_name and r.line_start == c.line_start and r.line_end == c.line_end
                for c in chunks
            )
            for r in result.references
        ),
    )
    return all_passed


def run_insufficient_context_short_circuit() -> bool:
    print("\n=== group 2 (gating): insufficient-context short-circuit never calls the provider ===")
    all_passed = True
    stub_provider = _StubProvider(
        '{"answer": "should never be seen", "insufficient_context": false, "follow_up_questions": []}'
    )

    patch = _Patch(assistant_service, "retrieve", lambda db, *, repository_id, query, k=10: [])
    try:
        result = assistant_service.answer_question(
            None, repository_id=uuid.uuid4(), question="anything?", provider=stub_provider
        )
    finally:
        patch.restore()
    all_passed &= _check("empty retrieve() -> insufficient_context=True", result.insufficient_context is True)
    all_passed &= _check("empty retrieve() -> no references", result.references == [])
    all_passed &= _check("provider.complete() was never called (empty case)", stub_provider.call_count == 0)

    low_score_chunks = [_fixture_chunk(score=0.01), _fixture_chunk(chunk_id=uuid.uuid4(), score=0.05)]
    patch = _Patch(assistant_service, "retrieve", lambda db, *, repository_id, query, k=10: low_score_chunks)
    try:
        result = assistant_service.answer_question(
            None, repository_id=uuid.uuid4(), question="anything?", provider=stub_provider
        )
    finally:
        patch.restore()
    all_passed &= _check("all-low-score retrieve() -> insufficient_context=True", result.insufficient_context is True)
    all_passed &= _check("provider.complete() was never called (low-score case)", stub_provider.call_count == 0)

    return all_passed


def run_malformed_json_fallback() -> bool:
    print("\n=== group 3 (gating): malformed LLM JSON degrades to a plain-text fallback ===")
    all_passed = True
    chunks = [_fixture_chunk()]

    raw_text = "This is not JSON at all, just a plain sentence."
    stub_provider = _StubProvider(raw_text)
    patch = _Patch(assistant_service, "retrieve", lambda db, *, repository_id, query, k=10: chunks)
    try:
        result = assistant_service.answer_question(
            None, repository_id=uuid.uuid4(), question="anything?", provider=stub_provider
        )
    finally:
        patch.restore()
    all_passed &= _check("no exception raised, fallback answer == raw text", result.answer == raw_text)
    all_passed &= _check("fallback insufficient_context is False", result.insufficient_context is False)
    all_passed &= _check("fallback follow_up_questions is empty", result.follow_up_questions == [])
    all_passed &= _check(
        "references are still built from the real retrieved chunks",
        len(result.references) == 1 and result.references[0].file_path == chunks[0].file_path,
    )

    malformed_shape_raw = '{"answer": "partial only"}'  # valid JSON, missing required keys
    stub_provider_2 = _StubProvider(malformed_shape_raw)
    patch = _Patch(assistant_service, "retrieve", lambda db, *, repository_id, query, k=10: chunks)
    try:
        result2 = assistant_service.answer_question(
            None, repository_id=uuid.uuid4(), question="anything?", provider=stub_provider_2
        )
    finally:
        patch.restore()
    all_passed &= _check(
        "valid JSON missing required keys also degrades to the fallback",
        result2.answer == malformed_shape_raw,
    )

    return all_passed


def run_suggested_questions_empty_repo() -> bool:
    print("\n=== group 4 (gating): list_suggested_questions returns [] for a never-analyzed repository ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-assistant-service@local.test")
        repository = _get_or_create_repository(
            db,
            user,
            github_repo_id="smoke-assistant-service-fixture",
            owner="smoke-assistant-service",
            name="fixture",
        )
        _clear_previous_runs(db, repository)
        questions = assistant_service.list_suggested_questions(db, repository_id=repository.id)
        return _check(
            "list_suggested_questions == [] for a never-analyzed repository", questions == [], str(questions)
        )
    finally:
        db.close()


def run_live_bonus() -> bool:
    print(f"\n=== group 5 (non-gating): live answer_question() against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
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
            if repository is None:
                print(f"  [SKIP] no repository found for {LIVE_TEST_OWNER}/{LIVE_TEST_REPO}")
                return True
            if get_latest_completed_run(db, repository.id) is None:
                print("  [SKIP] repository has no completed analysis run")
                return True

            all_passed = True
            for question in LIVE_QUESTIONS:
                result = assistant_service.answer_question(db, repository_id=repository.id, question=question)
                print(f"\n  Q: {question}")
                print(f"    insufficient_context={result.insufficient_context}")
                print(f"    answer: {result.answer[:300]!r}")
                print(f"    references: {[(r.file_path, r.symbol_name) for r in result.references][:5]}")
                print(f"    follow_ups: {result.follow_up_questions}")
                all_passed &= _check(f"{question!r} produced a non-empty answer", len(result.answer) > 0)
                all_passed &= _check(
                    f"{question!r} attached >=1 reference, or honestly reported insufficient context",
                    len(result.references) > 0 or result.insufficient_context,
                )

            questions = assistant_service.list_suggested_questions(db, repository_id=repository.id)
            print(f"\n  suggested questions: {questions}")
            all_passed &= _check(
                "list_suggested_questions returns a non-empty list for the analyzed repo", len(questions) > 0
            )

            return all_passed
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [SKIP] live bonus unavailable ({type(exc).__name__}: {exc})")
        return True


def main() -> int:
    r1 = run_references_from_retrieval_only()
    r2 = run_insufficient_context_short_circuit()
    r3 = run_malformed_json_fallback()
    r4 = run_suggested_questions_empty_repo()
    r5 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. references from retrieval only (gating):    {'PASS' if r1 else 'FAIL'}")
    print(f"2. insufficient-context short-circuit (gating): {'PASS' if r2 else 'FAIL'}")
    print(f"3. malformed JSON fallback (gating):            {'PASS' if r3 else 'FAIL'}")
    print(f"4. suggested questions empty repo (gating):     {'PASS' if r4 else 'FAIL'}")
    print(f"5. live bonus (non-gating):                     {'PASS' if r5 else 'SKIP/FAIL (excluded from exit code)'}")
    return 0 if (r1 and r2 and r3 and r4) else 1


if __name__ == "__main__":
    sys.exit(main())
