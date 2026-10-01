"""Manual smoke test for Phase 10.4's per-finding AI recommendation:
`app/services/debt_insight_service.py::get_or_generate_recommendation`,
`app/ai/prompts.py::build_debt_recommendation_prompt`, and
`GET /repos/{id}/debt/issues/{issue_id}/recommendation`
(`app/api/v1/debt.py`).

Same stub-provider technique as `smoke_file_insight.py`/`smoke_api_compare.py`:
a `_CountingStub` that returns a fixed string (or raises) and counts calls, so
"a cache hit means zero calls" is asserted, not assumed. Findings are seeded
with `smoke_debt_service.seed_run`; `RagChunk` rows are inserted directly with a
random vector (this feature never searches them).

Gating groups (real DB, no Celery/GitHub; the model is always a stub):

1. `run_cache_hit()` — `ai_recommendation` already set -> returned verbatim, zero calls.
2. `run_generate_and_cache()` — happy path: stripped text returned, persisted,
   the second call makes zero provider calls.
3. `run_failure_modes()` — nine ways generation can go wrong (provider error,
   misconfigured provider, an unexpected exception, non-JSON, JSON that is not
   an object, missing key, wrong type, null, empty/whitespace) -> `None` and
   *nothing persisted* each time.
4. `run_prompt_grounding()` — what the model is actually shown: the finding's
   facts and its own file's chunks only (not another file's, not another run's);
   a high-complexity finding's overlapping chunk is ordered before earlier
   chunks; the 8000-char cap is real; a module-level finding and a chunk-less
   file still make exactly one call, with the "no excerpts" wording.
5. `run_api()` — over HTTP (provider patched): 200 + text, cached on the second
   GET, a failure is `200 {recommendation: null}` (never 5xx) and then retried,
   404 for an unknown id / another user / another repository's finding, and an
   older run's finding id still resolves.
6. `run_live_bonus()` (non-gating) — two real HireSight findings through the
   real Groq provider, printed for a human to judge groundedness.

Run: `uv run python scripts/smoke_debt_insight.py` (from `server/`).
"""

from __future__ import annotations

import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.ai.provider import LLMProviderError  # noqa: E402
from app.analysis.chunking import RagChunkType  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun  # noqa: E402
from app.models.debt_issue import DebtIssue  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.rag_chunk import EMBEDDING_DIM, RagChunk  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import analysis_read_service, debt_insight_service  # noqa: E402
from smoke_debt_service import seed_run  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"

COMPLEXITY_DESC = "function 'build' (lines 40-60) has cyclomatic complexity 33"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _CountingStub:
    """Returns a fixed string, or raises, counting calls and recording what the
    model was shown."""

    def __init__(self, response: str | None = None, *, raises: Exception | None = None):
        self._response, self._raises = response, raises
        self.calls = 0
        self.systems: list[str] = []
        self.users: list[str] = []

    def complete(self, *, system: str, messages: list[dict], response_schema: dict | None = None) -> str:
        self.calls += 1
        self.systems.append(system)
        self.users.append(messages[0]["content"])
        if self._raises is not None:
            raise self._raises
        return self._response  # type: ignore[return-value]


def _ok(text: str) -> str:
    return json.dumps({"recommendation": text})


def _vec() -> list[float]:
    return [random.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)]


def _add_chunk(db: Session, repository: Repository, run: AnalysisRun, file_id: uuid.UUID, *, idx: int, text: str, a: int, b: int,
               ctype: RagChunkType = RagChunkType.SYMBOL) -> None:
    db.add(RagChunk(
        repository_id=repository.id, analysis_run_id=run.id, file_id=file_id, chunk_type=ctype, chunk_index=idx,
        chunk_count=1, chunk_text=text, line_start=a, line_end=b, module="app", chunk_metadata={}, embedding=_vec(),
    ))


def _fresh(db: Session, slug: str, issues):
    """A new user/repository/run with the given issue specs; returns (user, repository, run)."""
    user = _get_or_create_user(db, f"smoke-debt-insight-{slug}@local.test")
    repository = _get_or_create_repository(
        db, user, github_repo_id=f"smoke-debt-insight-{slug}", owner="smoke-debt", name=f"insight-{slug}"
    )
    _clear_previous_runs(db, repository)
    run = seed_run(db, repository, commit=slug[0], completed_at=datetime.now(timezone.utc) - timedelta(hours=1), issues=issues)
    return user, repository, run


def _issue(db: Session, run: AnalysisRun, issue_type: str) -> DebtIssue:
    from app.analysis.debt import DebtIssueType
    return db.execute(select(DebtIssue).where(DebtIssue.analysis_run_id == run.id, DebtIssue.issue_type == DebtIssueType(issue_type))).scalars().first()


def _persisted(issue_id: uuid.UUID):
    """Fresh-session read, so we see what is really in the database."""
    s = SessionLocal()
    try:
        return s.get(DebtIssue, issue_id).ai_recommendation
    finally:
        s.close()


FILE_ISSUE = [("high_complexity", "critical", "app/build.py", "app", COMPLEXITY_DESC, 33)]


def run_cache_hit() -> bool:
    print("=== group 1: a cached recommendation is returned with zero provider calls ===")
    db = SessionLocal()
    try:
        _u, _r, run = _fresh(db, "cache", FILE_ISSUE)
        issue = _issue(db, run, "high_complexity")
        issue.ai_recommendation = "Already cached."
        db.commit()
        stub = _CountingStub(raises=AssertionError("provider must not be called on a cache hit"))
        got = debt_insight_service.get_or_generate_recommendation(db, issue=issue, provider=stub)
        return _check("cached value returned verbatim", got == "Already cached.", str(got)) & _check("zero provider calls", stub.calls == 0, str(stub.calls))
    finally:
        db.close()


def run_generate_and_cache() -> bool:
    print("\n=== group 2: happy path generates once, persists, then serves from the row ===")
    db = SessionLocal()
    try:
        _u, _r, run = _fresh(db, "happy", FILE_ISSUE)
        issue = _issue(db, run, "high_complexity")
        stub = _CountingStub(_ok("  Split `build` into smaller steps.  "))
        got = debt_insight_service.get_or_generate_recommendation(db, issue=issue, provider=stub)
        ok = _check("returns the stripped text", got == "Split `build` into smaller steps.", repr(got))
        ok &= _check("exactly one provider call", stub.calls == 1)
        ok &= _check("persisted in the database", _persisted(issue.id) == "Split `build` into smaller steps.")
        again = _CountingStub(raises=AssertionError("second call must hit the cache"))
        ok &= _check("second call serves the row with zero provider calls", debt_insight_service.get_or_generate_recommendation(db, issue=issue, provider=again) == got and again.calls == 0)
        return ok
    finally:
        db.close()


def run_failure_modes() -> bool:
    print("\n=== group 3: nine failure modes -> None, and nothing is persisted ===")
    db = SessionLocal()
    try:
        _u, _r, run = _fresh(db, "fail", FILE_ISSUE)
        issue = _issue(db, run, "high_complexity")
        cases = [
            ("LLMProviderError", _CountingStub(raises=LLMProviderError("boom"))),
            ("RuntimeError (misconfigured provider)", _CountingStub(raises=RuntimeError("LLM_PROVIDER not supported"))),
            ("unexpected exception (TimeoutError)", _CountingStub(raises=TimeoutError("slow"))),
            ("non-JSON text", _CountingStub("here is some advice")),
            ("JSON that is not an object", _CountingStub(json.dumps(["a", "b"]))),
            ("object missing the key", _CountingStub(json.dumps({"advice": "x"}))),
            ("recommendation of the wrong type", _CountingStub(json.dumps({"recommendation": 42}))),
            ("recommendation is null", _CountingStub(json.dumps({"recommendation": None}))),
            ("empty / whitespace-only recommendation", _CountingStub(_ok("   \n  "))),
        ]
        ok = True
        for label, stub in cases:
            got = debt_insight_service.get_or_generate_recommendation(db, issue=issue, provider=stub)
            ok &= _check(f"{label} -> None, one attempt, nothing persisted", got is None and stub.calls == 1 and _persisted(issue.id) is None, f"{got!r} calls={stub.calls} db={_persisted(issue.id)!r}")
        # and a later good answer still works (a failure never poisons the row)
        good = _CountingStub(_ok("Now it works."))
        ok &= _check("a later successful attempt is generated and persisted", debt_insight_service.get_or_generate_recommendation(db, issue=issue, provider=good) == "Now it works." and _persisted(issue.id) == "Now it works.")
        return ok
    finally:
        db.close()


def run_prompt_grounding() -> bool:
    print("\n=== group 4: what the model is shown ===")
    db = SessionLocal()
    try:
        issues = FILE_ISSUE + [
            ("large_file", "high", "app/other.py", "app", "file has 700 non-blank lines of code (730 total)", 700),
            ("excessive_coupling", "medium", None, "app", "module has coupling score 12 (fan_in=5, fan_out=7)", 12),
            ("large_file", "medium", "app/empty.py", "app", "file has 350 non-blank lines of code (360 total)", 350),
        ]
        _u, repository, run = _fresh(db, "prompt", issues)
        fid = {f.path: f.id for f in db.execute(select(File).where(File.analysis_run_id == run.id)).scalars().all()}
        # build.py: an early chunk, the FILE_OVERVIEW, and the chunk that actually holds lines 40-60 (placed LAST by index)
        _add_chunk(db, repository, run, fid["app/build.py"], idx=0, text="EARLY-CHUNK imports and constants", a=1, b=10)
        _add_chunk(db, repository, run, fid["app/build.py"], idx=1, text="OVERVIEW-CHUNK file summary", a=1, b=100, ctype=RagChunkType.FILE_OVERVIEW)
        _add_chunk(db, repository, run, fid["app/build.py"], idx=2, text="TARGET-CHUNK def build(): ...", a=38, b=62)
        # another file's chunk in the same run: must never be shown for build.py's finding
        _add_chunk(db, repository, run, fid["app/other.py"], idx=0, text="OTHER-FILE-CHUNK", a=1, b=5)
        # a big file whose chunks far exceed the cap
        for i in range(6):
            _add_chunk(db, repository, run, fid["app/other.py"], idx=i + 1, text=("Z" * 3000), a=10 * i, b=10 * i + 9)
        db.commit()

        ok = True
        stub = _CountingStub(_ok("x"))
        debt_insight_service.get_or_generate_recommendation(db, issue=_issue(db, run, "high_complexity"), provider=stub)
        u = stub.users[0]
        ok &= _check("the finding's facts are in the prompt", all(t in u for t in ("High complexity", "critical", "33", COMPLEXITY_DESC, "app/build.py")), u[:300])
        ok &= _check("the file's own chunks are shown; another file's are not", "TARGET-CHUNK" in u and "OVERVIEW-CHUNK" in u and "OTHER-FILE-CHUNK" not in u)
        ok &= _check("the chunk overlapping the function's lines (40-60) comes first even though it has the last index "
                     "(the whole-file overview does not outrank it), then the overview, then the rest",
                     u.index("TARGET-CHUNK") < u.index("OVERVIEW-CHUNK") < u.index("EARLY-CHUNK"), "")
        ok &= _check("the system prompt carries the no-invention / absence-is-not-evidence rules",
                     "never invent" in stub.systems[0].lower() and "absence of information is not evidence" in stub.systems[0])

        stub2 = _CountingStub(_ok("x"))
        other_issue = db.execute(select(DebtIssue).where(DebtIssue.analysis_run_id == run.id, DebtIssue.file_id == fid["app/other.py"])).scalars().first()
        debt_insight_service.get_or_generate_recommendation(db, issue=other_issue, provider=stub2)
        z = stub2.users[0].count("Z")
        ok &= _check("the 8000-char cap is real (the last chunk is cut to fit)", 0 < z <= debt_insight_service.MAX_CONTEXT_CHARS, f"{z} Z chars")

        stub3 = _CountingStub(_ok("x"))
        got = debt_insight_service.get_or_generate_recommendation(db, issue=_issue(db, run, "excessive_coupling"), provider=stub3)
        ok &= _check("a module-level finding still makes exactly one call, told there is no file and no excerpts",
                     got == "x" and stub3.calls == 1 and "No code excerpts are available" in stub3.users[0] and "not one file" in stub3.users[0] and "Module: app" in stub3.users[0], stub3.users[0][:300])

        stub4 = _CountingStub(_ok("x"))
        empty_issue = db.execute(select(DebtIssue).where(DebtIssue.analysis_run_id == run.id, DebtIssue.file_id == fid["app/empty.py"])).scalars().first()
        debt_insight_service.get_or_generate_recommendation(db, issue=empty_issue, provider=stub4)
        ok &= _check("a file with no chunks is still advised on, from the finding alone", stub4.calls == 1 and "No code excerpts are available" in stub4.users[0] and "app/empty.py" in stub4.users[0])
        return ok
    finally:
        db.close()


def run_api() -> bool:
    print("\n=== group 5: HTTP — caching, graceful failure, ownership ===")
    db = SessionLocal()
    original = debt_insight_service.GroqProvider
    try:
        user, repository, run = _fresh(db, "api", FILE_ISSUE + [("large_file", "medium", "app/big.py", "app", "file has 400 non-blank lines of code (450 total)", 400)])
        other = _get_or_create_user(db, "smoke-debt-insight-api-other@local.test")
        other_repo = _get_or_create_repository(db, other, github_repo_id="smoke-debt-insight-api-other", owner="smoke-debt", name="insight-api-other")
        _clear_previous_runs(db, other_repo)
        other_run = seed_run(db, other_repo, commit="o", completed_at=datetime.now(timezone.utc), issues=FILE_ISSUE)
        foreign = _issue(db, other_run, "high_complexity")

        issue = _issue(db, run, "high_complexity")
        big = _issue(db, run, "large_file")
        client = TestClient(app)
        headers = _auth(create_access_token(subject=user.email))
        url = lambda rid, iid: f"/api/v1/repos/{rid}/debt/issues/{iid}/recommendation"  # noqa: E731

        stub = _CountingStub(_ok("Extract the three phases of build()."))
        debt_insight_service.GroqProvider = lambda: stub
        r = client.get(url(repository.id, issue.id), headers=headers)
        ok = _check("first GET -> 200 with the generated text", r.status_code == 200 and r.json() == {"issue_id": str(issue.id), "recommendation": "Extract the three phases of build()."}, r.text)
        r = client.get(url(repository.id, issue.id), headers=headers)
        ok &= _check("second GET is served from the cache (still one provider call)", r.status_code == 200 and r.json()["recommendation"] == "Extract the three phases of build()." and stub.calls == 1, f"calls={stub.calls}")

        failing = _CountingStub(raises=LLMProviderError("down"))
        debt_insight_service.GroqProvider = lambda: failing
        r = client.get(url(repository.id, big.id), headers=headers)
        ok &= _check("provider failure -> 200 {recommendation: null}, not a 5xx", r.status_code == 200 and r.json() == {"issue_id": str(big.id), "recommendation": None}, f"{r.status_code} {r.text}")
        recovered = _CountingStub(_ok("Split big.py by responsibility."))
        debt_insight_service.GroqProvider = lambda: recovered
        r = client.get(url(repository.id, big.id), headers=headers)
        ok &= _check("the next GET retries and succeeds (the failure was not cached)", r.json()["recommendation"] == "Split big.py by responsibility." and recovered.calls == 1)

        r = client.get(url(repository.id, uuid.uuid4()), headers=headers)
        ok &= _check("unknown finding id -> 404", r.status_code == 404, str(r.status_code))
        r = client.get(url(repository.id, issue.id), headers=_auth(create_access_token(subject=other.email)))
        ok &= _check("another user's token -> 404", r.status_code == 404, str(r.status_code))
        r = client.get(url(repository.id, foreign.id), headers=headers)
        ok &= _check("a finding that belongs to a different repository -> 404", r.status_code == 404, str(r.status_code))
        r = client.get(url(repository.id, issue.id))
        ok &= _check("no token -> 401/403", r.status_code in (401, 403), str(r.status_code))

        # an older run's finding still resolves (ids are handed out by an earlier list fetch)
        newer = seed_run(db, repository, commit="n", completed_at=datetime.now(timezone.utc) + timedelta(minutes=5), issues=FILE_ISSUE)
        assert newer.id != run.id
        stub_old = _CountingStub(_ok("Older run, still reachable."))
        debt_insight_service.GroqProvider = lambda: stub_old
        old_issue = _issue(db, run, "large_file")
        old_issue.ai_recommendation = None
        db.commit()
        r = client.get(url(repository.id, old_issue.id), headers=headers)
        ok &= _check("a finding from a superseded run still resolves", r.status_code == 200 and r.json()["recommendation"] == "Older run, still reachable.", r.text)
        return ok
    finally:
        debt_insight_service.GroqProvider = original
        db.close()


def run_live_bonus() -> bool:
    print("\n=== group 6 (non-gating): two real HireSight findings through real Groq ===")
    db = SessionLocal()
    try:
        best = None
        for r in db.execute(select(Repository).where(Repository.owner == LIVE_TEST_OWNER, Repository.name == LIVE_TEST_REPO)).scalars().all():
            run = analysis_read_service.get_latest_completed_run(db, r.id)
            if run is not None and (best is None or (run.completed_at or run.created_at) > (best[1].completed_at or best[1].created_at)):
                best = (r, run)
        if best is None:
            print("  [SKIP] no analyzed HireSight repository")
            return True
        repository, run = best
        from app.analysis.debt import DebtIssueType
        picks = [
            ("a file-scoped Large file finding", db.execute(select(DebtIssue).join(File, File.id == DebtIssue.file_id).where(
                DebtIssue.analysis_run_id == run.id, DebtIssue.issue_type == DebtIssueType.LARGE_FILE, File.path.like("%resumeProcessingService.ts")
            )).scalars().first()),
            ("the module-level circular-dependency finding", db.execute(select(DebtIssue).where(
                DebtIssue.analysis_run_id == run.id, DebtIssue.issue_type == DebtIssueType.CIRCULAR_DEPENDENCY
            )).scalars().first()),
        ]
        ok = True
        for label, issue in picks:
            if issue is None:
                print(f"  [SKIP] {label} not found")
                continue
            was_cached = issue.ai_recommendation is not None
            text = debt_insight_service.get_or_generate_recommendation(db, issue=issue)
            print(f"  --- {label} ({'served from cache' if was_cached else 'freshly generated'}):")
            print(f"      finding: {issue.description[:140]}")
            print(f"      recommendation: {text}")
            ok &= _check(f"{label}: a non-empty recommendation came back", bool(text))
            again = debt_insight_service.get_or_generate_recommendation(db, issue=issue, provider=_CountingStub(raises=AssertionError("must be cached")))
            ok &= _check(f"{label}: the second call is served from the row", again == text)
        return ok
    finally:
        db.close()


def main() -> int:
    gating = [run_cache_hit(), run_generate_and_cache(), run_failure_modes(), run_prompt_grounding(), run_api()]
    bonus = run_live_bonus()
    print("\n" + ("ALL GATING GROUPS PASS" if all(gating) else "SOME GATING GROUPS FAILED") + ("" if bonus else " (live bonus failed)"))
    return 0 if all(gating) else 1


if __name__ == "__main__":
    sys.exit(main())
