"""Grounded prompt construction (Phase 8.1, extended in 8.7) — turns
already-retrieved facts into the exact `(system, messages, response_schema)`
triple `GroqProvider.complete` (`app/ai/provider.py`) expects. No DB/model
calls anywhere in this module — every input is already-resolved data the
caller (Phase 8.2's `assistant_service.py`, Phase 8.4's `insight_service.py`,
Phase 8.7's `file_insight_service.py`) passes in; this module only formats
text.

Every builder shares one grounding preamble restating the guide's Section 14
principle as a direct instruction: answer only from the given context, cite
a file/symbol/line for every claim, say plainly when the context isn't
enough, and keep repository facts separate from AI interpretation. This is
the textual half of Phase 8's anti-hallucination design — the other half
(`Reference`s always built from `RetrievedChunk`s, never parsed out of the
LLM's own text) lives in Phase 8.2, not here.

Every response schema targets Groq's strict-mode structured output, which
(per the OpenAI `json_schema` convention Groq mirrors) requires the root
schema to be a JSON object, every property listed in `required` (no optional
fields), and `additionalProperties: false` — never a bare top-level array.
`build_insights_prompt`'s schema therefore wraps the "list of insights" the
plan describes as a top-level array under one `insights` object property;
`insight_service.py` (Phase 8.4) reads `parsed["insights"]`.
"""

from collections.abc import Iterable
from typing import Protocol

from app.rag.retrieval import RetrievedChunk

_GROUNDING_PREAMBLE = (
    "You are an AI assistant answering questions about a specific software "
    "repository. You are given retrieved excerpts of that repository's real "
    "code, API routes, and documentation as context. Follow these rules "
    "strictly:\n"
    "1. Answer only using the provided context — never invent behavior, "
    "files, or facts the context doesn't support.\n"
    "2. Whenever you make a claim about the codebase, ground it in the "
    "context you were given; the file path, symbol name, and line range are "
    "attached to each excerpt for this reason.\n"
    "3. If the provided context does not contain enough information to "
    "answer the question, say so plainly instead of guessing.\n"
    "4. Clearly distinguish established repository facts (what the context "
    "shows) from your own interpretation or inference about them."
)


# --- Q&A ---------------------------------------------------------------------

_QA_TASK_INSTRUCTION = (
    "Answer the user's question about this repository using only the "
    "repository context below. Also suggest 1-3 relevant follow-up "
    "questions a developer exploring this codebase might ask next — return "
    "an empty list if none are genuinely useful. Set insufficient_context "
    "to true if the provided context does not let you answer the question "
    "responsibly, and in that case make `answer` explain what's missing "
    "rather than guessing."
)

_QA_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "insufficient_context": {"type": "boolean"},
        "follow_up_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "insufficient_context", "follow_up_questions"],
    "additionalProperties": False,
}


def build_qa_prompt(question: str, retrieved_chunks: list[RetrievedChunk]) -> tuple[str, list[dict], dict]:
    """Builds the grounded Q&A prompt. Each chunk is rendered with a
    `file_path:line_start-line_end (symbol_name)` header before its text, so
    the model can attribute claims to a specific excerpt — though the
    `Reference`s Phase 8.2 actually attaches to its response always come
    from `retrieved_chunks` directly, never parsed back out of the model's
    own text (design decision #4)."""
    context = _format_chunks(retrieved_chunks)
    system = f"{_GROUNDING_PREAMBLE}\n\n{_QA_TASK_INSTRUCTION}"
    user_content = f"Repository context:\n\n{context}\n\n---\n\nQuestion: {question}"
    messages = [{"role": "user", "content": user_content}]
    return system, messages, _QA_RESPONSE_SCHEMA


def _format_chunks(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "(no relevant context was found)"
    blocks = []
    for chunk in chunks:
        header = f"{chunk.file_path}:{chunk.line_start}-{chunk.line_end}"
        if chunk.symbol_name:
            header += f" ({chunk.symbol_name})"
        blocks.append(f"[{header}]\n{chunk.chunk_text}")
    return "\n\n---\n\n".join(blocks)


# --- AI insights ---------------------------------------------------------------


class DebtIssueSummary(Protocol):
    """Duck-typed view of one debt finding — matches
    `app.analysis.debt.DebtIssue`'s field names, but deliberately not
    imported from there: Phase 8.4's `insight_service.py` re-queries
    persisted `DebtIssue` *rows* (`app.models.debt_issue.DebtIssue`, which
    has no `file_path` of its own, only `file_id`), so resolving whatever
    view it has into this shape is that caller's job, not this module's.
    `issue_type`/`severity` are plain, already-resolved strings (e.g. the
    enum's own `.value`), not enum members."""

    issue_type: str
    severity: str
    file_path: str | None
    module: str | None
    description: str


class CouplingSummary(Protocol):
    """Matches `app.analysis.graph.CouplingScore`'s fields exactly — that
    dataclass is pure (no DB/ORM), so it's fine for a caller to pass it
    directly; kept as a Protocol here rather than imported so this module
    still declares zero dependency on `analysis/`."""

    module: str
    fan_in: int
    fan_out: int
    score: int


class CircularGroupSummary(Protocol):
    """Matches `app.analysis.graph.CycleResult`'s `files` field."""

    files: Iterable[str]


_INSIGHTS_TASK_INSTRUCTION = (
    "Given the following structured facts about a repository's technical "
    "debt and dependency structure, generate 3 to 5 AI insights a developer "
    "would find genuinely useful — each one grounded in the facts given, "
    "never invented. For each insight, pick a tone: 'danger' for a serious, "
    "urgent problem, 'warning' for a real but non-urgent issue, or 'info' "
    "for a neutral observation worth knowing. Give each insight a short "
    "title and a one-to-two sentence body explaining the finding and, where "
    "natural, why it matters."
)

_INSIGHTS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tone": {"type": "string", "enum": ["danger", "warning", "info"]},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["tone", "title", "body"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["insights"],
    "additionalProperties": False,
}


class FileChunkSummary(Protocol):
    """Duck-typed view of one already-persisted `RagChunk` row belonging to
    the file being explained (Phase 8.7's `file_insight_service.py`) —
    gathering/ordering/capping the real rows is that caller's job (design
    decision #1: FILE_OVERVIEW first, then the rest by `chunk_index`,
    capped at an unvalidated `_MAX_CONTEXT_CHARS`), this module only
    formats. `chunk_type` is a plain, already-resolved string (the enum's
    own `.value`), not an enum member — same convention as
    `DebtIssueSummary.issue_type`/`.severity`."""

    chunk_type: str
    chunk_text: str
    line_start: int
    line_end: int


_FILE_INSIGHT_TASK_INSTRUCTION = (
    "Explain the purpose of the given repository file, using only the "
    "excerpts of its own real content provided below (its symbols, API "
    "routes, or documentation, depending on what was extracted from it). "
    "Return two fields: `purpose`, a single concise sentence describing "
    "what the file is for, and `explanation`, a short paragraph (2-4 "
    "sentences) describing what it actually does, grounded in the "
    "excerpts given."
)

_FILE_INSIGHT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "purpose": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["purpose", "explanation"],
    "additionalProperties": False,
}


def build_file_insight_prompt(file_path: str, chunks: list[FileChunkSummary]) -> tuple[str, list[dict], dict]:
    """Builds the per-file purpose/explanation prompt from the file's own
    already-persisted `RagChunk` rows. This function only formats; it never
    queries anything (`file_insight_service.py` owns gathering/ordering/
    capping the chunks, per design decision #1)."""
    blocks = []
    for chunk in chunks:
        header = f"[{chunk.chunk_type}] lines {chunk.line_start}-{chunk.line_end}"
        blocks.append(f"{header}\n{chunk.chunk_text}")
    excerpts = "\n\n---\n\n".join(blocks) if blocks else "(no content excerpts available)"

    system = f"{_GROUNDING_PREAMBLE}\n\n{_FILE_INSIGHT_TASK_INSTRUCTION}"
    user_content = f"File: {file_path}\n\nExcerpts:\n\n{excerpts}"
    messages = [{"role": "user", "content": user_content}]
    return system, messages, _FILE_INSIGHT_RESPONSE_SCHEMA


def build_insights_prompt(
    top_debt_issues: list[DebtIssueSummary],
    coupling_summary: list[CouplingSummary],
    circular_groups: list[CircularGroupSummary],
    doc_coverage_ratio: float,
) -> tuple[str, list[dict], dict]:
    """Builds the AI-insights prompt from already-computed structural facts
    (gathering them is Phase 8.4's job — see that sub-phase's design
    decision #8: top debt issues by severity, coupling/circular-dependency
    facts re-derived the same way `app/api/v1/dependencies.py` already does,
    and a doc-coverage ratio computed the same way Dashboard's health metric
    already is). This function only formats; it never queries anything."""
    lines: list[str] = ["Top technical debt issues:"]
    if top_debt_issues:
        for issue in top_debt_issues:
            target = issue.file_path or issue.module or "(repository-wide)"
            lines.append(f"- [{issue.severity}] {issue.issue_type} in {target}: {issue.description}")
    else:
        lines.append("- none found")

    lines.append("\nMost-coupled modules (fan-in + fan-out):")
    if coupling_summary:
        for coupling in coupling_summary:
            lines.append(
                f"- {coupling.module}: score={coupling.score} (fan_in={coupling.fan_in}, fan_out={coupling.fan_out})"
            )
    else:
        lines.append("- none found")

    lines.append("\nCircular dependency groups:")
    if circular_groups:
        for group in circular_groups:
            files_sorted = sorted(group.files)
            lines.append(f"- {len(files_sorted)} file(s): {', '.join(files_sorted)}")
    else:
        lines.append("- none found")

    lines.append(f"\nDocumentation coverage: {doc_coverage_ratio:.0%} of symbols have a docstring")

    facts = "\n".join(lines)
    system = f"{_GROUNDING_PREAMBLE}\n\n{_INSIGHTS_TASK_INSTRUCTION}"
    messages = [{"role": "user", "content": f"Repository facts:\n\n{facts}"}]
    return system, messages, _INSIGHTS_RESPONSE_SCHEMA
