"""AI narrative for Compare and Impact (Phase 9.7) — one short, grounded
paragraph saying what changed between two analyzed runs and what it means
downstream.

Follows `insight_service.generate_insights`'s contract exactly (design
decision #15): the model only ever sees facts the deterministic layers
already computed (`analysis/change.py`'s `ChangeSet`, and from Impact,
`impact_service`'s result) — never a raw diff patch or any file content —
through one call to the existing `GroqProvider`, and `generate_change_
explanation` **never raises**. Any failure (provider error, malformed or
wrongly-shaped JSON, an empty string, even a misconfigured provider) degrades
to `None`, which the screens render as "No AI explanation available". This is
the same deliberate asymmetry `insight_service` documents against
`assistant_service.answer_question`: an offline-style enrichment must never
fail an otherwise-successful comparison or impact read.

Two additions beyond `insight_service`, both needed because this runs on a
per-request GET rather than once per analysis:

* **No call for an empty diff.** If nothing differs there is nothing to
  explain, so the LLM is not asked (and cannot invent something).
* **A small in-process cache** keyed by the exact prompt. Runs are immutable,
  so the same pair of runs always produces the same prompt; repeat views of a
  comparison are then instant and free instead of a fresh LLM call each time.
  Only successful results are cached (a failure is retried next time), the
  cache is bounded, and it is per-process (a restart empties it) — a cheap
  stand-in for persisting the text, not a source of truth.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.ai.prompts import build_change_explanation_prompt
from app.ai.provider import GroqProvider, LLMProvider, LLMProviderError
from app.ai.text_cache import PromptCache
from app.analysis.change import ChangeSet
from app.analysis.dependencies import DependencyEdge, DependencyEdgeType
from app.services.impact_service import ImpactResult

logger = logging.getLogger(__name__)

# Unvalidated round numbers — flagged, not tuned, same stance as every other
# constant introduced across Phases 7-9. They only bound prompt size.
_MAX_LIST_ITEMS = 12
_MAX_EXPLANATION_CHARS = 1500

# The model sometimes writes narrow/no-break/thin spaces where it means a
# normal one (seen live in the assistant's answers); normalized out here too.
_ODD_SPACES = re.compile("[   ]")


@dataclass
class ImpactFacts:
    """Duck-typed to `app.ai.prompts.ImpactSummary`."""

    risk: str
    score: int
    change_kind: str
    direct_files: list[str] = field(default_factory=list)
    indirect_files: list[str] = field(default_factory=list)
    affected_apis: list[str] = field(default_factory=list)
    excluded_test_files: int = 0


@dataclass
class ChangeFacts:
    """Duck-typed to `app.ai.prompts.ChangeExplanationInput`."""

    base_ref: str
    head_ref: str
    added_files: list[str] = field(default_factory=list)
    removed_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    added_symbols: list[str] = field(default_factory=list)
    removed_symbols: list[str] = field(default_factory=list)
    added_apis: list[str] = field(default_factory=list)
    removed_apis: list[str] = field(default_factory=list)
    dependency_changes: list[str] = field(default_factory=list)
    debt_score_delta: float = 0.0
    impact: ImpactFacts | None = None

    def is_empty(self) -> bool:
        """True when no file/symbol/API/dependency differs — the only case
        in which the debt score alone moving is not worth narrating."""
        return not (
            self.added_files
            or self.removed_files
            or self.modified_files
            or self.added_symbols
            or self.removed_symbols
            or self.added_apis
            or self.removed_apis
            or self.dependency_changes
        )


def _cap(items: list[str]) -> list[str]:
    if len(items) <= _MAX_LIST_ITEMS:
        return items
    return [*items[:_MAX_LIST_ITEMS], f"(+{len(items) - _MAX_LIST_ITEMS} more)"]


def _edge_target(edge: DependencyEdge) -> str:
    if edge.edge_type is DependencyEdgeType.INTERNAL and edge.target_file is not None:
        return edge.target_file
    return edge.target_package or "?"


def build_change_facts(
    *,
    base_ref: str,
    head_ref: str,
    change_set: ChangeSet,
    debt_score_delta: float,
    impact: ImpactResult | None = None,
) -> ChangeFacts:
    """Flattens `ChangeSet` (and optionally `ImpactResult`) into the capped,
    plain-string facts the prompt is built from. Deterministic ordering
    throughout (sorted), so equal inputs give equal prompts — which is what
    makes the cache work."""
    return ChangeFacts(
        base_ref=base_ref,
        head_ref=head_ref,
        added_files=_cap(sorted(f.path for f in change_set.added_files)),
        removed_files=_cap(sorted(f.path for f in change_set.removed_files)),
        modified_files=_cap(sorted(change_set.modified_files)),
        added_symbols=_cap(sorted(f"{s.name} ({s.kind.value}) in {s.file_path}" for s in change_set.added_symbols)),
        removed_symbols=_cap(sorted(f"{s.name} ({s.kind.value}) in {s.file_path}" for s in change_set.removed_symbols)),
        added_apis=_cap(sorted(f"{a.method} {a.path}" for a in change_set.added_apis)),
        removed_apis=_cap(sorted(f"{a.method} {a.path}" for a in change_set.removed_apis)),
        dependency_changes=_cap(
            sorted(f"added: {e.source_file} imports {_edge_target(e)}" for e in change_set.added_edges)
            + sorted(f"removed: {e.source_file} no longer imports {_edge_target(e)}" for e in change_set.removed_edges)
        ),
        debt_score_delta=debt_score_delta,
        impact=None
        if impact is None
        else ImpactFacts(
            risk=impact.risk,
            score=impact.score,
            change_kind=impact.breakdown.change_kind,
            direct_files=_cap([f.path for f in impact.direct]),
            indirect_files=_cap([f.path for f in impact.indirect]),
            affected_apis=_cap(list(impact.affected_apis)),
            excluded_test_files=impact.breakdown.excluded_test_files,
        ),
    )


# Shared bounded LRU (see `app/ai/text_cache.py`) — keyed by the exact prompt.
_cache = PromptCache()


def clear_cache() -> None:
    """For tests."""
    _cache.clear()


def generate_change_explanation(facts: ChangeFacts, provider: LLMProvider | None = None) -> str | None:
    """A short plain-prose explanation, or `None` — never raises."""
    if facts.is_empty():
        return None

    try:
        system, messages, response_schema = build_change_explanation_prompt(facts)
        cache_key = PromptCache.key_for(system, messages)
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached

        llm = provider or GroqProvider()
        raw = llm.complete(system=system, messages=messages, response_schema=response_schema)
        explanation = _parse_explanation(raw)
        if explanation is not None:
            _cache.put(cache_key, explanation)
        return explanation
    except LLMProviderError:
        logger.warning("change_narrative_service: LLM call failed for %s -> %s", facts.base_ref, facts.head_ref)
        return None
    except Exception:  # noqa: BLE001 — the contract is "never raises", so even a misconfigured provider degrades
        logger.exception("change_narrative_service: unexpected failure for %s -> %s", facts.base_ref, facts.head_ref)
        return None


def _parse_explanation(raw: str) -> str | None:
    """Defensive, all-or-nothing, like `insight_service._parse_insights_response`."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    text = parsed.get("explanation")
    if not isinstance(text, str):
        return None
    text = _ODD_SPACES.sub(" ", text).strip()
    if not text:
        return None
    return text[:_MAX_EXPLANATION_CHARS]
