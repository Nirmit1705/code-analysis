"""Manual smoke test for Phase 7.3's `RagChunk` table
(`app/models/rag_chunk.py` + Alembic revision `30173cdb9096`).

Not named in `docs/PHASE_7_PLAN.md`'s literal 7.3 file list, but every
sub-phase so far has gotten its own `scripts/smoke_*.py`, and 7.3's own
Definition of Done is explicitly a verification script's job: "a scratch
check inserts/reads back one random 1024-dim vector via a cosine `ORDER BY`
query, proving the column/HNSW index/generated-tsvector round-trip
independent of any real embedding model." Nothing here exercises a real
embedding model or Phase 7.5's indexing orchestration — those don't exist
yet. `embedding`/`text_search` are filled with a random vector / derived by
Postgres itself, not a real model call.

`RagChunk.file_id`/`.symbol_id` are real, non-nullable-where-expected
foreign keys (see the model's own docstring), so a bare scratch insert
can't skip them — this script builds real `Repository`/`AnalysisRun`/
`File`/`Symbol` rows first (reusing `smoke_storage.py`'s existing fixture
helpers against the small `dependency_cycle/acyclic` fixture, which is
real Python source with one real symbol) via the real pipeline, then hangs
one `RagChunk` off the real resolved ids.

Three groups:
1. `run_vector_and_fts_roundtrip()` — inserts two `RagChunk` rows with two
   different random 1024-dim vectors (using a real chunk drafted by
   Phase 7.2's own `build_rag_chunks`, resolved to real `file_id`/
   `symbol_id` via `persist_analysis_run`'s returned lookup maps, same as
   Phase 7.5 will do); a cosine-distance `ORDER BY` querying with row A's
   own embedding must rank row A first at ~0 distance; a full-text search
   for a distinctive real word from the chunk's text must find it via the
   generated `text_search` column.
2. `run_embedding_not_null_enforced()` — confirms the DB actually rejects
   a `RagChunk` insert with `embedding=None` (an `IntegrityError`), proving
   the "no chunk without a vector" invariant from the model's own docstring
   is enforced at the schema level, not just assumed.
3. Cleanup — deletes the fixture `AnalysisRun` (cascades to every child
   row including `rag_chunks`), matching `smoke_storage.py`'s own
   `_clear_previous_runs` convention, so reruns don't accumulate data.

Run: `uv run python scripts/smoke_rag_schema.py` (from `server/`).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.analysis.chunking import RagChunkType  # noqa: E402
from app.analysis.ingestion import IngestionResult  # noqa: E402
from app.analysis.pipeline import analyze_ingested  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.rag_chunk import EMBEDDING_DIM, RagChunk  # noqa: E402
from app.models.repository import Repository  # noqa: E402
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

DOGFOOD_USER_EMAIL = "smoke-rag-schema@local.test"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _random_vector() -> list[float]:
    return [random.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)]


def _build_fixture_chunk_and_ids(db: Session, repository: Repository):
    """Runs the real pipeline against the small `acyclic` fixture (one real
    Python function, `module_x/x.py::use_y`) and persists it, returning a
    real `SYMBOL` `RagChunkDraft` plus its resolved `file_id`/`symbol_id`
    — the exact same resolution Phase 7.5's indexing step will do."""
    root = FIXTURES_DIR / "acyclic"
    files = _load_ingested_files(root, "acyclic")
    ingestion_result = IngestionResult(root_dir=root, files=files, ignored_paths=[])
    commit_sha = "ragschema0000000000000000000000000000000"[:40]
    result = analyze_ingested(ingestion_result, commit_sha=commit_sha)

    _clear_previous_runs(db, repository)
    queued_run = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha)
    run, file_id_by_path, symbol_id_by_symbol = persist_analysis_run(
        db, analysis_run=queued_run, analysis_result=result
    )
    run = finalize_analysis_run_completed(db, analysis_run=run, repository=repository)

    symbol_chunks = [c for c in result.rag_chunks if c.chunk_type is RagChunkType.SYMBOL and c.symbol is not None]
    if not symbol_chunks:
        raise RuntimeError("acyclic fixture produced no SYMBOL chunk — fixture changed?")
    chunk = symbol_chunks[0]

    file_id = file_id_by_path.get(chunk.file_path)
    symbol_id = symbol_id_by_symbol.get(chunk.symbol)
    if file_id is None or symbol_id is None:
        raise RuntimeError("could not resolve file_id/symbol_id for the fixture chunk")

    return run, chunk, file_id, symbol_id


def run_vector_and_fts_roundtrip(db: Session, repository: Repository) -> bool:
    print("=== group 1: vector round-trip (cosine ORDER BY) + generated tsvector/GIN round-trip ===")
    all_passed = True

    run, chunk, file_id, symbol_id = _build_fixture_chunk_and_ids(db, repository)

    vector_a = _random_vector()
    vector_b = _random_vector()

    row_a = RagChunk(
        repository_id=repository.id,
        analysis_run_id=run.id,
        file_id=file_id,
        symbol_id=symbol_id,
        chunk_type=chunk.chunk_type,
        chunk_index=chunk.chunk_index,
        chunk_count=chunk.chunk_count,
        chunk_text=chunk.chunk_text,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
        module=chunk.module,
        chunk_metadata=chunk.metadata,
        embedding=vector_a,
    )
    row_b = RagChunk(
        repository_id=repository.id,
        analysis_run_id=run.id,
        file_id=file_id,
        symbol_id=symbol_id,
        chunk_type=chunk.chunk_type,
        chunk_index=chunk.chunk_index + 1,
        chunk_count=chunk.chunk_count,
        chunk_text=chunk.chunk_text,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
        module=chunk.module,
        chunk_metadata={},
        embedding=vector_b,
    )
    db.add_all([row_a, row_b])
    db.commit()
    db.refresh(row_a)
    db.refresh(row_b)

    all_passed &= _check("RagChunk row A got a generated id", row_a.id is not None)
    all_passed &= _check(
        "chunk_type round-trips as the real enum member", row_a.chunk_type is RagChunkType.SYMBOL,
        f"got {row_a.chunk_type!r}",
    )
    all_passed &= _check("file_id matches the resolved File row", row_a.file_id == file_id)
    all_passed &= _check("symbol_id matches the resolved Symbol row", row_a.symbol_id == symbol_id)
    all_passed &= _check(
        "chunk_metadata JSONB round-trips", row_a.chunk_metadata == chunk.metadata,
        f"{row_a.chunk_metadata!r} vs {chunk.metadata!r}",
    )

    # Cosine-distance ORDER BY, querying with row A's own embedding: row A
    # must come back first, at ~0 distance, ahead of row B's unrelated
    # random vector. This is the real HNSW-indexed query path.
    nearest = db.execute(
        select(RagChunk.id, RagChunk.embedding.cosine_distance(vector_a).label("dist"))
        .where(RagChunk.analysis_run_id == run.id)
        .order_by(RagChunk.embedding.cosine_distance(vector_a))
    ).all()
    all_passed &= _check(
        "cosine ORDER BY ranks the identical-vector row first",
        len(nearest) == 2 and nearest[0].id == row_a.id and nearest[0].dist < 1e-6,
        f"{nearest!r}",
    )
    all_passed &= _check(
        "cosine ORDER BY ranks the unrelated-vector row second",
        len(nearest) == 2 and nearest[1].id == row_b.id and nearest[1].dist > nearest[0].dist,
        f"{nearest!r}",
    )

    # Full-text search: pick a real distinctive token from the chunk's own
    # text (the extracted symbol's name) and confirm the generated
    # `text_search` column + GIN index find it via `@@`.
    needle = chunk.symbol.name  # "use_y"
    fts_hit = db.execute(
        select(RagChunk.id)
        .where(RagChunk.analysis_run_id == run.id)
        .where(RagChunk.text_search.op("@@")(func.plainto_tsquery("english", needle)))
    ).scalars().all()
    all_passed &= _check(
        f"generated tsvector/GIN index finds {needle!r} via full-text search",
        row_a.id in fts_hit and row_b.id in fts_hit,
        f"hits={fts_hit!r}",
    )

    return all_passed


def run_embedding_not_null_enforced(db: Session, repository: Repository) -> bool:
    print("=== group 2: embedding NOT NULL is enforced at the DB level ===")
    all_passed = True

    run, chunk, file_id, symbol_id = _build_fixture_chunk_and_ids(db, repository)

    bad_row = RagChunk(
        repository_id=repository.id,
        analysis_run_id=run.id,
        file_id=file_id,
        symbol_id=symbol_id,
        chunk_type=chunk.chunk_type,
        chunk_index=0,
        chunk_count=1,
        chunk_text=chunk.chunk_text,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
        module=chunk.module,
        chunk_metadata={},
        embedding=None,
    )
    db.add(bad_row)
    raised = False
    try:
        db.commit()
    except IntegrityError:
        raised = True
        db.rollback()
    all_passed &= _check("inserting a RagChunk with embedding=None raises IntegrityError", raised)

    return all_passed


def main() -> int:
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-rag-schema-acyclic", owner="smoke-rag-schema", name="acyclic-fixture"
        )

        r1 = run_vector_and_fts_roundtrip(db, repository)
        r2 = run_embedding_not_null_enforced(db, repository)

        # Cleanup — cascades to every rag_chunks row inserted above.
        _clear_previous_runs(db, repository)
    finally:
        db.close()

    print("\n=== Summary ===")
    print(f"1. vector + tsvector round-trip:  {'PASS' if r1 else 'FAIL'}")
    print(f"2. embedding NOT NULL enforced:   {'PASS' if r2 else 'FAIL'}")
    return 0 if (r1 and r2) else 1


if __name__ == "__main__":
    sys.exit(main())
