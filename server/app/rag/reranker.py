"""Local reranker provider (Phase 7.4) — Qwen3-Reranker-0.6B via the
community `tomaarsen/Qwen3-Reranker-0.6B-seq-cls` conversion (standard
sigmoid classification head), loaded through sentence_transformers.CrossEncoder.

Same lazy-singleton / not-thread-safe tradeoff as `rag/embeddings.py` and
`analysis/treesitter/parser_registry.py` (Known Issue #5).

Query/document formatting follows the exact chat-style template documented
on the model card — the underlying Qwen3 causal-LM backbone was fine-tuned
against this specific prompt shape, so it isn't optional decoration. The
`task` instruction is overridden from the card's "web search query" default
to a codebase-appropriate one, for the same reason as
`rag/embeddings.py`'s query instruction override.

**Device (Phase 7.5 addendum):** uses CUDA when available, CPU otherwise —
same `_resolve_device()` helper and rationale as `rag/embeddings.py`
(duplicated, not imported, matching this codebase's established small-helper
convention).

**OOM fallback (Phase 8.5 post-signoff addendum):** on a 6GB-VRAM laptop GPU,
running both this model and `rag/embeddings.py`'s alongside real inference
activations genuinely exhausts CUDA memory — reproduced live, not
hypothetical (see `docs/PHASE_8_PLAN.md`'s 8.5 write-up). `score_candidates`
clears the CUDA cache and retries once; if it still OOMs, it degrades to a
rank-preserving fallback instead of raising, same "never raise, degrade
honestly" stance as `assistant_service.py`'s malformed-response fallback and
`insight_service.py`'s `LLMProviderError` handling. **Caught broader than
`torch.cuda.OutOfMemoryError`, found live**: PyTorch only raises that specific
subclass when its own caching allocator fails to reserve memory up front —
an OOM surfacing *inside* a kernel launch (observed live inside
`apply_rotary_pos_emb`'s elementwise ops, mid-forward-pass) instead raises a
bare `RuntimeError("CUDA error: out of memory")`, which `except
torch.cuda.OutOfMemoryError` does not catch. `_is_cuda_oom` matches both.
This fallback stays in place even after the fp16 change below — fp16 makes
OOM much less likely, not impossible.

**fp16 (same day, post-OOM-fix)**: fp32 weights for this model plus
`rag/embeddings.py`'s together (~4.8GB) left so little headroom that even a
successful (non-OOM) call thrashed severely — 150-220s wall-clock for one
chat question that should take a few seconds, measured live against real
`Nirmit1705/HireSight` questions. Loading both models in fp16 instead
(`_resolve_dtype`, CUDA-only) roughly halves that combined footprint,
confirmed live: the same two questions dropped to 63.5s (cold) and 13.1s
(warm), zero OOM warnings, same real files cited. See `docs/PHASE_8_PLAN.md`'s
8.5 write-up and Known Issue #23 in `docs/BACKEND_IMPLEMENTATION_PLAN.md`
for the full comparison.
"""

from functools import lru_cache
import logging

import torch
from sentence_transformers import CrossEncoder

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TASK_INSTRUCTION = (
    "Given a question about a software codebase, retrieve relevant code, API "
    "routes, or documentation that answer the question"
)

_QUERY_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements "
    'based on the Query and the Instruct provided. Note that the answer can '
    'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
_DOCUMENT_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def _resolve_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def _resolve_dtype(device: str) -> torch.dtype:
    """fp16 on CUDA only, same rationale as `rag/embeddings.py::_resolve_dtype`
    (duplicated, not imported — this codebase's established small-helper
    convention) — this model is the one that was actually observed OOMing."""
    return torch.float16 if device == "cuda" else torch.float32


def _is_cuda_oom(exc: RuntimeError) -> bool:
    return isinstance(exc, torch.cuda.OutOfMemoryError) or "out of memory" in str(exc).lower()


@lru_cache
def _get_model() -> CrossEncoder:
    settings = get_settings()
    if settings.RERANKER_PROVIDER != "local":
        raise RuntimeError(
            f"RERANKER_PROVIDER={settings.RERANKER_PROVIDER!r} is not supported — "
            "only 'local' is implemented (app/rag/reranker.py)"
        )
    device = _resolve_device()
    return CrossEncoder(
        settings.RERANKER_MODEL,
        device=device,
        model_kwargs={"torch_dtype": _resolve_dtype(device)},
        processor_kwargs={"padding_side": "left"},
    )


def _format_query(query: str) -> str:
    return f"{_QUERY_PREFIX}<Instruct>: {_TASK_INSTRUCTION}\n<Query>: {query}\n"


def _format_document(document: str) -> str:
    return f"<Document>: {document}{_DOCUMENT_SUFFIX}"


def score_candidates(query: str, candidates: list[str]) -> list[float]:
    """Score each candidate's relevance to `query`. Parallel to `candidates`,
    sigmoid in [0, 1] on success. On CUDA OOM, clears the cache and retries
    once; if it OOMs again, skips reranking for this call and returns a
    rank-preserving fallback (descending placeholder scores in `candidates`'
    own input order) instead of raising — `candidates` arrives already
    RRF-ranked by `retrieval.py`, so this degrades to "no rerank," never to
    a crashed request."""
    if not candidates:
        return []
    pairs = [[_format_query(query), _format_document(doc)] for doc in candidates]
    try:
        scores = _get_model().predict(pairs).tolist()
    except RuntimeError as exc:
        if not _is_cuda_oom(exc):
            raise
        logger.warning("Reranker CUDA OOM — clearing cache and retrying once")
        torch.cuda.empty_cache()
        try:
            scores = _get_model().predict(pairs).tolist()
        except RuntimeError as retry_exc:
            if not _is_cuda_oom(retry_exc):
                raise
            logger.warning("Reranker CUDA OOM again after retry — skipping rerank for this call")
            torch.cuda.empty_cache()
            return [1.0 - i / len(candidates) for i in range(len(candidates))]
    assert len(scores) == len(candidates)
    return scores
