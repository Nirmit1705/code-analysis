"""AI notes for the Flow Tracer (Phase 10.6) — one short sentence per traced
file saying what role it plays, fetched separately from the trace itself so the
screen renders the structure immediately and the notes fill in.

Same contract as `change_narrative_service` / `component_insight_service`
(docs/PHASE_10_PLAN.md decision #3): the model only ever sees facts the tracer
already computed — each file's layer label, the names it declares, the files it
imports and the named external services it reaches — never file content, in
**one** strict-schema `GroqProvider` call for the whole trace. `generate_trace_
notes` **never raises**: any failure (provider error, misconfigured provider,
malformed JSON, nothing usable in the answer) returns `None`, which the screen
shows as "no note". Successes are cached per process by the exact prompt (runs
are immutable, so the same trace always builds the same prompt); a failure is
never cached.

Parsing is per entry rather than all-or-nothing: a note for an index that is
not in the trace, a duplicate index, a non-string or an empty note is dropped,
and the rest are kept — each note is about one hop, so a partly-good answer is
still useful and a missing hop just has no note. `None` only when nothing
usable survives.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.ai.prompts import build_trace_notes_prompt
from app.ai.provider import GroqProvider, LLMProvider, LLMProviderError
from app.ai.text_cache import PromptCache
from app.schemas.tracer import TraceNote
from app.services.trace_service import TraceBundle

logger = logging.getLogger(__name__)

# Unvalidated round numbers — only bound prompt size and answer length.
_MAX_SYMBOLS = 10
_MAX_IMPORTS = 8
_MAX_NOTE_CHARS = 300

# Narrow/no-break/thin spaces the model sometimes writes for a normal one.
_ODD_SPACES = re.compile("[   ]")


@dataclass
class NoteHopFacts:
    """Duck-typed to `app.ai.prompts.TraceNoteHop`."""

    index: int
    layer: str
    file_path: str
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    external_services: list[str] = field(default_factory=list)


@dataclass
class TraceNotesFacts:
    """Duck-typed to `app.ai.prompts.TraceNotesInput`."""

    start_label: str
    handler: str | None
    hops: list[NoteHopFacts]
    truncated: bool


def build_notes_facts(bundle: TraceBundle) -> TraceNotesFacts:
    """Flattens a trace into the capped, plain-string facts the prompt is built
    from. Deterministic, so equal traces give equal prompts (the cache key)."""
    hops: list[NoteHopFacts] = []
    for hop in bundle.trace.hops:
        symbols = bundle.symbols_by_file.get(hop.file_path, [])
        hops.append(
            NoteHopFacts(
                index=hop.index,
                layer=hop.layer.value,
                file_path=hop.file_path,
                symbols=symbols[:_MAX_SYMBOLS],
                imports=_cap(list(hop.deps)),
                external_services=[
                    ext.label + (f" (via {' > '.join(ext.via)})" if ext.via else "")
                    for ext in hop.external_services
                ],
            )
        )
    return TraceNotesFacts(
        start_label=bundle.start.label,
        handler=bundle.trace.hops[0].fn if bundle.trace.hops else None,
        hops=hops,
        truncated=bundle.trace.truncated,
    )


def _cap(items: list[str]) -> list[str]:
    if len(items) <= _MAX_IMPORTS:
        return items
    return [*items[:_MAX_IMPORTS], f"(+{len(items) - _MAX_IMPORTS} more)"]


# Shared bounded LRU (see `app/ai/text_cache.py`) — keyed by the exact prompt.
# Stores the normalized notes as a JSON string.
_cache = PromptCache()


def clear_cache() -> None:
    """For tests."""
    _cache.clear()


def generate_trace_notes(facts: TraceNotesFacts, provider: LLMProvider | None = None) -> list[TraceNote] | None:
    """One note per answered hop, or `None` — never raises."""
    if not facts.hops:
        return None

    try:
        system, messages, response_schema = build_trace_notes_prompt(facts)
        cache_key = PromptCache.key_for(system, messages)
        cached = _cache.get(cache_key)
        if cached is not None:
            return [TraceNote(**item) for item in json.loads(cached)]

        llm = provider or GroqProvider()
        raw = llm.complete(system=system, messages=messages, response_schema=response_schema)
        notes = _parse_notes(raw, valid_indices={hop.index for hop in facts.hops})
        if notes is not None:
            _cache.put(cache_key, json.dumps([n.model_dump() for n in notes]))
        return notes
    except LLMProviderError:
        logger.warning("trace_narrative_service: LLM call failed for trace %s", facts.start_label)
        return None
    except Exception:  # noqa: BLE001 — the contract is "never raises", so even a misconfigured provider degrades
        logger.exception("trace_narrative_service: unexpected failure for trace %s", facts.start_label)
        return None


def _parse_notes(raw: str, *, valid_indices: set[int]) -> list[TraceNote] | None:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("notes"), list):
        return None

    notes: dict[int, TraceNote] = {}
    for item in parsed["notes"]:
        if not isinstance(item, dict):
            continue
        index, text = item.get("index"), item.get("note")
        # `bool` is an `int` subclass in Python; `true` is not a hop index.
        if not isinstance(index, int) or isinstance(index, bool) or index not in valid_indices:
            continue
        if index in notes or not isinstance(text, str):
            continue
        text = _ODD_SPACES.sub(" ", text).strip()
        if text:
            notes[index] = TraceNote(index=index, note=text[:_MAX_NOTE_CHARS])
    if not notes:
        return None
    return [notes[i] for i in sorted(notes)]
