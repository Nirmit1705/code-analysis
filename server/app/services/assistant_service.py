"""Grounded Q&A orchestration + suggested-questions heuristic (Phase 8.2) —
the actual "ask a question, get a grounded answer with real citations"
function `app/api/v1/assistant.py` (Phase 8.3) will sit on top of.

`answer_question` is provider-agnostic at the call site: it takes an
`LLMProvider` (defaulting to a real `GroqProvider()` if none is injected) so
gating tests can substitute a stub without touching the real network,
mirroring how `rag/indexing.py`/`rag/retrieval.py` already take
`embed_query`/`score_candidates` as swappable module-level names rather than
hard-coding a concrete implementation inline.

Two anti-hallucination guarantees enforced here, not just documented (Phase
8 design decisions #4/#5):
1. `references` are always built directly from the `RetrievedChunk`s
   `rag.retrieval.retrieve()` returned — never parsed out of, or trusted
   from, the LLM's own JSON text, even when that text contains
   fabricated-looking file paths.
2. If retrieval finds nothing usable (empty, or every candidate's rerank
   score is below a floor), the LLM is never called at all — a fixed
   "not enough context" answer is returned immediately.
"""

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.prompts import build_qa_prompt
from app.ai.provider import GroqProvider, LLMProvider
from app.models.api_endpoint import ApiEndpoint
from app.models.symbol import Symbol
from app.rag.retrieval import RetrievedChunk, retrieve
from app.services.analysis_read_service import get_latest_completed_run

# Reranker scores are a sigmoid in [0, 1] (`rag/reranker.py::score_candidates`).
# An unvalidated round number — flagged, not tuned, same stance as
# `analysis/debt.py`'s thresholds (Known Issue #13) and every other
# unvalidated constant introduced across Phase 7. Deliberately low: this
# only exists to catch the "retrieval found nothing genuinely relevant"
# case, not to second-guess the reranker's own ordering.
_MIN_RELEVANCE_SCORE = 0.1

_INSUFFICIENT_CONTEXT_ANSWER = (
    "I don't have enough grounded information in this repository to answer "
    "that question. Try asking about a specific file, function, or API route."
)

_MAX_MODULE_QUESTIONS = 3
_MAX_ENDPOINT_QUESTIONS = 3


@dataclass(frozen=True)
class Reference:
    """One citation attached to an `AssistantAnswer` — mirrors
    `RetrievedChunk`'s own citation fields exactly (a strict subset: no
    `chunk_id`/`chunk_text`/`score`/`matched_modes`, which are retrieval
    internals, not something a citation chip needs to render)."""

    file_path: str
    symbol_name: str | None
    line_start: int
    line_end: int


@dataclass(frozen=True)
class AssistantAnswer:
    answer: str
    references: list[Reference]
    follow_up_questions: list[str]
    insufficient_context: bool


def answer_question(
    db: Session, *, repository_id: uuid.UUID, question: str, provider: LLMProvider | None = None
) -> AssistantAnswer:
    """Retrieves grounding context for `question`, asks the LLM to answer
    strictly from it, and returns a real, citation-backed answer. Never
    raises on a malformed LLM response (degrades to a plain-text fallback);
    does propagate `app.ai.provider.LLMProviderError` for a genuine
    provider-side failure (non-2xx, timeout, connection error) — that's a
    real infrastructure failure, not something this function can paper
    over, and is left for the API layer (Phase 8.3) to translate."""
    chunks = retrieve(db, repository_id=repository_id, query=question)
    if not chunks or all(chunk.score < _MIN_RELEVANCE_SCORE for chunk in chunks):
        return AssistantAnswer(
            answer=_INSUFFICIENT_CONTEXT_ANSWER,
            references=[],
            follow_up_questions=[],
            insufficient_context=True,
        )

    references = _build_references(chunks)
    system, messages, response_schema = build_qa_prompt(question, chunks)

    llm = provider or GroqProvider()
    raw = llm.complete(system=system, messages=messages, response_schema=response_schema)

    parsed = _parse_qa_response(raw)
    if parsed is None:
        # Defensive fallback (design decision #3): the raw text becomes the
        # answer verbatim, references still come from retrieval (never from
        # this unparsed text), no follow-ups, and insufficient_context is
        # left False — we genuinely don't know either way from unparsed text,
        # and False is the "answer, don't claim ignorance" default.
        return AssistantAnswer(answer=raw, references=references, follow_up_questions=[], insufficient_context=False)

    return AssistantAnswer(
        answer=parsed["answer"],
        references=references,
        follow_up_questions=parsed["follow_up_questions"],
        insufficient_context=parsed["insufficient_context"],
    )


def _build_references(chunks: list[RetrievedChunk]) -> list[Reference]:
    return [
        Reference(
            file_path=chunk.file_path,
            symbol_name=chunk.symbol_name,
            line_start=chunk.line_start,
            line_end=chunk.line_end,
        )
        for chunk in chunks
    ]


def _parse_qa_response(raw: str) -> dict | None:
    """`None` means "fall back to plain text" — covers a non-JSON response,
    a JSON value that isn't an object, and one missing/mistyping any of the
    three schema fields. Groq's strict mode should make this unreachable in
    practice (schema fields are all `required`, no optional fields), but
    this is genuine belt-and-suspenders per design decision #3, not a
    wasted precaution — the same "never let one bad input abort the whole
    operation" convention as the analysis pipeline's per-file try/except."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if not isinstance(parsed.get("answer"), str):
        return None
    if not isinstance(parsed.get("insufficient_context"), bool):
        return None
    follow_ups = parsed.get("follow_up_questions")
    if not isinstance(follow_ups, list) or not all(isinstance(q, str) for q in follow_ups):
        return None
    return parsed


def list_suggested_questions(db: Session, *, repository_id: uuid.UUID) -> list[str]:
    """A deterministic heuristic over already-persisted facts — never an
    LLM call (design decision #6). `[]` for a repository with no completed
    analysis run, matching every other Phase 6+ screen's "not analyzed"
    convention rather than fabricating a result."""
    run = get_latest_completed_run(db, repository_id)
    if run is None:
        return []

    # Modules with the most symbols — a simple, already-available proxy for
    # "a module worth asking about" (no new centrality algorithm invented
    # here, per this sub-phase's own scoping). "." (the root-level-files
    # module, see `Symbol.module`'s own docstring) is excluded — it isn't a
    # nameable concept a question like "How does the . module work?" reads
    # sensibly for.
    module_rows = db.execute(
        select(Symbol.module, func.count(Symbol.id))
        .where(Symbol.repository_id == repository_id, Symbol.analysis_run_id == run.id, Symbol.module != ".")
        .group_by(Symbol.module)
        .order_by(func.count(Symbol.id).desc(), Symbol.module.asc())
        .limit(_MAX_MODULE_QUESTIONS)
    ).all()

    # Deterministic selection by (method, path) — "top" here means
    # "consistently selected", not ranked by any importance signal this
    # sub-phase computes.
    endpoint_rows = db.execute(
        select(ApiEndpoint.method, ApiEndpoint.path)
        .where(ApiEndpoint.repository_id == repository_id, ApiEndpoint.analysis_run_id == run.id)
        .order_by(ApiEndpoint.method.asc(), ApiEndpoint.path.asc())
        .limit(_MAX_ENDPOINT_QUESTIONS)
    ).all()

    questions = [f"How does the {module} module work?" for module, _count in module_rows]
    questions += [f"What does {method} {path} do?" for method, path in endpoint_rows]
    return questions
