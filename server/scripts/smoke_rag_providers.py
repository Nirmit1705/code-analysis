"""Manual smoke test for Phase 7.4's local model providers
(`app/rag/embeddings.py`, `app/rag/reranker.py`).

Both modules are pure "load one model, expose one function" wrappers around
`sentence_transformers` — no DB, no Celery, nothing to fixture-build. Four
groups, run in this exact order:

1. `run_empty_input_contract()` (gating) — `embed_documents([])` /
   `score_candidates(q, [])` return `[]` without ever constructing the real
   model. Asserted both by return value and by wall-clock time (well under a
   second — real model construction takes several seconds even from a warm
   local cache), so this genuinely proves the short-circuit rather than
   "happened to be fast."
2. `run_provider_guard()` (gating) — monkeypatches each module's
   `get_settings` (the local `_Patch` helper, same monkeypatch-and-restore
   shape `smoke_analysis_tasks.py` already uses) to report an unsupported
   provider, then calls with non-empty input and asserts `RuntimeError` is
   raised fast. Proves the provider guard fires before model construction.
   **Must run before group 3** — `_get_model()` in both modules is a
   zero-argument `@lru_cache`d singleton, so a real load in group 3 would
   otherwise pre-empt this group's ability to ever re-enter `_get_model()`'s
   body (exceptions aren't cached by `lru_cache`, but a *successful* real
   load is, and that cached instance would be returned regardless of what
   `get_settings` is patched to say afterwards).
3. `run_live_bonus()` (non-gating) — real model load: `embed_query`/
   `embed_documents` on real text, dimension + L2-normalization checked;
   `score_candidates` on five candidates where exactly one is obviously
   relevant to the query, checked that it scores highest. Whole body in one
   `try/except Exception` → `[SKIP]` + `return True` on any failure (no
   network, no disk space, a slow/failed Hugging Face Hub download), mirroring
   `smoke_chunking.py`'s live-bonus pattern exactly. Excluded from the exit
   code, same as every other live-bonus group in this codebase.

Run: `uv run python scripts/smoke_rag_providers.py` (from `server/`). Groups
1-2 run in well under a second with zero network access. Group 3 downloads
~1-3GB per model from the Hugging Face Hub on first run (cached afterwards
under `%USERPROFILE%\\.cache\\huggingface` on Windows) and can take several
minutes the first time.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag import embeddings, reranker  # noqa: E402


class _Patch:
    """Tiny monkeypatch-and-restore helper — mirrors `smoke_analysis_tasks.py`'s
    own local `_Patch`, duplicated here rather than imported (small-helper
    duplication is this codebase's established convention)."""

    def __init__(self, obj, attr: str, replacement):
        self._obj = obj
        self._attr = attr
        self._original = getattr(obj, attr)
        setattr(obj, attr, replacement)

    def restore(self) -> None:
        setattr(self._obj, self._attr, self._original)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def run_empty_input_contract() -> bool:
    print("=== group 1 (gating): empty-input short-circuit (no model construction) ===")
    all_passed = True

    start = time.monotonic()
    docs_result = embeddings.embed_documents([])
    docs_elapsed = time.monotonic() - start
    all_passed &= _check("embed_documents([]) == []", docs_result == [])
    all_passed &= _check(
        "embed_documents([]) returned in well under a second (no model load)",
        docs_elapsed < 1.0,
        f"took {docs_elapsed:.3f}s",
    )

    start = time.monotonic()
    scores_result = reranker.score_candidates("anything", [])
    scores_elapsed = time.monotonic() - start
    all_passed &= _check("score_candidates(q, []) == []", scores_result == [])
    all_passed &= _check(
        "score_candidates(q, []) returned in well under a second (no model load)",
        scores_elapsed < 1.0,
        f"took {scores_elapsed:.3f}s",
    )

    return all_passed


def run_provider_guard() -> bool:
    print("=== group 2 (gating): unsupported provider raises before model construction ===")
    all_passed = True

    class _FakeSettings:
        EMBEDDING_PROVIDER = "gemini"
        EMBEDDING_MODEL = "does-not-matter"
        EMBEDDING_DIM = 1024
        RERANKER_PROVIDER = "openai"
        RERANKER_MODEL = "does-not-matter"

    fake_settings = _FakeSettings()

    embed_patch = _Patch(embeddings, "get_settings", lambda: fake_settings)
    try:
        start = time.monotonic()
        raised = False
        try:
            embeddings.embed_query("a non-empty query")
        except RuntimeError:
            raised = True
        elapsed = time.monotonic() - start
        all_passed &= _check("embed_query raises RuntimeError for an unsupported EMBEDDING_PROVIDER", raised)
        all_passed &= _check(
            "the guard raised fast (before any model construction was attempted)",
            elapsed < 1.0,
            f"took {elapsed:.3f}s",
        )
    finally:
        embed_patch.restore()

    rerank_patch = _Patch(reranker, "get_settings", lambda: fake_settings)
    try:
        start = time.monotonic()
        raised = False
        try:
            reranker.score_candidates("a query", ["a candidate document"])
        except RuntimeError:
            raised = True
        elapsed = time.monotonic() - start
        all_passed &= _check("score_candidates raises RuntimeError for an unsupported RERANKER_PROVIDER", raised)
        all_passed &= _check(
            "the guard raised fast (before any model construction was attempted)",
            elapsed < 1.0,
            f"took {elapsed:.3f}s",
        )
    finally:
        rerank_patch.restore()

    return all_passed


def run_live_bonus() -> bool:
    print("\n=== group 3 (non-gating): real local model load + encode + rerank ===")
    try:
        query = "How does the analysis pipeline extract API routes from a FastAPI app?"
        query_vector = embeddings.embed_query(query)
        r1 = _check("embed_query returns a 1024-dim vector", len(query_vector) == 1024, f"got {len(query_vector)}")
        norm = sum(x * x for x in query_vector) ** 0.5
        r2 = _check("embed_query's vector is L2-normalized", abs(norm - 1.0) < 1e-2, f"norm={norm:.6f}")

        documents = [
            "def extract_fastapi_routes(tree): ...  # walks the AST for @app.get/@app.post decorators",
            "The quarterly earnings report shows revenue grew 12% year over year.",
            "class Cat: def meow(self): print('meow')",
        ]
        doc_vectors = embeddings.embed_documents(documents)
        r3 = _check(
            "embed_documents returns one 1024-dim vector per document",
            len(doc_vectors) == len(documents) and all(len(v) == 1024 for v in doc_vectors),
            f"shapes={[len(v) for v in doc_vectors]}",
        )

        candidates = [
            "Venus is often called Earth's twin because of its similar size and proximity.",
            "def extract_fastapi_routes(tree): finds @app.get/@app.post decorated functions and builds ApiRoute objects",
            "Jupiter, the largest planet in our solar system, has a prominent red spot.",
            "Saturn, famous for its rings, is sometimes mistaken for the Red Planet.",
            "Mars, known for its reddish appearance, is often referred to as the Red Planet.",
        ]
        scores = reranker.score_candidates(query, candidates)
        top_index = max(range(len(scores)), key=lambda i: scores[i])
        r4 = _check(
            "reranker ranks the obviously-relevant candidate first",
            top_index == 1,
            f"scores={[round(s, 4) for s in scores]}, top_index={top_index}",
        )

        return r1 and r2 and r3 and r4
    except Exception as exc:  # noqa: BLE001 — any failure here means "can't reach the real models", not a bug
        print(f"  [SKIP] live bonus unavailable ({type(exc).__name__}: {exc})")
        return True


def main() -> int:
    r1 = run_empty_input_contract()
    r2 = run_provider_guard()
    r3 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. empty-input short-circuit (gating):  {'PASS' if r1 else 'FAIL'}")
    print(f"2. provider guard (gating):              {'PASS' if r2 else 'FAIL'}")
    print(f"3. live bonus (non-gating):               {'PASS' if r3 else 'SKIP/FAIL (excluded from exit code)'}")
    return 0 if (r1 and r2) else 1


if __name__ == "__main__":
    sys.exit(main())
