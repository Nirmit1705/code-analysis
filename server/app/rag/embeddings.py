"""Local embedding provider (Phase 7.4) — Qwen3-Embedding-0.6B via sentence-transformers.

Lazy module-level singleton model cache, mirroring `app/core/redis.py`'s
`get_redis_client()` pattern: built on first use, not at import time. Not
thread-safe — same accepted tradeoff as
`analysis/treesitter/parser_registry.py`'s `Parser` cache (Known Issue #5),
fine under this codebase's single-process model (Windows Celery
`--pool=solo`, sync FastAPI).

The model's own shipped "query" prompt (`config_sentence_transformers.json`
on the Hub) is literally "Given a web search query, retrieve relevant
passages that answer the query" — the wrong domain for a codebase-question
RAG system. `embed_query` overrides it via `prompt=` with a codebase-specific
instruction, reusing the same `Instruct: ...\nQuery:` template the model
expects rather than the built-in `prompt_name="query"` default.

**Device (Phase 7.5 addendum):** uses CUDA when `torch.cuda.is_available()`,
CPU otherwise. `pyproject.toml`'s `torch` source moved from the CPU-only
wheel index to a CUDA build (`cu124`) in the same sub-phase for the same
reason — real, measured CPU-only fp32 inference over a real repo's full
chunk set (~1,400 for `Nirmit1705/HireSight`) took 30+ minutes on this
project's own dev machine, impractical for a live demo. A CUDA build still
runs correctly on a CPU-only machine (`torch.cuda.is_available()` is simply
`False` there); the deliberate portability tradeoff is a larger one-time
`torch` download (~2.4GB vs. the CPU wheel), accepted because indexing
speed matters more here than install size.
"""

from functools import lru_cache

import torch
from sentence_transformers import SentenceTransformer

from app.core.config import get_settings

_QUERY_INSTRUCTION = (
    "Instruct: Given a question about a software codebase, retrieve relevant "
    "code, API routes, or documentation that answer the question\nQuery:"
)


def _resolve_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def _resolve_dtype(device: str) -> torch.dtype:
    """fp16 on CUDA only — CPU fp16 kernels are slow/partially unsupported
    for many ops in PyTorch, so fp32 stays the CPU default. Trial run
    (2026-09-21, per `docs/PHASE_8_PLAN.md`'s 8.5 write-up): fp32 weights for
    both this model and the reranker together (~4.8GB) left almost no
    headroom on a 6GB laptop GPU, causing severe thrashing (150-220s for a
    single chat question that should take a few seconds) and, before a
    separate defensive fix, occasional outright CUDA OOM crashes. fp16
    roughly halves combined weight memory (~2.4GB)."""
    return torch.float16 if device == "cuda" else torch.float32


@lru_cache
def _get_model() -> SentenceTransformer:
    settings = get_settings()
    if settings.EMBEDDING_PROVIDER != "local":
        raise RuntimeError(
            f"EMBEDDING_PROVIDER={settings.EMBEDDING_PROVIDER!r} is not supported — "
            "only 'local' is implemented (app/rag/embeddings.py)"
        )
    device = _resolve_device()
    return SentenceTransformer(
        settings.EMBEDDING_MODEL,
        device=device,
        model_kwargs={"torch_dtype": _resolve_dtype(device)},
        processor_kwargs={"padding_side": "left"},
    )


def _assert_dim(actual_dim: int) -> None:
    expected = get_settings().EMBEDDING_DIM
    if actual_dim != expected:
        raise RuntimeError(
            f"embedding model produced {actual_dim}-dim vectors but "
            f"settings.EMBEDDING_DIM={expected} — check app/core/config.py and "
            "app/models/rag_chunk.py::EMBEDDING_DIM agree"
        )


def embed_query(text: str) -> list[float]:
    """Embed a single user query, with the codebase-domain instruction prefix applied."""
    vector = _get_model().encode(text, prompt=_QUERY_INSTRUCTION, normalize_embeddings=True)
    _assert_dim(len(vector))
    return vector.tolist()


def embed_documents(texts: list[str], *, batch_size: int = 32) -> list[list[float]]:
    """Embed chunk texts (no instruction prefix — documents are embedded plain)."""
    if not texts:
        return []
    vectors = _get_model().encode(texts, batch_size=batch_size, normalize_embeddings=True)
    _assert_dim(vectors.shape[1])
    return vectors.tolist()
