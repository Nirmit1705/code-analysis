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
"""

from functools import lru_cache

from sentence_transformers import CrossEncoder

from app.core.config import get_settings

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


@lru_cache
def _get_model() -> CrossEncoder:
    settings = get_settings()
    if settings.RERANKER_PROVIDER != "local":
        raise RuntimeError(
            f"RERANKER_PROVIDER={settings.RERANKER_PROVIDER!r} is not supported — "
            "only 'local' is implemented (app/rag/reranker.py)"
        )
    return CrossEncoder(
        settings.RERANKER_MODEL,
        device="cpu",
        processor_kwargs={"padding_side": "left"},
    )


def _format_query(query: str) -> str:
    return f"{_QUERY_PREFIX}<Instruct>: {_TASK_INSTRUCTION}\n<Query>: {query}\n"


def _format_document(document: str) -> str:
    return f"<Document>: {document}{_DOCUMENT_SUFFIX}"


def score_candidates(query: str, candidates: list[str]) -> list[float]:
    """Score each candidate's relevance to `query`. Parallel to `candidates`, sigmoid in [0, 1]."""
    if not candidates:
        return []
    pairs = [[_format_query(query), _format_document(doc)] for doc in candidates]
    scores = _get_model().predict(pairs).tolist()
    assert len(scores) == len(candidates)
    return scores
