"""Manual smoke test for Phase 7.6's hybrid retrieval
(`app/rag/retrieval.py::retrieve`).

`app.rag.retrieval.embed_query`/`.score_candidates` (the names `retrieve()`
calls bare, imported via `from app.rag.embeddings import embed_query` /
`from app.rag.reranker import score_candidates`) are what every gating group
below monkeypatches — patching the origin modules
(`app.rag.embeddings.embed_query`/`app.rag.reranker.score_candidates`)
instead would silently do nothing, since `retrieval.py` already bound its
own module-level names to the original function objects at import time. Same
technique `smoke_rag_indexing.py`/`smoke_rag_providers.py` already use.

One shared hand-built fixture (direct ORM inserts, not a full pipeline run
— this needs precise control over embeddings/text/symbol-links/dependency-
edges per mode that a real `analyze_ingested` pass can't give):
  - `vector_target.py` — a FILE_OVERVIEW chunk whose stored embedding is
    bit-identical to a fixed vector the stubbed `embed_query` always
    returns; findable only by cosine similarity.
  - 25 `filler.py` FILE_OVERVIEW chunks with embeddings perturbed only
    slightly from that same fixed vector — without these, this fixture's
    total chunk count (5) would sit *under* `_MODE_CANDIDATE_LIMIT` (20),
    so vector search's real top-N cutoff would never actually exclude
    anything and every mode-necessity check below would be meaningless.
    With them, only genuinely close vectors make vector search's top 20.
  - `keyword_target.py` — a FILE_OVERVIEW chunk containing a nonsense,
    globally-unique word (`zzflurbnickel`) findable only via full-text
    search, with an unrelated random embedding.
  - `auth/symbol_target.py` — owns a real `Symbol` named
    `AuthenticationService`; its SYMBOL chunk is findable via substring
    symbol-name matching, with an unrelated random embedding.
  - `graph/graph_target.py` — a FILE_OVERVIEW chunk with no direct lexical/
    embedding/symbol-name overlap with any test query; reachable only by
    walking one `DependencyEdge` hop from `auth/symbol_target.py`.
  - `services/redis_client.py` — a FILE_OVERVIEW chunk reachable only via
    graph-lookup's *external*-package resolution path (an EXTERNAL
    `DependencyEdge` with `target_package_name="ioredis"`).

Seven groups:

1-4 (gating). Each of the four modes is proven individually *necessary* by
   ablation: with all four modes active, that mode's own target chunk is
   found (`k=20`, comfortably above this fixture's total distinct chunk
   count, so presence/absence — not ranking — is what's being checked);
   with just that one mode's private search function monkeypatched to
   always return `[]` (the other three left real), the same target chunk
   disappears. Group 4 (graph) checks both its internal-neighbor and
   external-package resolution paths.
5 (gating). `_reciprocal_rank_fusion` is called directly (no DB/model
   involved) and proven to rank a chunk appearing at rank 2 in two modes
   above a chunk that's rank 1 in only one — computed exactly from the
   module's own `_RRF_K` constant, not a hand-guessed number.
6 (gating). A repository with no completed analysis run returns `[]`
   immediately, without ever calling a (poisoned, exception-raising)
   `embed_query` stub — proving the early-return actually short-circuits
   before any model call.
7 (non-gating live bonus). A real, freshly-triggered `Nirmit1705/HireSight`
   analysis (this codebase's established sample repo) via
   `run_repository_analysis.delay()` in Celery eager mode, then real
   `retrieve()` calls with the real local models against the Phase 7
   Definition of Done's own example questions — printed for a human to
   eyeball, not hard-asserted (real repo content and model behavior drift
   over time).

Run: `uv run python scripts/smoke_rag_retrieval.py` (from `server/`). Groups
1-6 are offline and fast. Group 7 needs the real local models (already
downloaded from 7.4/7.5 dogfooding) and a live GitHub App installation for
`Nirmit1705/HireSight` — skips cleanly if that installation isn't found.
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

import app.workers.tasks.analysis_tasks as analysis_tasks  # noqa: E402
from app.analysis.chunking import RagChunkType  # noqa: E402
from app.analysis.dependencies import DependencyEdgeType  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.symbols import SymbolKind  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus, AnalysisRunTriggerSource  # noqa: E402
from app.models.dependency import DependencyEdge  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.rag_chunk import EMBEDDING_DIM, RagChunk  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.symbol import Symbol  # noqa: E402
from app.models.user import User  # noqa: E402
from app.rag import retrieval  # noqa: E402
from app.services import github_service  # noqa: E402
from app.services.repo_service import create_queued_analysis_run  # noqa: E402
from app.workers.celery_app import celery_app  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

DOGFOOD_USER_EMAIL = "smoke-rag-retrieval@local.test"

# This codebase's established Phase 6+ sample repo (server/CLAUDE.md) — also
# Phase 7.2/7.4/7.5's own live-bonus target.
LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"

# The Phase 7 Definition of Done's own example questions
# (`docs/BACKEND_IMPLEMENTATION_PLAN.md`'s Phase 7 section / the backend
# guide's Phase 8 example list).
LIVE_QUESTIONS = [
    "How does authentication work?",
    "Where is Redis used?",
    "Which modules depend on the main application entrypoint?",
]

_NUM_FILLER_CHUNKS = 25


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


class _Patch:
    """Tiny monkeypatch-and-restore helper — same shape as every other
    smoke script's own local copy (`smoke_rag_indexing.py`,
    `smoke_rag_providers.py`); duplicated rather than imported, matching
    this codebase's established small-helper convention."""

    def __init__(self, obj, attr: str, replacement):
        self._obj = obj
        self._attr = attr
        self._original = getattr(obj, attr)
        setattr(obj, attr, replacement)

    def restore(self) -> None:
        setattr(self._obj, self._attr, self._original)


def _random_vector() -> list[float]:
    return [random.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)]


VECTOR_TARGET_EMBEDDING = _random_vector()


def _perturbed_vector(base: list[float], magnitude: float = 0.05) -> list[float]:
    """A vector very close to `base` by cosine similarity — used for filler
    chunks that must dominate vector search's real top-N cutoff without
    being bit-identical to it."""
    return [v + random.uniform(-magnitude, magnitude) for v in base]


def _stub_score_candidates(query: str, candidates: list[str]) -> list[float]:
    """Constant reranker stub — presence in the final top-k is what these
    tests check, not ranking, so a tie-everything score is sufficient and
    avoids loading the real cross-encoder model."""
    return [1.0] * len(candidates)


# --- fixture construction ------------------------------------------------


def _build_fixture(db: Session, repository: Repository) -> dict[str, object]:
    """One hand-built, COMPLETED `AnalysisRun` with everything the four
    retrieval modes need to be tested in isolation. Direct ORM inserts
    (not a full pipeline run) — this needs precise control over
    embeddings/text/symbol-links/dependency-edges a real `analyze_ingested`
    pass over real source can't give."""
    _clear_previous_runs(db, repository)

    run = AnalysisRun(
        repository_id=repository.id,
        branch="main",
        commit_sha="ragretrieval0000000000000000000000000000"[:40],
        status=AnalysisRunStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()

    def _make_file(path: str, module: str) -> File:
        f = File(
            repository_id=repository.id,
            analysis_run_id=run.id,
            path=path,
            language=Language.PYTHON,
            size_bytes=100,
            module=module,
            loc=10,
            last_analyzed_commit_sha=run.commit_sha,
        )
        db.add(f)
        return f

    file_vector = _make_file("vector_target.py", ".")
    file_filler = _make_file("filler.py", ".")
    file_keyword = _make_file("keyword_target.py", ".")
    file_symbol = _make_file("auth/symbol_target.py", "auth")
    file_graph = _make_file("graph/graph_target.py", "graph")
    file_external = _make_file("services/redis_client.py", "services")
    db.commit()

    symbol = Symbol(
        repository_id=repository.id,
        file_id=file_symbol.id,
        analysis_run_id=run.id,
        name="AuthenticationService",
        kind=SymbolKind.CLASS,
        line_start=1,
        line_end=5,
        module="auth",
        language=Language.PYTHON,
        has_docstring=False,
    )
    db.add(symbol)
    db.commit()

    # F3 (symbol_target.py) --INTERNAL--> F4 (graph_target.py): a query
    # resolving F3 via its Symbol should reach F4 as a 1-hop neighbor.
    edge_internal = DependencyEdge(
        repository_id=repository.id,
        analysis_run_id=run.id,
        source_file_id=file_symbol.id,
        target_file_id=file_graph.id,
        edge_type=DependencyEdgeType.INTERNAL,
    )
    # F5 (redis_client.py) --EXTERNAL--> "ioredis": a query mentioning
    # "Redis" should resolve F5 via the package name, not a symbol.
    edge_external = DependencyEdge(
        repository_id=repository.id,
        analysis_run_id=run.id,
        source_file_id=file_external.id,
        target_file_id=None,
        target_package_name="ioredis",
        edge_type=DependencyEdgeType.EXTERNAL,
    )
    db.add_all([edge_internal, edge_external])
    db.commit()

    def _make_chunk(*, file: File, text: str, embedding: list[float], symbol_id=None) -> RagChunk:
        chunk = RagChunk(
            repository_id=repository.id,
            analysis_run_id=run.id,
            file_id=file.id,
            symbol_id=symbol_id,
            chunk_type=RagChunkType.SYMBOL if symbol_id is not None else RagChunkType.FILE_OVERVIEW,
            chunk_index=0,
            chunk_count=1,
            chunk_text=text,
            line_start=1,
            line_end=5,
            module=file.module,
            embedding=embedding,
        )
        db.add(chunk)
        return chunk

    chunk_vector = _make_chunk(
        file=file_vector,
        text="[FILE_OVERVIEW] vector_target.py\nGeneric utility code, nothing lexically distinctive.",
        embedding=VECTOR_TARGET_EMBEDDING,
    )
    chunk_keyword = _make_chunk(
        file=file_keyword,
        text="[FILE_OVERVIEW] keyword_target.py\nThis handles zzflurbnickel processing for the widget pipeline.",
        embedding=_random_vector(),
    )
    chunk_symbol = _make_chunk(
        file=file_symbol,
        text="[CLASS] AuthenticationService\nfile: auth/symbol_target.py\nmodule: auth",
        embedding=_random_vector(),
        symbol_id=symbol.id,
    )
    chunk_graph = _make_chunk(
        file=file_graph,
        text="[FILE_OVERVIEW] graph/graph_target.py\nLow-level networking helper code, nothing distinctive.",
        embedding=_random_vector(),
    )
    chunk_external = _make_chunk(
        file=file_external,
        text="[FILE_OVERVIEW] services/redis_client.py\nConnects to the cache backend, nothing distinctive.",
        embedding=_random_vector(),
    )
    for i in range(_NUM_FILLER_CHUNKS):
        _make_chunk(
            file=file_filler,
            text=f"[FILE_OVERVIEW] filler.py\nGeneric filler content #{i}, nothing distinctive.",
            embedding=_perturbed_vector(VECTOR_TARGET_EMBEDDING),
        )
    db.commit()

    return {
        "run": run,
        "vector": chunk_vector,
        "keyword": chunk_keyword,
        "symbol": chunk_symbol,
        "graph": chunk_graph,
        "external": chunk_external,
    }


def _found(db: Session, repository_id, query: str, target_id, *, ablate: str | None) -> bool:
    """Runs `retrieve()` with the vector-embedding and reranker models
    stubbed out, optionally forcing one mode's private search function to
    always return `[]`. Returns whether `target_id` appears anywhere in
    the result.

    `k=_RERANK_POOL_CAP` (not some smaller number): the constant-score
    reranker stub ties every candidate at the same score, so the final
    sort's only tiebreaker is `chunk_id` (an arbitrary UUID). With vector
    search alone already contributing ~20 filler chunks to every query's
    fused pool, a smaller `k` would silently truncate before the real
    target purely by UUID-ordering luck — `k` must cover the whole capped
    pool for "found" to mean "present in the fused candidates," not
    "happened to win an arbitrary tiebreak."
    """
    patches = [
        _Patch(retrieval, "embed_query", lambda text: VECTOR_TARGET_EMBEDDING),
        _Patch(retrieval, "score_candidates", _stub_score_candidates),
    ]
    if ablate is not None:
        patches.append(_Patch(retrieval, ablate, lambda *args, **kwargs: []))
    try:
        results = retrieval.retrieve(
            db, repository_id=repository_id, query=query, k=retrieval._RERANK_POOL_CAP
        )
    finally:
        for patch in patches:
            patch.restore()
    return any(r.chunk_id == target_id for r in results)


# --- groups 1-4: each mode is individually necessary ----------------------


def run_vector_mode_necessary(db: Session, repository: Repository, fixture: dict) -> bool:
    print("=== group 1 (gating): vector mode is individually necessary ===")
    query = "irrelevant query text — only the fixed embedding stub makes this findable"
    target = fixture["vector"].id
    ok = True
    ok &= _check("found with all four modes active", _found(db, repository.id, query, target, ablate=None))
    ok &= _check(
        "absent when vector search is ablated", not _found(db, repository.id, query, target, ablate="_vector_search")
    )
    return ok


def run_keyword_mode_necessary(db: Session, repository: Repository, fixture: dict) -> bool:
    print("\n=== group 2 (gating): keyword mode is individually necessary ===")
    query = "What handles zzflurbnickel processing?"
    target = fixture["keyword"].id
    ok = True
    ok &= _check("found with all four modes active", _found(db, repository.id, query, target, ablate=None))
    ok &= _check(
        "absent when keyword search is ablated",
        not _found(db, repository.id, query, target, ablate="_keyword_search"),
    )
    return ok


def run_symbol_mode_necessary(db: Session, repository: Repository, fixture: dict) -> bool:
    print("\n=== group 3 (gating): symbol-name mode is individually necessary ===")
    query = "How does AuthenticationService work?"
    target = fixture["symbol"].id
    ok = True
    ok &= _check("found with all four modes active", _found(db, repository.id, query, target, ablate=None))
    ok &= _check(
        "absent when symbol-name search is ablated",
        not _found(db, repository.id, query, target, ablate="_symbol_name_search"),
    )
    return ok


def run_graph_mode_necessary(db: Session, repository: Repository, fixture: dict) -> bool:
    print("\n=== group 4 (gating): graph-lookup mode is individually necessary (internal + external paths) ===")
    ok = True

    query_internal = "How does AuthenticationService work?"
    target_internal = fixture["graph"].id
    ok &= _check(
        "internal-neighbor path: found with all four modes active",
        _found(db, repository.id, query_internal, target_internal, ablate=None),
    )
    ok &= _check(
        "internal-neighbor path: absent when graph lookup is ablated",
        not _found(db, repository.id, query_internal, target_internal, ablate="_graph_lookup"),
    )

    query_external = "Where is Redis used?"
    target_external = fixture["external"].id
    ok &= _check(
        "external-package path: found with all four modes active",
        _found(db, repository.id, query_external, target_external, ablate=None),
    )
    ok &= _check(
        "external-package path: absent when graph lookup is ablated",
        not _found(db, repository.id, query_external, target_external, ablate="_graph_lookup"),
    )

    return ok


# --- group 5: RRF fusion, unit-tested directly ----------------------------


def run_rrf_boost(fixture: dict) -> bool:
    print("\n=== group 5 (gating): RRF correctly boosts a multi-mode hit above a single-mode top rank ===")

    chunk_x = fixture["keyword"]  # will appear in two modes at rank 2
    chunk_y = fixture["symbol"]  # will appear in one mode at rank 1
    filler = fixture["graph"]  # rank-1 filler for the modes chunk_x is rank-2 in

    mode_results = {
        "vector": [chunk_y],
        "keyword": [filler, chunk_x],
        "symbol": [filler, chunk_x],
        "graph": [],
    }
    fused = retrieval._reciprocal_rank_fusion(mode_results)
    by_id = {candidate.chunk.id: candidate for candidate in fused}
    order = list(by_id)

    expected_x = 2 * (1.0 / (retrieval._RRF_K + 2))
    expected_y = 1.0 / (retrieval._RRF_K + 1)

    ok = True
    ok &= _check(
        "multi-mode chunk's fused score matches the hand-computed RRF sum",
        chunk_x.id in by_id and abs(by_id[chunk_x.id].rrf_score - expected_x) < 1e-9,
        f"{by_id.get(chunk_x.id)!r}",
    )
    ok &= _check(
        "single-mode chunk's fused score matches its rank-1 contribution",
        chunk_y.id in by_id and abs(by_id[chunk_y.id].rrf_score - expected_y) < 1e-9,
        f"{by_id.get(chunk_y.id)!r}",
    )
    ok &= _check(
        f"multi-mode chunk ({expected_x:.5f}) outranks the single-mode rank-1 chunk ({expected_y:.5f})",
        expected_x > expected_y and order.index(chunk_x.id) < order.index(chunk_y.id),
    )
    return ok


# --- group 6: empty repository short-circuits -----------------------------


def run_empty_repository_returns_empty(db: Session, user: User) -> bool:
    print("\n=== group 6 (gating): a repository with no completed analysis run returns [] without any model call ===")
    repository = _get_or_create_repository(
        db, user, github_repo_id="smoke-rag-retrieval-empty", owner="smoke-rag-retrieval", name="empty-fixture"
    )
    # Deliberately no AnalysisRun at all for this repository.

    def _poison(*args, **kwargs):
        raise AssertionError("embed_query should never be called when there's no completed analysis run")

    patch = _Patch(retrieval, "embed_query", _poison)
    try:
        results = retrieval.retrieve(db, repository_id=repository.id, query="anything at all", k=5)
    finally:
        patch.restore()

    return _check("retrieve() returns [] for a never-analyzed repository", results == [], str(results))


# --- group 7 (non-gating): live HireSight bonus, real models --------------


def run_live_bonus() -> bool:
    print(
        f"\n=== group 7 (non-gating): live retrieve() against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} "
        "— real models, real questions ==="
    )
    original_eager = celery_app.conf.task_always_eager
    try:
        installations = github_service.list_installations()
        installation = next(
            (i for i in installations if i["account"]["login"].lower() == LIVE_TEST_OWNER.lower()), None
        )
        if installation is None:
            print(f"  [SKIP] no installation found for account {LIVE_TEST_OWNER!r}")
            return True

        token_data = github_service.get_installation_access_token(str(installation["id"]))
        token = token_data["token"]
        branch_info = github_service.get_branch(token, LIVE_TEST_OWNER, LIVE_TEST_REPO, "main")
        commit_sha = branch_info["commit"]["sha"]

        db = SessionLocal()
        try:
            user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
            repository = _get_or_create_repository(
                db,
                user,
                github_repo_id=f"smoke-rag-retrieval-live-{LIVE_TEST_OWNER}-{LIVE_TEST_REPO}".lower(),
                owner=LIVE_TEST_OWNER,
                name=LIVE_TEST_REPO,
            )
            repository.installation_id = str(installation["id"])
            db.add(repository)
            db.commit()

            _clear_previous_runs(db, repository)
            queued = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha, trigger_source=AnalysisRunTriggerSource.MANUAL)

            celery_app.conf.task_always_eager = True
            async_result = analysis_tasks.run_repository_analysis.delay(str(queued.id))
            # Generous timeout: a full analysis + real embedding pass over
            # HireSight's real chunk set.
            result = async_result.get(timeout=900)

            db.refresh(queued)
            all_passed = True
            all_passed &= _check("task returns status completed", result.get("status") == "completed", str(result))
            all_passed &= _check("AnalysisRun.status == COMPLETED", queued.status is AnalysisRunStatus.COMPLETED)

            for question in LIVE_QUESTIONS:
                results = retrieval.retrieve(db, repository_id=repository.id, query=question, k=5)
                print(f"\n  Q: {question}")
                if not results:
                    print("    (no results)")
                for r in results:
                    location = f"{r.file_path}:{r.line_start}-{r.line_end}"
                    symbol_part = f" [{r.symbol_name}]" if r.symbol_name else ""
                    print(
                        f"    score={r.score:.3f} modes={r.matched_modes} "
                        f"{location}{symbol_part} ({r.chunk_type.value})"
                    )
                all_passed &= _check(f"{question!r} returned at least one result", len(results) > 0)

            return all_passed
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [ERROR] live bonus failed: {exc!r}")
        return False
    finally:
        celery_app.conf.task_always_eager = original_eager


def main() -> int:
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-rag-retrieval-fixture", owner="smoke-rag-retrieval", name="fixture"
        )
        fixture = _build_fixture(db, repository)

        r1 = run_vector_mode_necessary(db, repository, fixture)
        r2 = run_keyword_mode_necessary(db, repository, fixture)
        r3 = run_symbol_mode_necessary(db, repository, fixture)
        r4 = run_graph_mode_necessary(db, repository, fixture)
        r5 = run_rrf_boost(fixture)
        r6 = run_empty_repository_returns_empty(db, user)

        _clear_previous_runs(db, repository)
    finally:
        db.close()

    r7 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. Vector mode necessary:              {'PASS' if r1 else 'FAIL'}")
    print(f"2. Keyword mode necessary:              {'PASS' if r2 else 'FAIL'}")
    print(f"3. Symbol-name mode necessary:          {'PASS' if r3 else 'FAIL'}")
    print(f"4. Graph-lookup mode necessary:         {'PASS' if r4 else 'FAIL'}")
    print(f"5. RRF boosts a multi-mode hit:         {'PASS' if r5 else 'FAIL'}")
    print(f"6. Empty repository short-circuits:     {'PASS' if r6 else 'FAIL'}")
    print(f"7. Live bonus (non-gating):             {'PASS' if r7 else 'FAIL (see above — may be a network/credentials issue)'}")
    return 0 if (r1 and r2 and r3 and r4 and r5 and r6) else 1


if __name__ == "__main__":
    sys.exit(main())
