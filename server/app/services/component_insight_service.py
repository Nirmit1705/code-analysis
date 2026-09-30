"""AI insight for one Architecture component — what the Architecture screen's
detail panel shows when you click a node.

That panel used to end with a hardcoded line ("AI-generated insights will
appear here once the assistant is wired up") and `GraphNode.insight` has been
a permanent `""` since Phase 6.5, so nothing ever loaded. This fills it, the
same way Phase 8.7 did for a single file (lazily, on demand) and Phase 9.7 did
for Compare/Impact (grounded only in facts the deterministic layers already
computed, one `GroqProvider` call, never raises):

* The model sees only what the Architecture pipeline already knows about the
  component: its kind, size, key files and API endpoints, which components it
  depends on and which depend on it (with import counts), how many of its
  files sit in a dependency cycle, and its recorded technical-debt findings.
  Never file content.
* `generate_component_insight` returns `None` on any failure (provider error,
  malformed JSON, empty text, even a misconfigured provider) — the panel shows
  "No AI insight available" and nothing else in the screen is affected.
* Generated on click, not up front: a repository has up to ~24 components, and
  generating all of them on every page view would be that many model calls
  nobody asked for. A repeat click is served from `PromptCache` (per-process,
  successes only — see `app/ai/text_cache.py`).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import build_component_insight_prompt
from app.ai.provider import GroqProvider, LLMProvider, LLMProviderError
from app.ai.text_cache import PromptCache
from app.analysis.debt import DebtSeverity
from app.models.debt_issue import DebtIssue
from app.models.file import File
from app.schemas.architecture import GraphEdge, GraphNode
from app.services.module_classification import ModuleClassification

logger = logging.getLogger(__name__)

# Unvalidated round numbers — only bound prompt size.
_MAX_LIST_ITEMS = 8
_MAX_DEBT_ISSUES = 4
_MAX_INSIGHT_CHARS = 1200

# Duplicated from `insight_service.py`'s own (private) `_SEVERITY_RANK`, per
# this codebase's "duplicate small private helpers" convention. Needed because
# `DebtIssue.severity` can't be ordered in SQL (Known Issue #15 — the column
# stores the member *name*, so `ORDER BY` would sort alphabetically).
_SEVERITY_RANK: dict[DebtSeverity, int] = {
    DebtSeverity.LOW: 0,
    DebtSeverity.MEDIUM: 1,
    DebtSeverity.HIGH: 2,
    DebtSeverity.CRITICAL: 3,
}

# The model sometimes emits narrow/no-break/thin spaces where it means a normal
# one (seen live in the assistant's answers).
_ODD_SPACES = re.compile("[   ]")


@dataclass
class ComponentFacts:
    """Duck-typed to `app.ai.prompts.ComponentInsightInput`."""

    label: str
    kind: str
    is_external: bool
    files: int
    loc: int
    key_files: list[str] = field(default_factory=list)
    apis: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    used_by: list[str] = field(default_factory=list)
    circular_files: int = 0
    circular_with: list[str] = field(default_factory=list)
    debt_issues: list[str] = field(default_factory=list)


def _cap(items: list[str]) -> list[str]:
    if len(items) <= _MAX_LIST_ITEMS:
        return items
    return [*items[:_MAX_LIST_ITEMS], f"(+{len(items) - _MAX_LIST_ITEMS} more)"]


def _relations(edges: list[GraphEdge], *, node_id: str, labels: dict[str, str], outgoing: bool) -> list[str]:
    """`"Label (N imports)"`, most-imported first, ties by label — so equal
    inputs always give equal prompts (which is what makes the cache work)."""
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
    return [f"{label} ({weight} import{'' if weight == 1 else 's'})" for weight, label in rows]


def build_component_facts(
    db: Session,
    *,
    node: GraphNode,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    classification: ModuleClassification,
    files: list[File],
    analysis_run_id: uuid.UUID,
) -> ComponentFacts:
    labels = {n.id: n.label for n in nodes}

    circular_neighbours = sorted(
        {
            labels.get(e.target if e.source == node.id else e.source, "?")
            for e in edges
            if e.circular and node.id in (e.source, e.target)
        }
    )

    return ComponentFacts(
        label=node.label,
        kind=node.kind,
        is_external=node.files == 0,
        files=node.files,
        loc=node.loc,
        key_files=_cap(list(node.key_files)),
        apis=_cap(list(node.apis)),
        depends_on=_cap(_relations(edges, node_id=node.id, labels=labels, outgoing=True)),
        used_by=_cap(_relations(edges, node_id=node.id, labels=labels, outgoing=False)),
        circular_files=node.circular_files,
        circular_with=_cap(circular_neighbours),
        debt_issues=_debt_issue_lines(db, node, classification, files, analysis_run_id),
    )


def _debt_issue_lines(
    db: Session, node: GraphNode, classification: ModuleClassification, files: list[File], analysis_run_id: uuid.UUID
) -> list[str]:
    """The component's top recorded debt findings, found through the files that
    belong to it (a `DebtIssue` row carries `file_id`, not a component id)."""
    member_files = {f.id: f.path for f in files if classification.file_group_id.get(f.path) == node.id}
    if not member_files:
        return []
    issues = (
        db.execute(
            select(DebtIssue).where(
                DebtIssue.analysis_run_id == analysis_run_id, DebtIssue.file_id.in_(list(member_files))
            )
        )
        .scalars()
        .all()
    )
    top = sorted(issues, key=lambda i: (-_SEVERITY_RANK[i.severity], -i.metric_value, i.description))[:_MAX_DEBT_ISSUES]
    return [
        f"[{i.severity.value}] {i.issue_type.value} in {member_files[i.file_id]}: {i.description}"
        for i in top
        if i.file_id is not None
    ]


_cache = PromptCache()


def clear_cache() -> None:
    """For tests."""
    _cache.clear()


def generate_component_insight(facts: ComponentFacts, provider: LLMProvider | None = None) -> str | None:
    """A short plain-prose insight, or `None` — never raises."""
    try:
        system, messages, response_schema = build_component_insight_prompt(facts)
        cache_key = PromptCache.key_for(system, messages)
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached

        llm = provider or GroqProvider()
        raw = llm.complete(system=system, messages=messages, response_schema=response_schema)
        insight = _parse_insight(raw)
        if insight is not None:
            _cache.put(cache_key, insight)
        return insight
    except LLMProviderError:
        logger.warning("component_insight_service: LLM call failed for %s", facts.label)
        return None
    except Exception:  # noqa: BLE001 — the contract is "never raises", so even a misconfigured provider degrades
        logger.exception("component_insight_service: unexpected failure for %s", facts.label)
        return None


def _parse_insight(raw: str) -> str | None:
    """Defensive, all-or-nothing — same style as `insight_service`."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    text = parsed.get("insight")
    if not isinstance(text, str):
        return None
    text = _ODD_SPACES.sub(" ", text).strip()
    return text[:_MAX_INSIGHT_CHARS] if text else None
