"""Generated documentation (Phase 10.8): four sections — `overview`,
`architecture`, `modules`, `api` — built from one completed run's persisted facts
and stored in `generated_documents`, one current row per (repository, section).

**Nothing here runs on its own.** `generate_document` is called only when a user
asks for a section (docs/PHASE_10_PLAN.md decision #7: not on view, not in the
analysis pipeline), and it replaces the previous version (upsert).

**Facts first, prose second** (decision #3). Every block is assembled
deterministically from rows the other screens already read — `File`,
`ApiEndpoint`, the Architecture component graph (`architecture_service`, the
same derivation that screen uses), the dependency graph's cycles, `DebtIssue`
counts and the run's own counters. The model writes exactly two kinds of text,
each in a block flagged `ai: true` so the screen and the exports can say which
words are which:

* the Overview's one paragraph about what the README says, grounded only in the
  README's own stored chunks (omitted when the repository has no README — it is
  never invented from the code);
* the Modules section's one-line role per component, from the component's name,
  size, main files, endpoints and import relations (one batched call).

Architecture and API are fully deterministic, so they never need the model.

**Generation never fails because the model did.** Any model failure — provider
error, misconfigured provider, malformed or empty output, a component the answer
skipped — only removes that AI text; the document is still built and stored, with
`ai_written=False` when no AI text survived. The only way to fail is the
repository having no completed analysis (`NotAnalyzedError`).

Block shape (`content["blocks"]`): `{h?, p?, list?, code?, ai?}`, rendered in the
order h, p, list, code. `content["source"]` is one line naming what the document
was built from.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.ai.prompts import build_module_summaries_prompt, build_readme_summary_prompt
from app.ai.provider import GroqProvider, LLMProvider, LLMProviderError
from app.analysis.chunking import RagChunkType
from app.analysis.language_detect import Language
from app.models.analysis_run import AnalysisRun
from app.models.debt_issue import DebtIssue
from app.models.file import File
from app.models.generated_document import DocType, GeneratedDocument
from app.models.rag_chunk import RagChunk
from app.models.repository import Repository
from app.schemas.api_reference import ApiEndpointRead
from app.schemas.architecture import GraphEdge, GraphNode
from app.services import api_reference_service, architecture_service
from app.services.architecture_service import Architecture
from app.services.external_services import EXTERNAL_NODE_ID, resolve_external_target

logger = logging.getLogger(__name__)

Block = dict[str, Any]

# Unvalidated round numbers — they bound the size of a stored document and of a
# prompt, nothing more.
MAX_README_CHARS = 6000
MAX_SUMMARY_CHARS = 1200
MAX_MODULE_SUMMARY_CHARS = 300
MAX_ENTRY_POINTS = 8
MAX_RELATIONS = 8
MAX_COMPONENT_LINKS = 40
MAX_CYCLES_LISTED = 10
MAX_CYCLE_FILES_SHOWN = 6
MAX_API_ENDPOINTS = 400
MAX_ENDPOINT_EXAMPLES = 3

_ODD_SPACES = re.compile("[   ]")

# Groq's free tier caps tokens per minute, and a Modules request alone can use
# half of it, so two model calls in a row ("Generate all") can hit a 429 that
# clears in seconds. Generation is an explicit, already-slow user action, so one
# bounded wait-and-retry here beats silently dropping the AI text.
MAX_RATE_LIMIT_WAIT_SECONDS = 20.0
_RATE_LIMIT_WAIT_RE = re.compile(r"try again in ([0-9.]+)\s*s", re.IGNORECASE)
_sleep = time.sleep  # patched by the smoke script so tests do not really wait

_LANGUAGE_NAMES: dict[Language, str] = {
    Language.JAVASCRIPT: "JavaScript",
    Language.TYPESCRIPT: "TypeScript",
    Language.TSX: "TSX",
    Language.PYTHON: "Python",
    Language.JAVA: "Java",
}
_FRAMEWORK_NAMES = {"express": "Express", "spring_boot": "Spring Boot", "fastapi": "FastAPI", "flask": "Flask"}
_KIND_LABELS = {
    "frontend": "Frontend",
    "gateway": "API / gateway",
    "service": "Service",
    "data": "Data",
    "cache": "Cache",
    "external": "External",
}
# Duplicated from `analysis/debt.py`/`component_insight_service.py` per this
# codebase's "small private helpers stay local" convention.
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Files that look like where a program starts. A file only counts when no other
# (non-test) source file imports it — a barrel `index.ts` that everything
# imports is not an entry point.
_ENTRY_STEMS = frozenset({"main", "app", "server", "index", "__main__", "manage", "wsgi", "asgi", "application"})
_TEST_DIR_NAMES = frozenset({"__tests__", "tests", "test", "spec", "specs", "e2e"})
_TEST_FILE_RE = re.compile(r"(\.(test|spec)\.[^./]+$)|(^test_[^/]*\.py$)|(_test\.py$)|(Tests?\.java$)")
_README_RE = re.compile(r"^readme(\.(md|markdown|rst|txt))?$", re.IGNORECASE)


class NotAnalyzedError(Exception):
    """The repository has no completed analysis, so there is nothing to document."""


# --- facts ---------------------------------------------------------------------------


@dataclass
class Readme:
    """Duck-typed to `app.ai.prompts.ReadmeSummaryInput`."""

    readme_path: str
    readme_text: str


@dataclass
class LanguageRow:
    name: str
    files: int
    loc: int


@dataclass
class ModuleFacts:
    """Duck-typed to `app.ai.prompts.ModuleSummaryFacts`; also what the Modules
    blocks are rendered from, so the prompt and the document agree."""

    id: str
    label: str
    kind: str
    files: int
    loc: int
    key_files: list[str] = field(default_factory=list)
    apis: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    used_by: list[str] = field(default_factory=list)
    circular_files: int = 0
    debt_findings: int = 0
    worst_severity: str | None = None


@dataclass
class ModulesInput:
    """Duck-typed to `app.ai.prompts.ModuleSummariesInput`."""

    modules: list[ModuleFacts]


@dataclass
class DocFacts:
    repository_full_name: str
    run: AnalysisRun
    architecture: Architecture
    endpoints: list[ApiEndpointRead]
    languages: list[LanguageRow]
    total_loc: int
    supported_files: int
    entry_points: list[str]
    debt_issue_count: int
    database_label: str | None
    readme: Readme | None
    modules: list[ModuleFacts]


def is_test_path(file_path: str) -> bool:
    path = PurePosixPath(file_path)
    if any(part.lower() in _TEST_DIR_NAMES for part in path.parts[:-1]):
        return True
    return bool(_TEST_FILE_RE.search(path.name))


def load_facts(db: Session, repository: Repository) -> DocFacts:
    """Everything the four builders need, from the latest completed run.
    Raises `NotAnalyzedError` when there is none."""
    architecture = architecture_service.build_architecture(db, repository)
    if architecture is None:
        raise NotAnalyzedError
    run = architecture.run
    files = architecture.files

    by_language: dict[Language, LanguageRow] = {}
    for f in files:
        if f.language is None:
            continue
        row = by_language.setdefault(f.language, LanguageRow(name=_LANGUAGE_NAMES[f.language], files=0, loc=0))
        row.files += 1
        row.loc += f.loc
    languages = sorted(by_language.values(), key=lambda r: (-r.loc, r.name))

    debt_rows = db.execute(select(DebtIssue.file_id, DebtIssue.severity).where(DebtIssue.analysis_run_id == run.id)).all()
    path_by_file_id = {f.id: f.path for f in files}
    debt_by_component: dict[str, tuple[int, str]] = {}
    for file_id, severity in debt_rows:
        path = path_by_file_id.get(file_id) if file_id is not None else None
        group_id = architecture.classification.file_group_id.get(path) if path is not None else None
        if group_id is None:
            continue
        count, worst = debt_by_component.get(group_id, (0, "low"))
        level = severity.value
        debt_by_component[group_id] = (count + 1, level if _SEVERITY_RANK[level] > _SEVERITY_RANK[worst] else worst)

    database_label = None
    if run.database_provider:
        resolved = resolve_external_target("@prisma/client", run.database_provider)
        database_label = resolved[1] if resolved is not None else None

    return DocFacts(
        repository_full_name=repository.full_name,
        run=run,
        architecture=architecture,
        endpoints=api_reference_service.load_api_endpoints(db, run.id),
        languages=languages,
        total_loc=sum(f.loc for f in files),
        supported_files=sum(1 for f in files if f.language is not None),
        entry_points=_entry_points(architecture),
        debt_issue_count=len(debt_rows),
        database_label=database_label,
        readme=_load_readme(db, run, files),
        modules=_module_facts(architecture, debt_by_component),
    )


def _entry_points(architecture: Architecture) -> list[str]:
    imported = {
        e.target_file
        for e in architecture.dependency_edges
        if e.target_file is not None and not is_test_path(e.source_file)
    }
    found = []
    for f in architecture.files:
        if f.language is None or is_test_path(f.path) or f.path in imported:
            continue
        stem = PurePosixPath(f.path).stem
        if stem.lower() in _ENTRY_STEMS or stem.lower().endswith("application"):
            found.append(f.path)
    return sorted(found, key=lambda p: (p.count("/"), p))[:MAX_ENTRY_POINTS]


def _load_readme(db: Session, run: AnalysisRun, files: list[File]) -> Readme | None:
    """The shallowest README that has stored documentation chunks. Only the
    stored chunks are used (no GitHub call), in file order, capped."""
    candidates = sorted(
        (f for f in files if _README_RE.match(PurePosixPath(f.path).name)),
        key=lambda f: (f.path.count("/"), f.path),
    )
    for f in candidates:
        chunks = db.execute(
            select(RagChunk.chunk_text)
            .where(
                RagChunk.analysis_run_id == run.id,
                RagChunk.file_id == f.id,
                RagChunk.chunk_type == RagChunkType.DOCUMENTATION,
            )
            .order_by(RagChunk.chunk_index, RagChunk.line_start)
        ).scalars().all()
        # Every stored chunk starts with the chunker's own `[DOCUMENTATION] <path>`
        # header line; it is bookkeeping, not README text, so it isn't shown to the model.
        header = f"[DOCUMENTATION] {f.path}\n\n"
        bodies = [c[len(header):] if c.startswith(header) else c for c in chunks]
        text = "\n\n".join(b.strip() for b in bodies if b.strip()).strip()
        if text:
            return Readme(readme_path=f.path, readme_text=text[:MAX_README_CHARS])
    return None


def _relations(edges: list[GraphEdge], *, node_id: str, labels: dict[str, str], outgoing: bool) -> list[str]:
    """`"Label (N imports)"`, most-imported first, ties by label."""
    rows: list[tuple[int, str]] = []
    for edge in edges:
        if outgoing and edge.source == node_id:
            other = edge.target
        elif not outgoing and edge.target == node_id:
            other = edge.source
        else:
            continue
        rows.append((edge.weight, labels.get(other, other)))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return [f"{label} ({_count(weight, 'import')})" for weight, label in rows[:MAX_RELATIONS]]


def display_labels(nodes: list[GraphNode]) -> dict[str, str]:
    """`{node id: label}`, with the node id appended when two components share a
    label (a server `Services` and a client `Services`), so a sentence like
    "Services depends on server/src" is never ambiguous."""
    counts = Counter(n.label for n in nodes)
    return {n.id: (f"{n.label} ({n.id})" if counts[n.label] > 1 else n.label) for n in nodes}


def _module_facts(architecture: Architecture, debt_by_component: dict[str, tuple[int, str]]) -> list[ModuleFacts]:
    labels = display_labels(architecture.nodes)
    modules = []
    for node in architecture.nodes:
        if node.files == 0:  # a synthetic external-service node, not code in the repository
            continue
        count, worst = debt_by_component.get(node.id, (0, ""))
        modules.append(
            ModuleFacts(
                id=node.id,
                label=labels[node.id],
                kind=node.kind,
                files=node.files,
                loc=node.loc,
                key_files=list(node.key_files),
                apis=list(node.apis),
                depends_on=_relations(architecture.edges, node_id=node.id, labels=labels, outgoing=True),
                used_by=_relations(architecture.edges, node_id=node.id, labels=labels, outgoing=False),
                circular_files=node.circular_files,
                debt_findings=count,
                worst_severity=worst or None,
            )
        )
    return modules


# --- block builders (pure) -------------------------------------------------------------


def _count(n: int, noun: str, plural: str | None = None) -> str:
    return f"{n:,} {noun if n == 1 else (plural or noun + 's')}"


def _short(sha: str) -> str:
    return sha[:7]


def build_overview_blocks(facts: DocFacts, readme_summary: str | None) -> list[Block]:
    run = facts.run
    blocks: list[Block] = []

    if facts.readme is not None and readme_summary:
        blocks.append({"h": f"What {facts.readme.readme_path} says", "p": readme_summary, "ai": True})

    components = [n for n in facts.architecture.nodes if n.files > 0]
    glance = [
        f"{_count(run.file_count, 'file')} analyzed, {facts.total_loc:,} lines of code",
        f"{_count(facts.supported_files, 'file')} in a supported language (JavaScript, TypeScript, Python or Java)",
        f"{_count(run.module_count, 'module')} and {_count(len(components), 'architecture component')}",
    ]
    frameworks = sorted({_FRAMEWORK_NAMES.get(e.framework, e.framework) for e in facts.endpoints})
    glance.append(
        f"{_count(run.api_count, 'API endpoint')}" + (f" ({', '.join(frameworks)})" if frameworks and run.api_count else "")
    )
    cycles = facts.architecture.cycle_count
    glance.append(f"{_count(cycles, 'circular dependency cycle')}" if cycles else "No circular dependencies found in the import graph")
    glance.append(f"{_count(facts.debt_issue_count, 'technical debt finding')} (score {run.debt_score:g})")
    if facts.database_label:
        glance.append(f"Database: {facts.database_label}, read from the project's schema file")
    blocks.append({"h": "At a glance", "list": glance})

    if facts.languages:
        parsed_loc = sum(r.loc for r in facts.languages)
        blocks.append(
            {
                "h": "Languages",
                "list": [
                    f"{r.name}: {_count(r.files, 'file')}, {r.loc:,} lines"
                    + (f" ({r.loc * 100 / parsed_loc:.1f}% of source lines)" if parsed_loc else "")
                    for r in facts.languages
                ],
            }
        )

    if facts.entry_points:
        blocks.append(
            {
                "h": "Likely entry points",
                "p": "Source files named like an entry point (main, app, server, index…) that no other source file imports.",
                "list": list(facts.entry_points),
            }
        )
    return blocks


def build_architecture_blocks(facts: DocFacts) -> list[Block]:
    arch = facts.architecture
    components = [n for n in arch.nodes if n.files > 0]
    # Named services only; the catch-all "External Dependencies" bucket is not
    # one (it is every package the curated table doesn't individually recognise).
    services = [n for n in arch.nodes if n.files == 0 and n.id != EXTERNAL_NODE_ID]
    other_packages = next((n for n in arch.nodes if n.id == EXTERNAL_NODE_ID), None)
    labels = display_labels(arch.nodes)
    internal_ids = {n.id for n in components}
    links = sorted(
        (e for e in arch.edges if e.source in internal_ids and e.target in internal_ids),
        key=lambda e: (-e.weight, labels.get(e.source, e.source), labels.get(e.target, e.target)),
    )

    blocks: list[Block] = [
        {
            "p": (
                f"{_count(len(components), 'component')}, {_count(len(links), 'dependency', 'dependencies')} between them"
                f" and {_count(arch.cycle_count, 'circular dependency cycle')}. Components are groups of files recognised"
                " from folder names and API route declarations; a component labelled with a raw path is one where no"
                " clearer role was recognised."
            )
        },
        {
            "h": "Components",
            "list": [
                f"{labels[n.id]}: {_KIND_LABELS.get(n.kind, n.kind).lower()}, {_count(n.files, 'file')}, {n.loc:,} lines"
                + (f", {_count(len(n.apis), 'endpoint')}" if n.apis else "")
                for n in components
            ],
        },
    ]

    if services:
        users: dict[str, list[str]] = {s.id: [] for s in services}
        for e in arch.edges:
            if e.target in users and e.source in internal_ids:
                users[e.target].append(labels.get(e.source, e.source))
        blocks.append(
            {
                "h": "External services",
                "p": "Named services the code imports a client for (a database driver, a cache, a third-party SDK).",
                "list": [
                    f"{s.label} ({_KIND_LABELS.get(s.kind, s.kind).lower()}), used by {', '.join(sorted(set(users[s.id]))) or 'no component'}"
                    for s in services
                ],
            }
        )
    if other_packages is not None:
        other_users = sorted({labels.get(e.source, e.source) for e in arch.edges if e.target == EXTERNAL_NODE_ID and e.source in internal_ids})
        if other_users:
            blocks.append(
                {
                    "p": "Other third-party packages that are not individually recognised are imported by "
                    + ", ".join(other_users)
                    + "."
                }
            )

    if links:
        shown = links[:MAX_COMPONENT_LINKS]
        lines = [
            f"{labels.get(e.source, e.source)} depends on {labels.get(e.target, e.target)} ({_count(e.weight, 'import')})"
            + (", part of a cycle" if e.circular else "")
            for e in shown
        ]
        if len(links) > len(shown):
            lines.append(f"…and {len(links) - len(shown)} more")
        blocks.append({"h": "How components depend on each other", "p": "Counted in distinct file-to-file imports.", "list": lines})

    cycles = sorted(arch.graph.cycles, key=lambda c: (-len(c.files), sorted(c.files)))
    if cycles:
        lines = []
        for c in cycles[:MAX_CYCLES_LISTED]:
            ordered = sorted(c.files)
            names = ", ".join(PurePosixPath(p).name for p in ordered[:MAX_CYCLE_FILES_SHOWN])
            more = f" and {len(ordered) - MAX_CYCLE_FILES_SHOWN} more" if len(ordered) > MAX_CYCLE_FILES_SHOWN else ""
            where = sorted({labels.get(arch.classification.file_group_id.get(p, ""), "") for p in ordered} - {""})
            lines.append(f"{_count(len(ordered), 'file')} import each other in a loop: {names}{more}" + (f" (in {', '.join(where)})" if where else ""))
        if len(cycles) > MAX_CYCLES_LISTED:
            lines.append(f"…and {len(cycles) - MAX_CYCLES_LISTED} more cycles")
        blocks.append({"h": "Circular dependencies", "list": lines})
    else:
        blocks.append({"h": "Circular dependencies", "p": "None found in the resolved import graph."})
    return blocks


def build_modules_blocks(facts: DocFacts, summaries: dict[str, str]) -> list[Block]:
    blocks: list[Block] = []
    for m in facts.modules:
        lines = [f"{_KIND_LABELS.get(m.kind, m.kind)}: {_count(m.files, 'file')}, {m.loc:,} lines"]
        if m.key_files:
            lines.append("Main files: " + ", ".join(m.key_files))
        if m.apis:
            examples = ", ".join(m.apis[:MAX_ENDPOINT_EXAMPLES])
            more = "…" if len(m.apis) > MAX_ENDPOINT_EXAMPLES else ""
            lines.append(f"{_count(len(m.apis), 'endpoint')}: {examples}{more}")
        if m.depends_on:
            lines.append("Depends on: " + ", ".join(m.depends_on))
        if m.used_by:
            lines.append("Used by: " + ", ".join(m.used_by))
        if m.circular_files:
            lines.append(f"{_count(m.circular_files, 'file')} of this component {'is' if m.circular_files == 1 else 'are'} part of a circular dependency")
        if m.debt_findings:
            lines.append(f"{_count(m.debt_findings, 'technical debt finding')} (worst: {m.worst_severity})")
        block: Block = {"h": m.label, "list": lines}
        summary = summaries.get(m.id)
        if summary:
            block["p"] = summary
            block["ai"] = True
        blocks.append(block)
    if not blocks:
        blocks.append({"p": "No source components were recognised in this analysis."})
    return blocks


def build_api_blocks(facts: DocFacts) -> list[Block]:
    endpoints = facts.endpoints
    if not endpoints:
        return [{"p": "No API endpoints were detected. Routes are recognised for Express, Spring Boot, FastAPI and Flask."}]

    groups: dict[str, list[ApiEndpointRead]] = {}
    for e in endpoints:
        groups.setdefault(e.group, []).append(e)
    frameworks = sorted({_FRAMEWORK_NAMES.get(e.framework, e.framework) for e in endpoints})

    blocks: list[Block] = [
        {
            "p": (
                f"{_count(len(endpoints), 'endpoint')} in {_count(len(groups), 'resource group')}, extracted from route"
                f" declarations ({', '.join(frameworks)}). Request and response shapes and authentication are not extracted,"
                " so none are listed; middleware is shown by name only, as written on the route itself."
            )
        }
    ]
    budget = MAX_API_ENDPOINTS
    for group in sorted(groups):
        if budget <= 0:
            break
        items = groups[group][:budget]
        budget -= len(items)
        lines = [_endpoint_line(e) for e in items]
        if len(items) < len(groups[group]):
            lines.append(f"…and {len(groups[group]) - len(items)} more")
        blocks.append({"h": group, "list": lines})
    omitted = len(endpoints) - MAX_API_ENDPOINTS
    if omitted > 0:
        blocks.append({"p": f"{_count(omitted, 'more endpoint')} are not listed here. See the API Reference screen for the full list."})
    return blocks


def _endpoint_line(e: ApiEndpointRead) -> str:
    line = f"{e.method} {e.path}"
    if e.handler is not None:
        line += " → " + ("an inline callback" if e.handler.name == "<inline>" else e.handler.name)
    if e.middleware:
        line += f" (middleware: {', '.join(e.middleware)})"
    line += f" · {e.file_path}" + (f":{e.line}" if e.line is not None else "")
    return line


# --- the two model calls (both optional, both never raise) ------------------------------


def _normalize(text: str, limit: int) -> str | None:
    # The model sometimes writes a no-break hyphen (U+2011) for a plain one, which breaks search and copy-paste.
    text = _ODD_SPACES.sub(" ", text).replace("‑", "-").strip()
    return text[:limit] if text else None


def _complete(llm: LLMProvider, *, system: str, messages: list[dict], response_schema: dict) -> str:
    """`llm.complete`, retried once after the advised wait when the provider
    reports a per-minute rate limit (429 with a "try again in Ns" hint no longer
    than `MAX_RATE_LIMIT_WAIT_SECONDS`). Any other failure is re-raised."""
    try:
        return llm.complete(system=system, messages=messages, response_schema=response_schema)
    except LLMProviderError as exc:
        text = str(exc)
        match = _RATE_LIMIT_WAIT_RE.search(text) if "(429)" in text else None
        if match is None:
            raise
        wait = float(match.group(1)) + 0.5
        if wait > MAX_RATE_LIMIT_WAIT_SECONDS:
            raise
        logger.info("documentation_service: rate limited, retrying once in %.1fs", wait)
        _sleep(wait)
        return llm.complete(system=system, messages=messages, response_schema=response_schema)


def _summarize_readme(readme: Readme, provider: LLMProvider | None) -> str | None:
    try:
        system, messages, schema = build_readme_summary_prompt(readme)
        llm = provider or GroqProvider()
        parsed = json.loads(_complete(llm, system=system, messages=messages, response_schema=schema))
        summary = parsed.get("summary") if isinstance(parsed, dict) else None
        return _normalize(summary, MAX_SUMMARY_CHARS) if isinstance(summary, str) else None
    except LLMProviderError:
        logger.warning("documentation_service: README summary request failed for %s", readme.readme_path)
    except (json.JSONDecodeError, TypeError):
        logger.warning("documentation_service: README summary was not usable JSON for %s", readme.readme_path)
    except Exception:  # noqa: BLE001 — generation must never fail because the model did
        logger.exception("documentation_service: unexpected failure summarising %s", readme.readme_path)
    return None


def _summarize_modules(modules: list[ModuleFacts], provider: LLMProvider | None) -> dict[str, str]:
    """`{component id: one-line role}`; keeps every valid entry and drops the
    rest (unknown or duplicate ids, non-strings, blanks)."""
    if not modules:
        return {}
    try:
        system, messages, schema = build_module_summaries_prompt(ModulesInput(modules=modules))
        llm = provider or GroqProvider()
        parsed = json.loads(_complete(llm, system=system, messages=messages, response_schema=schema))
        items = parsed.get("modules") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            return {}
        valid = {m.id for m in modules}
        out: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            module_id, text = item.get("id"), item.get("summary")
            if not isinstance(module_id, str) or module_id not in valid or module_id in out or not isinstance(text, str):
                continue
            cleaned = _normalize(text, MAX_MODULE_SUMMARY_CHARS)
            if cleaned:
                out[module_id] = cleaned
        return out
    except LLMProviderError:
        logger.warning("documentation_service: module summaries request failed")
    except (json.JSONDecodeError, TypeError):
        logger.warning("documentation_service: module summaries were not usable JSON")
    except Exception:  # noqa: BLE001 — generation must never fail because the model did
        logger.exception("documentation_service: unexpected failure summarising modules")
    return {}


# --- generate + read ---------------------------------------------------------------------


def generate_document(
    db: Session, repository: Repository, doc_type: DocType, provider: LLMProvider | None = None
) -> GeneratedDocument:
    """Builds `doc_type` from the repository's latest completed run and stores it,
    replacing any previous version of that section. Raises `NotAnalyzedError` when
    there is no completed run; never raises because of the model."""
    facts = load_facts(db, repository)
    run = facts.run
    head = f"{_short(run.commit_sha)} on {run.branch}"

    if doc_type is DocType.OVERVIEW:
        summary = _summarize_readme(facts.readme, provider) if facts.readme is not None else None
        blocks = build_overview_blocks(facts, summary)
        if facts.readme is None:
            readme_note = "no README found"
        elif summary:
            readme_note = f"{facts.readme.readme_path} summarised by AI"
        else:
            readme_note = f"{facts.readme.readme_path} found, AI summary unavailable"
        source = f"Analysis of {_count(run.file_count, 'file')} at {head} · README: {readme_note}"
    elif doc_type is DocType.ARCHITECTURE:
        blocks = build_architecture_blocks(facts)
        components = sum(1 for n in facts.architecture.nodes if n.files > 0)
        source = f"Import graph of {_count(run.file_count, 'file')} at {head} · {_count(components, 'component')} · computed, no AI text"
    elif doc_type is DocType.MODULES:
        summaries = _summarize_modules(facts.modules, provider)
        blocks = build_modules_blocks(facts, summaries)
        if summaries:
            note = "one-line roles written by AI from names and import relations"
        else:
            note = "computed, no AI text" if not facts.modules else "AI role lines unavailable"
        source = f"Component classification at {head} · {_count(len(facts.modules), 'component')} · {note}"
    else:
        blocks = build_api_blocks(facts)
        source = f"Route declarations at {head} · {_count(len(facts.endpoints), 'endpoint')} · computed, no AI text"

    ai_written = any(b.get("ai") for b in blocks)
    content = {"blocks": blocks, "source": source}

    stmt = (
        pg_insert(GeneratedDocument)
        .values(
            repository_id=repository.id,
            analysis_run_id=run.id,
            doc_type=doc_type,
            content=content,
            ai_written=ai_written,
            generated_at=func.now(),
        )
        .on_conflict_do_update(
            constraint="uq_generated_documents_repository_id_doc_type",
            set_={
                "analysis_run_id": run.id,
                "content": content,
                "ai_written": ai_written,
                "generated_at": func.now(),
            },
        )
        .returning(GeneratedDocument.id)
    )
    document_id = db.execute(stmt).scalar_one()
    db.commit()
    return db.execute(
        select(GeneratedDocument).where(GeneratedDocument.id == document_id).execution_options(populate_existing=True)
    ).scalar_one()


def get_document(db: Session, repository_id: uuid.UUID, doc_type: DocType) -> GeneratedDocument | None:
    return db.execute(
        select(GeneratedDocument).where(
            GeneratedDocument.repository_id == repository_id, GeneratedDocument.doc_type == doc_type
        )
    ).scalar_one_or_none()


def list_documents(db: Session, repository_id: uuid.UUID) -> list[GeneratedDocument]:
    """Every stored section, in `DocType` order (sorted in Python — the column
    stores the member *name*, so a SQL `ORDER BY` would sort alphabetically,
    Known Issue #15)."""
    rows = db.execute(select(GeneratedDocument).where(GeneratedDocument.repository_id == repository_id)).scalars().all()
    order = {t: i for i, t in enumerate(DocType)}
    return sorted(rows, key=lambda d: order[d.doc_type])
