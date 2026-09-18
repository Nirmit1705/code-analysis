"""Hybrid retrieval (Phase 7.6) — the actual payoff of Phase 7: given a free-
text query, search a repository's already-indexed `RagChunk` rows (Phase 7.5)
and return the most relevant ones. No user-facing endpoint yet — Phase 8's
AI assistant is the first real consumer (`docs/PHASE_7_PLAN.md` design
decision #9).

Four independent retrieval signals, merged with Reciprocal Rank Fusion (RRF)
and refined by the existing cross-encoder reranker:
  - vector: pgvector cosine similarity over `RagChunk.embedding`.
  - keyword: Postgres full-text search over `RagChunk.text_search`.
  - symbol: regex-extracted identifier tokens matched against `Symbol.name`.
  - graph: 1-hop `DependencyEdge` neighbors of a symbol/package resolved
    from the query — the only mode that answers "which modules depend on
    X?" or "where is Y used?" style questions grounded in the real
    dependency graph rather than lexical/semantic chunk-text similarity.

**Scoping correctness note**: unlike `RagChunk` (pruned to one generation
per repository by `rag/indexing.py`), `File`/`Symbol`/`DependencyEdge` rows
from old analysis runs are never deleted — this codebase's general "never
delete old rows" convention, for which RagChunk pruning is the one
documented exception. Every query in every mode below is therefore scoped
by `analysis_run_id == get_latest_completed_run(...).id`, not just
`repository_id` — including the `RagChunk` queries themselves, which
matters for a real edge case: an `AnalysisRun` can reach `COMPLETED` with
zero `RagChunk` rows if 7.5's indexing step failed (logged, non-fatal to the
run). Scoping by `analysis_run_id` makes that case compose into a clean
empty result across all four modes, rather than a `repository_id`-only
query silently falling back to a stale prior run's chunks.

`embed_query`/`score_candidates` are imported by name and called bare, not
via `embeddings.embed_query(...)`/`reranker.score_candidates(...)` —
matching `rag/indexing.py`'s own established reasoning: this is what lets
`scripts/smoke_rag_retrieval.py` monkeypatch this module's own bound name.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.analysis.chunking import RagChunkType
from app.analysis.dependencies import DependencyEdgeType
from app.models.dependency import DependencyEdge
from app.models.file import File
from app.models.rag_chunk import RagChunk
from app.models.symbol import Symbol
from app.rag.embeddings import embed_query
from app.rag.reranker import score_candidates
from app.services.analysis_read_service import get_latest_completed_run

# Unvalidated round numbers — flagged, not tuned, same stance as
# `analysis/debt.py`'s thresholds (Known Issue #13) and Phase 7.2/7.3's own
# splitter/HNSW constants. Revisit only if real retrieval quality looks off.
_RRF_K = 60
_MODE_CANDIDATE_LIMIT = 20
_RERANK_POOL_CAP = 30
_MIN_TOKEN_LEN = 3

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Common English words that are ALSO extremely common identifier names
# ("must match a real Symbol.name" alone doesn't filter these out — a repo
# of any real size really does define functions/classes named `run`, `get`,
# `test`, etc.) plus ordinary interrogatives/stopwords from natural-language
# questions. Not exhaustive by design — flagged, not tuned.
_STOPWORDS = {
    "how", "does", "do", "did", "what", "where", "why", "when", "which",
    "who", "is", "are", "was", "were", "the", "a", "an", "of", "in", "on",
    "for", "with", "and", "or", "to", "from", "by", "at", "this", "that",
    "these", "those", "work", "works", "used", "use", "uses", "module",
    "modules", "depend", "depends", "dependency", "dependencies",
    "run", "get", "set", "test", "main", "init", "build", "process",
    "handle", "parse", "load", "save", "update", "check",
}


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieval result — a `RagChunk` plus the resolved context Phase
    8's assistant needs to cite a real source (file/symbol/line) without a
    second query per chunk."""

    chunk_id: uuid.UUID
    chunk_type: RagChunkType
    chunk_text: str
    file_path: str
    symbol_name: str | None
    module: str
    line_start: int
    line_end: int
    score: float
    matched_modes: tuple[str, ...]


@dataclass(frozen=True)
class _Candidate:
    chunk: RagChunk
    rrf_score: float
    matched_modes: tuple[str, ...]


def retrieve(db: Session, *, repository_id: uuid.UUID, query: str, k: int = 10) -> list[RetrievedChunk]:
    """Hybrid search over `repository_id`'s current (latest completed run's)
    `RagChunk` rows. Returns up to `k` results, best first. Returns `[]`
    (never raises) for an empty query, a repository that's never completed
    an analysis, or a repository whose latest run indexed zero chunks —
    each mirrors an existing "not analyzed"/empty-state convention rather
    than fabricating a result."""
    if not query.strip():
        return []

    run = get_latest_completed_run(db, repository_id)
    if run is None:
        return []

    tokens = _extract_identifier_tokens(query)

    mode_results = {
        "vector": _vector_search(db, repository_id, run.id, query),
        "keyword": _keyword_search(db, repository_id, run.id, query),
        "symbol": _symbol_name_search(db, repository_id, run.id, tokens),
        "graph": _graph_lookup(db, repository_id, run.id, tokens),
    }

    fused = _reciprocal_rank_fusion(mode_results)
    if not fused:
        return []

    pool = fused[:_RERANK_POOL_CAP]
    rerank_scores = score_candidates(query, [candidate.chunk.chunk_text for candidate in pool])

    ranked = sorted(zip(pool, rerank_scores), key=lambda pair: (-pair[1], pair[0].chunk.id))
    top = ranked[:k]

    return _build_results(db, top)


def _extract_identifier_tokens(query: str) -> list[str]:
    tokens: set[str] = set()
    for match in _IDENTIFIER_RE.finditer(query):
        token = match.group(0)
        if len(token) < _MIN_TOKEN_LEN or token.lower() in _STOPWORDS:
            continue
        tokens.add(token)
    return sorted(tokens)


def _vector_search(db: Session, repository_id: uuid.UUID, analysis_run_id: uuid.UUID, query: str) -> list[RagChunk]:
    query_vector = embed_query(query)
    return list(
        db.execute(
            select(RagChunk)
            .where(RagChunk.repository_id == repository_id, RagChunk.analysis_run_id == analysis_run_id)
            .order_by(RagChunk.embedding.cosine_distance(query_vector), RagChunk.id)
            .limit(_MODE_CANDIDATE_LIMIT)
        )
        .scalars()
        .all()
    )


def _keyword_search(db: Session, repository_id: uuid.UUID, analysis_run_id: uuid.UUID, query: str) -> list[RagChunk]:
    tsquery = func.plainto_tsquery("english", query)
    return list(
        db.execute(
            select(RagChunk)
            .where(
                RagChunk.repository_id == repository_id,
                RagChunk.analysis_run_id == analysis_run_id,
                RagChunk.text_search.op("@@")(tsquery),
            )
            .order_by(func.ts_rank(RagChunk.text_search, tsquery).desc(), RagChunk.id)
            .limit(_MODE_CANDIDATE_LIMIT)
        )
        .scalars()
        .all()
    )


def _symbol_name_search(
    db: Session, repository_id: uuid.UUID, analysis_run_id: uuid.UUID, tokens: list[str]
) -> list[RagChunk]:
    """Chunks tied to a `Symbol` whose name contains one of the query's
    identifier tokens (substring, not exact match — a query token like
    `authentication` is far more likely to hit a real `AuthenticationService`
    or `authenticate_user` than a symbol literally named `authentication`).
    Joining on `RagChunk.symbol_id` naturally restricts this to SYMBOL
    chunks and any API_ENDPOINT chunk whose handler resolved to a real
    symbol (`chunking.py` sets `symbol=route.handler_symbol`, nullable). No
    extra `Symbol`-side run filter is needed: a `RagChunk` from this run can
    only ever reference a `Symbol` row from that same run (both trace back
    to the same `persist_analysis_run`/`index_analysis_run` pass)."""
    if not tokens:
        return []
    conditions = [func.lower(Symbol.name).like(f"%{token.lower()}%") for token in tokens]
    return list(
        db.execute(
            select(RagChunk)
            .join(Symbol, RagChunk.symbol_id == Symbol.id)
            .where(
                RagChunk.repository_id == repository_id,
                RagChunk.analysis_run_id == analysis_run_id,
                or_(*conditions),
            )
            .order_by(RagChunk.id)
            .limit(_MODE_CANDIDATE_LIMIT)
        )
        .scalars()
        .all()
    )


def _graph_lookup(
    db: Session, repository_id: uuid.UUID, analysis_run_id: uuid.UUID, tokens: list[str]
) -> list[RagChunk]:
    """1-hop `DependencyEdge` neighbors of a symbol/package resolved from the
    query — answers "which modules depend on X?" (internal) and "where is Y
    used?" (external) style questions grounded in the real dependency graph,
    not lexical/semantic chunk-text similarity. Two resolution paths feed one
    shared neighbor set (kept as a single RRF mode — `docs/PHASE_7_PLAN.md`
    design decision #9 locks in exactly four modes)."""
    if not tokens:
        return []

    neighbor_file_ids = _graph_neighbors_via_symbols(db, repository_id, analysis_run_id, tokens)
    neighbor_file_ids |= _graph_neighbors_via_external_package(db, analysis_run_id, tokens)

    if not neighbor_file_ids:
        return []

    return list(
        db.execute(
            select(RagChunk)
            .where(
                RagChunk.repository_id == repository_id,
                RagChunk.analysis_run_id == analysis_run_id,
                RagChunk.file_id.in_(neighbor_file_ids),
            )
            # Not a relevance ranking, just a deterministic one — this
            # mode's rank contributes "presence" to RRF; the final
            # cross-encoder rerank is what actually judges quality among
            # these candidates.
            .order_by(RagChunk.module, RagChunk.chunk_index, RagChunk.id)
            .limit(_MODE_CANDIDATE_LIMIT)
        )
        .scalars()
        .all()
    )


def _graph_neighbors_via_symbols(
    db: Session, repository_id: uuid.UUID, analysis_run_id: uuid.UUID, tokens: list[str]
) -> set[uuid.UUID]:
    conditions = [func.lower(Symbol.name).like(f"%{token.lower()}%") for token in tokens]
    resolved_file_ids = set(
        db.execute(
            select(Symbol.file_id).where(
                Symbol.repository_id == repository_id,
                Symbol.analysis_run_id == analysis_run_id,
                or_(*conditions),
            )
        )
        .scalars()
        .all()
    )
    if not resolved_file_ids:
        return set()

    # Same 1-hop query shape already proven in
    # `app/api/v1/codebase.py::_build_dependencies`/`_build_used_by`,
    # generalized from "one file" to "a resolved set of files" and walked
    # in both directions (a query can ask either "what does X depend on"
    # or "what depends on X").
    neighbor_ids: set[uuid.UUID] = set(
        db.execute(
            select(DependencyEdge.target_file_id).where(
                DependencyEdge.analysis_run_id == analysis_run_id,
                DependencyEdge.source_file_id.in_(resolved_file_ids),
                DependencyEdge.target_file_id.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    neighbor_ids.update(
        db.execute(
            select(DependencyEdge.source_file_id).where(
                DependencyEdge.analysis_run_id == analysis_run_id,
                DependencyEdge.target_file_id.in_(resolved_file_ids),
                DependencyEdge.source_file_id.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    return neighbor_ids


def _graph_neighbors_via_external_package(
    db: Session, analysis_run_id: uuid.UUID, tokens: list[str]
) -> set[uuid.UUID]:
    """Files that import an external package matching a query token (e.g.
    "Where is Redis used?" -> a token `Redis` substring-matching a real
    `target_package_name` like `ioredis`) — grounded in a real import edge,
    not just a text mention."""
    conditions = [func.lower(DependencyEdge.target_package_name).like(f"%{token.lower()}%") for token in tokens]
    return set(
        db.execute(
            select(DependencyEdge.source_file_id).where(
                DependencyEdge.analysis_run_id == analysis_run_id,
                DependencyEdge.edge_type == DependencyEdgeType.EXTERNAL,
                DependencyEdge.source_file_id.is_not(None),
                or_(*conditions),
            )
        )
        .scalars()
        .all()
    )


def _reciprocal_rank_fusion(mode_results: dict[str, list[RagChunk]]) -> list[_Candidate]:
    """Standard RRF: `score += 1 / (_RRF_K + rank)` per mode a chunk appears
    in, 1-based rank within that mode's own result list. Dedup is automatic
    since fusion keys by `chunk.id`."""
    scores: dict[uuid.UUID, float] = {}
    modes_by_id: dict[uuid.UUID, list[str]] = {}
    chunk_by_id: dict[uuid.UUID, RagChunk] = {}

    for mode_name, chunks in mode_results.items():
        for rank, chunk in enumerate(chunks, start=1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
            modes_by_id.setdefault(chunk.id, []).append(mode_name)
            chunk_by_id[chunk.id] = chunk

    ranked_ids = sorted(scores, key=lambda cid: (-scores[cid], cid))
    return [
        _Candidate(chunk=chunk_by_id[cid], rrf_score=scores[cid], matched_modes=tuple(modes_by_id[cid]))
        for cid in ranked_ids
    ]


def _build_results(db: Session, ranked: list[tuple[_Candidate, float]]) -> list[RetrievedChunk]:
    """Resolves `File.path`/`Symbol.name` for only the final ranked rows —
    one batched `IN (...)` query each, same "build an id->value dict" shape
    as `app/api/v1/codebase.py::_build_dependencies`."""
    chunks = [candidate.chunk for candidate, _rerank_score in ranked]
    file_ids = {chunk.file_id for chunk in chunks}
    symbol_ids = {chunk.symbol_id for chunk in chunks if chunk.symbol_id is not None}

    path_by_file_id = dict(db.execute(select(File.id, File.path).where(File.id.in_(file_ids))).all())
    name_by_symbol_id: dict[uuid.UUID, str] = {}
    if symbol_ids:
        name_by_symbol_id = dict(db.execute(select(Symbol.id, Symbol.name).where(Symbol.id.in_(symbol_ids))).all())

    results: list[RetrievedChunk] = []
    for candidate, rerank_score in ranked:
        chunk = candidate.chunk
        results.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                chunk_type=chunk.chunk_type,
                chunk_text=chunk.chunk_text,
                file_path=path_by_file_id[chunk.file_id],
                symbol_name=name_by_symbol_id.get(chunk.symbol_id) if chunk.symbol_id is not None else None,
                module=chunk.module,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                score=rerank_score,
                matched_modes=candidate.matched_modes,
            )
        )
    return results
