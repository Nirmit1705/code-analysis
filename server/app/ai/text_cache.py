"""A small, thread-safe, bounded, process-local LRU of generated text, keyed
by the exact prompt (`PromptCache.key_for`).

Used by the services that generate a short piece of text on a per-request GET
from immutable facts (`change_narrative_service` for Compare/Impact,
`component_insight_service` for Architecture): an analysis run never changes,
so the same facts always build the same prompt, and a repeat view can be served
without another LLM call. Only successful results are ever stored (a failure
is retried next time), it is per-process (a restart, or a second uvicorn
worker, starts cold), and it is a cheap stand-in for persisting the text — not
a source of truth.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict


class PromptCache:
    def __init__(self, max_entries: int = 256):
        self._max_entries = max_entries
        self._data: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key_for(system: str, messages: list[dict]) -> str:
        return hashlib.sha256(json.dumps([system, messages], sort_keys=True).encode()).hexdigest()

    def get(self, key: str) -> str | None:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
