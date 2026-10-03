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


# --- Architecture component insight ---------------------------------------------


class ComponentInsightInput(Protocol):
    """Everything the component-insight prompt is allowed to know about one
    Architecture node — facts already derived by the Architecture screen's
    own pipeline (module classification, file-grain dependency edges, cycle
    detection, persisted debt issues), never file content. Lists are
    pre-capped by the caller (`component_insight_service.py`)."""

    label: str
    kind: str
    is_external: bool
    files: int
    loc: int
    key_files: list[str]
    apis: list[str]
    depends_on: list[str]
    used_by: list[str]
    circular_files: int
    circular_with: list[str]
    debt_issues: list[str]


_COMPONENT_TASK_INSTRUCTION = (
    "You are explaining one component of a software system to a developer "
    "who is new to it. Below are structured facts about that component: what "
    "kind it is, how large, its main files and API endpoints, which other "
    "components it depends on, which components depend on it, and any "
    "circular dependencies or technical-debt findings recorded for it. Write "
    "2 to 4 short sentences of plain prose (no markdown, no bullet points, "
    "no headings) saying what role this component plays in the system and "
    "what a developer should be careful about when changing it. Use only the "
    "facts given. Infer a role only from the names, kind and relationships "
    "shown, and say so plainly when that is all you have; never describe "
    "code you were not shown. Never say a component is healthy, well "
    "designed, safe or low risk unless the facts say so: absence of "
    "information is not evidence. Mention a circular dependency or debt "
    "finding only if one is listed. If other components depend on it, say "
    "that changes here can affect them."
)

_COMPONENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"insight": {"type": "string"}},
    "required": ["insight"],
    "additionalProperties": False,
}


def build_component_insight_prompt(facts: ComponentInsightInput) -> tuple[str, list[dict], dict]:
    """Builds the per-component insight prompt. Same shared grounding
    preamble as every other prompt here; formats only, never queries."""

    def section(title: str, items: list[str]) -> list[str]:
        return [f"\n{title}:", *(f"- {item}" for item in items)] if items else [f"\n{title}: none"]

    kind = f"{facts.kind} (an external service this repository imports, not code in it)" if facts.is_external else facts.kind
    lines: list[str] = [f"Component: {facts.label}", f"Kind: {kind}"]
    if not facts.is_external:
        lines.append(f"Size: {facts.files} files, {facts.loc} lines of code")
    lines += section("Main files", facts.key_files)
    lines += section("API endpoints defined here", facts.apis)
    lines += section("Depends on (imports from)", facts.depends_on)
    lines += section("Used by (imported by)", facts.used_by)
    if facts.circular_files:
        lines.append(f"\nCircular dependency: {facts.circular_files} of this component's files are in a cycle")
        if facts.circular_with:
            lines.append("The cycle also reaches: " + ", ".join(facts.circular_with))
    else:
        lines.append("\nCircular dependency: none recorded")
    lines += section("Technical debt findings recorded for this component", facts.debt_issues)

    system = f"{_GROUNDING_PREAMBLE}\n\n{_COMPONENT_TASK_INSTRUCTION}"
    messages = [{"role": "user", "content": "Component facts:\n" + "\n".join(lines)}]
    return system, messages, _COMPONENT_RESPONSE_SCHEMA


# --- Change narrative (Phase 9.7) -------------------------------------------------


class ImpactSummary(Protocol):
    """Duck-typed view of `impact_service.ImpactResult`, already flattened to
    plain strings/ints by `change_narrative_service.py` (which owns capping
    the file lists) — this module only formats."""

    risk: str
    score: int
    change_kind: str
    direct_files: list[str]
    indirect_files: list[str]
    affected_apis: list[str]
    excluded_test_files: int


class ChangeExplanationInput(Protocol):
    """Everything the change-narrative prompt is allowed to know: the
    already-computed, file-grain facts from `analysis/change.py`'s
    `ChangeSet` (and, from Impact, `impact_service`'s result) — never a raw
    diff patch or any file content. Lists are pre-capped by the caller; an
    over-long list already ends in a `"(+N more)"` entry."""

    base_ref: str
    head_ref: str
    added_files: list[str]
    removed_files: list[str]
    modified_files: list[str]
    added_symbols: list[str]
    removed_symbols: list[str]
    added_apis: list[str]
    removed_apis: list[str]
    dependency_changes: list[str]
    debt_score_delta: float
    impact: ImpactSummary | None


_CHANGE_TASK_INSTRUCTION = (
    "You are explaining a code change to a developer who is reviewing it. "
    "Below are structured facts comparing two analyzed states of a "
    "repository: which files, symbols, API endpoints and dependency edges "
    "differ, and, when present, the downstream impact computed by "
    "dependency analysis. Write 2 to 4 short sentences of plain prose "
    "(no markdown, no bullet points, no headings) saying what changed and "
    "what it means for the rest of the codebase. Use only the facts given. "
    "Do not guess why the change was made and do not describe code you were "
    "not shown: a 'modified' file only means its symbols or line count "
    "differ. Never say that something is unaffected, safe, low risk or "
    "non-breaking unless the facts say so: absence of information is not "
    "evidence. In particular, when no downstream impact section is given, "
    "say nothing about what else the change affects or whether it breaks "
    "anything. Leave out any section that has no entries. If impact data is "
    "given, explain the risk level through how many files depend on the "
    "change and whether it is breaking, and never contradict it. If nothing "
    "changed, say that plainly."
)

_CHANGE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"explanation": {"type": "string"}},
    "required": ["explanation"],
    "additionalProperties": False,
}


def build_change_explanation_prompt(facts: ChangeExplanationInput) -> tuple[str, list[dict], dict]:
    """Builds the Compare/Impact narrative prompt. Same shared grounding
    preamble as every other Phase 8 prompt; the user message is a short,
    pre-digested fact list, matching `build_insights_prompt`'s precedent of
    feeding the model structured facts rather than raw data. This function
    only formats; it never queries anything."""

    def section(title: str, items: list[str]) -> list[str]:
        return [f"\n{title}:", *(f"- {item}" for item in items)] if items else [f"\n{title}: none"]

    lines: list[str] = [f"Comparison: {facts.base_ref} -> {facts.head_ref}"]
    lines += section("Files added", facts.added_files)
    lines += section("Files removed", facts.removed_files)
    lines += section("Files modified", facts.modified_files)
    lines += section("Symbols added", facts.added_symbols)
    lines += section("Symbols removed", facts.removed_symbols)
    lines += section("API endpoints added", facts.added_apis)
    lines += section("API endpoints removed", facts.removed_apis)
    lines += section("Dependency changes", facts.dependency_changes)
    lines.append(f"\nTechnical debt score change: {facts.debt_score_delta:+.1f}")

    if facts.impact is not None:
        impact = facts.impact
        lines.append(
            f"\nDownstream impact (from dependency analysis): risk {impact.risk}, "
            f"score {impact.score}/100, change type {impact.change_kind}"
        )
        lines += section("Files that directly import a changed file", impact.direct_files)
        lines += section("Files that depend on a changed file indirectly", impact.indirect_files)
        lines += section("API endpoints defined in affected files", impact.affected_apis)
        if impact.excluded_test_files:
            lines.append(f"\n{impact.excluded_test_files} test file(s) are also reached but do not count toward the score")

    system = f"{_GROUNDING_PREAMBLE}\n\n{_CHANGE_TASK_INSTRUCTION}"
    messages = [{"role": "user", "content": "Change facts:\n" + "\n".join(lines)}]
    return system, messages, _CHANGE_RESPONSE_SCHEMA


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


# --- Debt recommendation (Phase 10.4) -------------------------------------------


class DebtRecommendationInput(Protocol):
    """One technical-debt finding plus whatever real code was found for it.
    `excerpts` are the finding's own file's already-persisted `RagChunk` rows
    (gathered, ordered and capped by `debt_insight_service.py`); empty for a
    module-level finding or a file with no chunks."""

    category: str
    severity: str
    metric_value: float
    description: str
    file_path: str | None
    module: str | None
    excerpts: list[FileChunkSummary]


_DEBT_TASK_INSTRUCTION = (
    "You are advising a developer on ONE technical-debt finding found by a rule-based analyzer. Below are "
    "the finding (its category, severity, the measured value and the analyzer's own sentence), where it is, "
    "and — when available — excerpts of that file's real code. Write 2 to 4 sentences of plain prose (no "
    "markdown, no bullet points, no headings) recommending a concrete first step to reduce this specific "
    "debt. Rules: base the advice only on the finding and the excerpts; name a function, class or file only "
    "if it appears in the facts or excerpts; never invent line numbers, names or behaviour you were not "
    "shown; do not give generic advice (such as 'write more tests' or 'refactor') unless you tie it to "
    "something shown. If no code excerpts are provided, say plainly that you are advising from the finding "
    "alone and keep the advice to what that finding supports. Never claim a change is safe, quick or "
    "low risk: absence of information is not evidence."
)

_DEBT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"recommendation": {"type": "string"}},
    "required": ["recommendation"],
    "additionalProperties": False,
}


def build_debt_recommendation_prompt(facts: DebtRecommendationInput) -> tuple[str, list[dict], dict]:
    """Builds the per-finding recommendation prompt. Same shared grounding
    preamble as every other prompt here; formats only, never queries."""
    lines = [
        f"Category: {facts.category}",
        f"Severity: {facts.severity}",
        f"Measured value: {facts.metric_value:g}",
        f"Analyzer's finding: {facts.description}",
        f"File: {facts.file_path}" if facts.file_path else "File: (a module- or repository-level finding, not one file)",
    ]
    if facts.module:
        lines.append(f"Module: {facts.module}")

    if facts.excerpts:
        blocks = [f"[{c.chunk_type}] lines {c.line_start}-{c.line_end}\n{c.chunk_text}" for c in facts.excerpts]
        lines.append("\nExcerpts of the file's real code:\n\n" + "\n\n---\n\n".join(blocks))
    else:
        lines.append("\nNo code excerpts are available for this finding.")

    system = f"{_GROUNDING_PREAMBLE}\n\n{_DEBT_TASK_INSTRUCTION}"
    messages = [{"role": "user", "content": "Technical debt finding:\n" + "\n".join(lines)}]
    return system, messages, _DEBT_RESPONSE_SCHEMA


# --- Flow tracer notes (Phase 10.6) ----------------------------------------------


class TraceNoteHop(Protocol):
    """One file on a traced request path, with only the facts the tracer
    already computed (`services/trace_narrative_service.py` builds these,
    pre-capped). `external_services` are rendered strings such as
    "PostgreSQL (via config/database.ts)"."""

    index: int
    layer: str
    file_path: str
    symbols: list[str]
    imports: list[str]
    external_services: list[str]


class TraceNotesInput(Protocol):
    start_label: str
    handler: str | None
    hops: list[TraceNoteHop]
    truncated: bool


_TRACE_NOTES_TASK_INSTRUCTION = (
    "You are annotating a request-flow trace through a codebase for a developer. The trace was computed "
    "from file imports: starting at one file, it lists the files a request is expected to pass through, "
    "outermost layer first. For each file you are given its index, a layer label (assigned from folder and "
    "filename naming conventions, so it is a guess, not a verified role), the functions or classes declared "
    "in it, the files it imports, and any named external service (database, cache, SaaS SDK) it reaches. "
    "Write exactly ONE short plain-prose sentence (at most 25 words, no markdown) per file saying what role "
    "that file plays in the flow. Rules: use only the facts given; infer a role only from the file name, "
    "layer label, declared names and imports shown, and say 'appears to' when you are inferring; the trace "
    "knows which files import which, NOT which functions are called or what data moves, so never state what "
    "a function does, which function is called, or what is passed between files; never say a file validates, "
    "authenticates, sanitises, caches or similar unless its name or a declared name says so; do not mention "
    "index numbers or the word 'hop'. Return one note for every index given, using that index. Absence of "
    "information is not evidence: if a file's name and contents give you little, say so briefly."
)

_TRACE_NOTES_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "note": {"type": "string"}},
                "required": ["index", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["notes"],
    "additionalProperties": False,
}


def build_trace_notes_prompt(facts: TraceNotesInput) -> tuple[str, list[dict], dict]:
    """Builds the batched per-file notes prompt for one trace. Same shared
    grounding preamble as every other prompt here; formats only, never queries."""
    lines = [f"Trace start: {facts.start_label}"]
    if facts.handler:
        lines.append(f"The route's handler (named in the route declaration): {facts.handler}")
    if facts.truncated:
        lines.append("The trace was cut off by a size limit, so more files may exist downstream.")

    for hop in facts.hops:
        lines.append(f"\n[{hop.index}] {hop.file_path}")
        lines.append(f"  layer label: {hop.layer}")
        lines.append("  declares: " + (", ".join(hop.symbols) if hop.symbols else "nothing recorded"))
        lines.append("  imports: " + (", ".join(hop.imports) if hop.imports else "no other files"))
        if hop.external_services:
            lines.append("  external services: " + ", ".join(hop.external_services))

    system = f"{_GROUNDING_PREAMBLE}\n\n{_TRACE_NOTES_TASK_INSTRUCTION}"
    messages = [{"role": "user", "content": "Trace facts:\n" + "\n".join(lines)}]
    return system, messages, _TRACE_NOTES_RESPONSE_SCHEMA
