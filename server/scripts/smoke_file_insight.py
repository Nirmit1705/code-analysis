"""Manual smoke test for Phase 8.7's per-file AI purpose/explanation
(`app/services/file_insight_service.py::get_or_generate_file_insight`),
wired into `app/api/v1/codebase.py::get_file_insight` in place of the
hardcoded `purpose=None`/`explanation=None` placeholder Phase 6.7/6.8 left
behind.

The `alembic upgrade head` / `downgrade -1` / `upgrade head` round-trip for
the new `File.purpose`/`.explanation` columns was verified manually (not
scripted here) — matches `smoke_ai_insights.py`'s own precedent for its
`ai_insights` column.

Reuses `scripts/fixtures/dependency_cycle/acyclic` throughout (already
hand-verified by `smoke_rag_schema.py`/`smoke_storage.py`): `module_x/x.py`
owns one real symbol (`use_y`), so it gets a real SYMBOL (and FILE_OVERVIEW)
`RagChunkDraft` from Phase 7.2's `build_rag_chunks` to ground on;
`module_x/__init__.py` is an empty file with zero symbols, so it gets zero
chunks — the real zero-`RagChunk` case, not a contrived one. Chunks are
inserted directly as `RagChunk` rows with a random 1024-dim vector (same
technique `smoke_rag_schema.py` uses) rather than through Phase 7.5's real
indexing — this feature never runs a vector search over them (design
decision #1), so a real embedding is unnecessary for gating coverage.

Four gating groups, one fresh fixture repository each (so `File.purpose`
starts `None` in every group, uncontaminated by a previous group's writes):

1. `run_cache_hit()` — `file.purpose`/`.explanation` set directly (bypassing
   the service), then `get_or_generate_file_insight` called with a stub
   provider that raises if it's ever invoked. Proves a cache hit returns the
   exact cached values with zero provider calls.
2. `run_zero_chunk_short_circuit()` — called against `module_x/__init__.py`
   (zero `RagChunk` rows, never seeded with any), same call-count-raising
   stub. Proves the zero-chunk short-circuit fires before any LLM call.
3. `run_malformed_response_not_persisted()` — `module_x/x.py`, seeded with
   one real SYMBOL `RagChunk` row. Three sub-cases against the *same* file
   (safe to chain: none of them ever set `file.purpose`, so each subsequent
   call still finds nothing cached) — non-JSON text, valid-JSON-missing-a-
   key, and a real `LLMProviderError` — all three degrade to `(None, None)`
   and leave `file.purpose` still `None` in the DB afterward (never
   persisted), unlike group 4 below.
4. `run_well_formed_generation_and_cache()` — a fresh file/repository, one
   well-formed stub response. First call returns the parsed
   `(purpose, explanation)` and persists it (`file.purpose` non-None in the
   DB afterward); a second call against the *same* `file` object, with a
   stub that raises if invoked, proves the cache-hit path picked up the
   just-persisted value with zero further provider calls.

One non-gating live bonus:

5. `run_live_bonus()` — a real file from the already-indexed
   `Nirmit1705/HireSight` repository (reused as-is, matching
   `smoke_assistant_service.py`'s own precedent, rather than re-triggering a
   fresh analysis run) — a real `GroqProvider` call for one file with real
   `RagChunk` rows, printed for a human to eyeball, then a second call
   confirmed to hit the now-persisted DB cache.

Run: `uv run python scripts/smoke_file_insight.py` (from `server/`). Groups
1-4 are fully offline and fast. Group 5 needs a real, already-analyzed
`Nirmit1705/HireSight` repository in the dev DB and makes one real Groq
call; skips cleanly if that repository isn't found.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Real LLM purpose/explanation text (group 5's live bonus) can contain
# Unicode punctuation Windows' default console codepage (cp1252) can't
# encode — same fix every other Phase 8 live-bonus script needed.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.ai.provider import LLMProviderError  # noqa: E402
from app.analysis.chunking import RagChunkType  # noqa: E402
from app.analysis.ingestion import IngestionResult  # noqa: E402
from app.analysis.pipeline import AnalysisResult, analyze_ingested  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunTriggerSource  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.rag_chunk import EMBEDDING_DIM, RagChunk  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import analysis_read_service, file_insight_service  # noqa: E402
from app.services.repo_service import (  # noqa: E402
    create_queued_analysis_run,
    finalize_analysis_run_completed,
    persist_analysis_run,
)
from smoke_storage import (  # noqa: E402
    FIXTURES_DIR,
    _clear_previous_runs,
    _get_or_create_repository,
    _get_or_create_user,
    _load_ingested_files,
)

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"
LIVE_TEST_USER_EMAIL = "john@gmail.com"  # the real, already-GitHub-linked user established in Phase 6.11


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _random_vector() -> list[float]:
    return [random.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)]


class _CountingStubProvider:
    """A fake `LLMProvider` returning a fixed response string (or raising a
    fixed exception), counting how many times `complete` was actually
    invoked — the thing every gating group here needs to prove (a
    short-circuit or a cache hit means the count must stay `0`)."""

    def __init__(self, response_text: str | None = None, *, raises: Exception | None = None):
        self._response_text = response_text
        self._raises = raises
        self.call_count = 0

    def complete(self, *, system: str, messages: list[dict], response_schema: dict | None = None) -> str:
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return self._response_text


def _persist_acyclic_fixture(db: Session, repository: Repository) -> tuple[AnalysisRun, AnalysisResult, dict, dict]:
    """Runs the real pipeline against the small `acyclic` fixture and
    persists it — same technique as `smoke_rag_schema.py`'s own
    `_build_fixture_chunk_and_ids`, returning the full result plus both
    lookup maps instead of resolving just one chunk."""
    root = FIXTURES_DIR / "acyclic"
    files = _load_ingested_files(root, "acyclic")
    ingestion_result = IngestionResult(root_dir=root, files=files, ignored_paths=[])
    commit_sha = "fileinsight000000000000000000000000000000"[:40]
    result = analyze_ingested(ingestion_result, commit_sha=commit_sha)

    _clear_previous_runs(db, repository)
    queued_run = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha, trigger_source=AnalysisRunTriggerSource.MANUAL)
    run, file_id_by_path, symbol_id_by_symbol = persist_analysis_run(
        db, analysis_run=queued_run, analysis_result=result
    )
    run = finalize_analysis_run_completed(db, analysis_run=run, repository=repository)
    return run, result, file_id_by_path, symbol_id_by_symbol


def _seed_rag_chunks_for_file(
    db: Session,
    repository: Repository,
    run: AnalysisRun,
    result: AnalysisResult,
    file_id_by_path: dict,
    symbol_id_by_symbol: dict,
    file_path: str,
) -> None:
    """Inserts every real `RagChunkDraft` targeting `file_path` (in
    practice, its SYMBOL + FILE_OVERVIEW chunks) as real `RagChunk` rows
    with a random vector — grounding data for `get_or_generate_file_insight`
    to read, without needing a real embedding model call."""
    drafts = [c for c in result.rag_chunks if c.file_path == file_path]
    if not drafts:
        raise RuntimeError(f"fixture produced no RagChunkDraft for {file_path!r} — fixture changed?")

    file_id = file_id_by_path[file_path]
    for draft in drafts:
        symbol_id = symbol_id_by_symbol.get(draft.symbol) if draft.symbol is not None else None
        db.add(
            RagChunk(
                repository_id=repository.id,
                analysis_run_id=run.id,
                file_id=file_id,
                symbol_id=symbol_id,
                chunk_type=draft.chunk_type,
                chunk_index=draft.chunk_index,
                chunk_count=draft.chunk_count,
                chunk_text=draft.chunk_text,
                line_start=draft.line_start,
                line_end=draft.line_end,
                module=draft.module,
                chunk_metadata=draft.metadata,
                embedding=_random_vector(),
            )
        )
    db.commit()


def _get_file(db: Session, run: AnalysisRun, path: str) -> File:
    file = db.execute(
        select(File).where(File.analysis_run_id == run.id, File.path == path)
    ).scalar_one_or_none()
    if file is None:
        raise RuntimeError(f"fixture produced no File row for {path!r} — fixture changed?")
    return file


# --- group 1: cache hit ---------------------------------------------------------


def run_cache_hit() -> bool:
    print("=== group 1 (gating): a cache hit returns the cached values with zero provider calls ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-file-insight-cache@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-file-insight-cache", owner="smoke-file-insight", name="cache"
        )
        run, _result, _file_ids, _symbol_ids = _persist_acyclic_fixture(db, repository)
        file = _get_file(db, run, "acyclic/module_x/x.py")

        file.purpose = "Cached purpose from a previous view."
        file.explanation = "Cached explanation from a previous view."
        db.commit()

        stub = _CountingStubProvider(raises=AssertionError("provider must not be called on a cache hit"))
        purpose, explanation = file_insight_service.get_or_generate_file_insight(db, file=file, provider=stub)

        all_passed = True
        all_passed &= _check("returns the exact cached purpose", purpose == "Cached purpose from a previous view.")
        all_passed &= _check(
            "returns the exact cached explanation", explanation == "Cached explanation from a previous view."
        )
        all_passed &= _check("provider.complete was never called", stub.call_count == 0, str(stub.call_count))
        return all_passed
    finally:
        db.close()


# --- group 2: zero-chunk short-circuit -------------------------------------------


def run_zero_chunk_short_circuit() -> bool:
    print("\n=== group 2 (gating): a file with zero RagChunk rows short-circuits to (None, None) ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-file-insight-zerochunk@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-file-insight-zerochunk", owner="smoke-file-insight", name="zerochunk"
        )
        run, _result, _file_ids, _symbol_ids = _persist_acyclic_fixture(db, repository)
        # module_x/__init__.py is a real empty file — zero symbols, so
        # build_rag_chunks never produced a chunk for it. Confirmed no
        # RagChunk row exists for it (nothing seeds one in this group).
        file = _get_file(db, run, "acyclic/module_x/__init__.py")

        stub = _CountingStubProvider(raises=AssertionError("provider must not be called on the zero-chunk path"))
        purpose, explanation = file_insight_service.get_or_generate_file_insight(db, file=file, provider=stub)

        all_passed = True
        all_passed &= _check("purpose is None", purpose is None, repr(purpose))
        all_passed &= _check("explanation is None", explanation is None, repr(explanation))
        all_passed &= _check("provider.complete was never called", stub.call_count == 0, str(stub.call_count))
        return all_passed
    finally:
        db.close()


# --- group 3: malformed responses never persist -----------------------------------


def run_malformed_response_not_persisted() -> bool:
    print("\n=== group 3 (gating): malformed/failed responses degrade to (None, None), never persisted ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-file-insight-malformed@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-file-insight-malformed", owner="smoke-file-insight", name="malformed"
        )
        run, result, file_id_by_path, symbol_id_by_symbol = _persist_acyclic_fixture(db, repository)
        file = _get_file(db, run, "acyclic/module_x/x.py")
        _seed_rag_chunks_for_file(
            db, repository, run, result, file_id_by_path, symbol_id_by_symbol, "acyclic/module_x/x.py"
        )

        all_passed = True

        purpose, explanation = file_insight_service.get_or_generate_file_insight(
            db, file=file, provider=_CountingStubProvider("not valid json at all")
        )
        all_passed &= _check(
            "non-JSON response -> (None, None)", (purpose, explanation) == (None, None), str((purpose, explanation))
        )
        db.refresh(file)
        all_passed &= _check("not persisted after non-JSON response", file.purpose is None)

        purpose, explanation = file_insight_service.get_or_generate_file_insight(
            db, file=file, provider=_CountingStubProvider('{"purpose": "only one field"}')
        )
        all_passed &= _check(
            "valid JSON missing 'explanation' -> (None, None)",
            (purpose, explanation) == (None, None), str((purpose, explanation)),
        )
        db.refresh(file)
        all_passed &= _check("not persisted after wrong-shape response", file.purpose is None)

        purpose, explanation = file_insight_service.get_or_generate_file_insight(
            db, file=file, provider=_CountingStubProvider(raises=LLMProviderError("simulated Groq outage"))
        )
        all_passed &= _check(
            "a real LLMProviderError never propagates -> (None, None)",
            (purpose, explanation) == (None, None), str((purpose, explanation)),
        )
        db.refresh(file)
        all_passed &= _check("not persisted after a provider failure", file.purpose is None)

        return all_passed
    finally:
        db.close()


# --- group 4: well-formed generation persists + caches ----------------------------


def run_well_formed_generation_and_cache() -> bool:
    print("\n=== group 4 (gating): a well-formed response persists; a second call makes zero further calls ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-file-insight-wellformed@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-file-insight-wellformed", owner="smoke-file-insight", name="wellformed"
        )
        run, result, file_id_by_path, symbol_id_by_symbol = _persist_acyclic_fixture(db, repository)
        file = _get_file(db, run, "acyclic/module_x/x.py")
        _seed_rag_chunks_for_file(
            db, repository, run, result, file_id_by_path, symbol_id_by_symbol, "acyclic/module_x/x.py"
        )

        all_passed = True

        well_formed = '{"purpose": "Defines use_y, a small helper function.", "explanation": "This module wraps module_y functionality for callers in module_x."}'
        first_stub = _CountingStubProvider(well_formed)
        purpose, explanation = file_insight_service.get_or_generate_file_insight(db, file=file, provider=first_stub)
        all_passed &= _check(
            "first call returns the parsed purpose", purpose == "Defines use_y, a small helper function.", purpose
        )
        all_passed &= _check(
            "first call returns the parsed explanation",
            explanation == "This module wraps module_y functionality for callers in module_x.",
            explanation,
        )
        all_passed &= _check("first call invoked the provider exactly once", first_stub.call_count == 1)

        db.refresh(file)
        all_passed &= _check("purpose persisted to the DB", file.purpose == purpose)
        all_passed &= _check("explanation persisted to the DB", file.explanation == explanation)

        second_stub = _CountingStubProvider(raises=AssertionError("provider must not be called on the now-cached path"))
        purpose2, explanation2 = file_insight_service.get_or_generate_file_insight(db, file=file, provider=second_stub)
        all_passed &= _check("second call returns the same cached purpose", purpose2 == purpose)
        all_passed &= _check("second call returns the same cached explanation", explanation2 == explanation)
        all_passed &= _check(
            "second call made zero further provider calls", second_stub.call_count == 0, str(second_stub.call_count)
        )

        return all_passed
    finally:
        db.close()


# --- group 5 (non-gating): live bonus ----------------------------------------------


def run_live_bonus() -> bool:
    print(f"\n=== group 5 (non-gating): live file insight against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
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
            run = analysis_read_service.get_latest_completed_run(db, repository.id)
            if run is None:
                print(f"  [SKIP] {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} has no completed analysis run")
                return True

            # Pick a real file that actually owns >=1 RagChunk row (a
            # config/lockfile-style file with none would just exercise the
            # honest zero-chunk path again, already covered by group 2).
            file_id = db.execute(
                select(RagChunk.file_id).where(RagChunk.analysis_run_id == run.id).limit(1)
            ).scalar_one_or_none()
            if file_id is None:
                print(f"  [SKIP] {LIVE_TEST_OWNER}/{LIVE_TEST_REPO}'s latest run has zero RagChunk rows")
                return True
            file = db.get(File, file_id)

            all_passed = True
            purpose, explanation = file_insight_service.get_or_generate_file_insight(db, file=file)
            print(f"\n  file: {file.path}")
            print(f"  purpose: {purpose}")
            print(f"  explanation: {explanation}")
            all_passed &= _check("a real purpose string was generated", isinstance(purpose, str) and len(purpose) > 0)
            all_passed &= _check(
                "a real explanation string was generated", isinstance(explanation, str) and len(explanation) > 0
            )

            db.refresh(file)
            all_passed &= _check("purpose persisted to the real File row", file.purpose == purpose)

            second_stub = _CountingStubProvider(raises=AssertionError("provider must not be called on the cached path"))
            purpose2, explanation2 = file_insight_service.get_or_generate_file_insight(db, file=file, provider=second_stub)
            all_passed &= _check(
                "reselecting the same file makes zero further Groq calls",
                second_stub.call_count == 0 and purpose2 == purpose and explanation2 == explanation,
                str(second_stub.call_count),
            )
            return all_passed
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [SKIP] live bonus unavailable ({type(exc).__name__}: {exc})")
        return True


def main() -> int:
    r1 = run_cache_hit()
    r2 = run_zero_chunk_short_circuit()
    r3 = run_malformed_response_not_persisted()
    r4 = run_well_formed_generation_and_cache()
    r5 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. Cache hit, zero provider calls (gating):            {'PASS' if r1 else 'FAIL'}")
    print(f"2. Zero-chunk short-circuit (gating):                  {'PASS' if r2 else 'FAIL'}")
    print(f"3. Malformed/failed responses never persist (gating):  {'PASS' if r3 else 'FAIL'}")
    print(f"4. Well-formed generation persists + caches (gating):  {'PASS' if r4 else 'FAIL'}")
    print(f"5. Live bonus (non-gating):                            {'PASS' if r5 else 'SKIP/FAIL (excluded from exit code)'}")
    return 0 if (r1 and r2 and r3 and r4) else 1


if __name__ == "__main__":
    sys.exit(main())
