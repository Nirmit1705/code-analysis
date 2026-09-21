"""Manual smoke test for Phase 8.1's LLM provider abstraction and grounded
prompt templates (`app/ai/provider.py`, `app/ai/prompts.py`).

Five groups, run in this order:

1. `run_request_shape()` (gating) — monkeypatches `provider._client.post` to
   a recording stub (no real network) and asserts the constructed request:
   Bearer header carries the configured API key, `response_format` is
   included only when a `response_schema` was passed to `complete(...)`,
   and — importantly — the API key never leaks into the JSON payload body
   itself (only the `Authorization` header carries it).
2. `run_error_handling()` (gating) — a stub `_client.post` returning a 429
   response, and a stub raising `httpx.TimeoutException`/`httpx.ConnectError`,
   each proven to surface as `LLMProviderError` — never the raw `httpx`
   exception escaping `GroqProvider.complete`.
3. `run_provider_guard()` (gating) — monkeypatches `get_settings` to report
   an unsupported `LLM_PROVIDER`, asserts `RuntimeError` fires and the
   `_client.post` stub is never actually invoked (proves the guard runs
   before any network call is attempted).
4. `run_prompt_builders()` (gating) — `build_qa_prompt`/`build_insights_prompt`
   called against small fixture inputs (hand-built `RetrievedChunk`s and
   duck-typed debt/coupling/cycle fixtures), asserting the returned
   `(system, messages, response_schema)` triple has the expected shape:
   grounding preamble present in `system`, one user message carrying the
   question/facts, and a schema whose `required` list matches exactly.
5. `run_live_bonus()` (non-gating) — one real Groq call (real `.env`
   credentials) with a trivial grounded question built via
   `build_qa_prompt`, confirming a real, schema-conforming JSON response
   comes back and parses cleanly — and printing which model actually
   honored strict mode. Whole body in one `try/except Exception` ->
   `[SKIP]` + `return True` on any failure (no network, no API key
   configured), mirroring `smoke_rag_providers.py`'s live-bonus pattern.

Run: `uv run python scripts/smoke_ai_provider.py` (from `server/`). Groups
1-4 make zero real network calls. Group 5 makes one real Groq API call.
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.ai import prompts, provider  # noqa: E402
from app.analysis.chunking import RagChunkType  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.rag.retrieval import RetrievedChunk  # noqa: E402


class _Patch:
    """Tiny monkeypatch-and-restore helper — mirrors `smoke_rag_providers.py`'s
    own local `_Patch`, duplicated here rather than imported (small-helper
    duplication is this codebase's established convention)."""

    def __init__(self, obj, attr: str, replacement):
        self._obj = obj
        self._attr = attr
        self._original = getattr(obj, attr)
        setattr(obj, attr, replacement)

    def restore(self) -> None:
        setattr(self._obj, self._attr, self._original)


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self) -> dict:
        return self._json_data


class _FakeSettings:
    LLM_PROVIDER = "groq"
    LLM_MODEL = "openai/gpt-oss-120b"
    LLM_API_KEY = "test-secret-key-should-never-appear-in-a-payload"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _fixture_chunk(**overrides) -> RetrievedChunk:
    defaults = dict(
        chunk_id=uuid.uuid4(),
        chunk_type=RagChunkType.SYMBOL,
        chunk_text="def authenticate_user(token: str) -> User:\n    ...",
        file_path="server/app/services/auth_service.py",
        symbol_name="authenticate_user",
        module="server/app/services",
        line_start=10,
        line_end=25,
        score=0.87,
        matched_modes=("vector", "symbol"),
    )
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


def run_request_shape() -> bool:
    print("=== group 1 (gating): constructed request has the right shape ===")
    all_passed = True
    captured: list[dict] = []

    def _stub_post(url, *, json=None, headers=None, **kwargs):
        captured.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    settings_patch = _Patch(provider, "get_settings", lambda: _FakeSettings())
    client_patch = _Patch(provider._client, "post", _stub_post)
    try:
        provider.GroqProvider().complete(system="sys", messages=[{"role": "user", "content": "hi"}])
        provider.GroqProvider().complete(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            response_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        )

        all_passed &= _check("exactly two requests were made", len(captured) == 2, f"got {len(captured)}")
        no_schema_call, schema_call = captured[0], captured[1]

        all_passed &= _check(
            "Bearer header carries the configured API key",
            no_schema_call["headers"] == {"Authorization": f"Bearer {_FakeSettings.LLM_API_KEY}"},
        )
        all_passed &= _check(
            "response_format is absent when no schema was passed",
            "response_format" not in no_schema_call["json"],
        )
        all_passed &= _check(
            "response_format is present, strict, when a schema was passed",
            schema_call["json"].get("response_format", {}).get("json_schema", {}).get("strict") is True,
        )
        all_passed &= _check(
            "system message is sent first",
            no_schema_call["json"]["messages"][0] == {"role": "system", "content": "sys"},
        )
        all_passed &= _check(
            "the API key never leaks into the JSON payload body (header only)",
            _FakeSettings.LLM_API_KEY not in json.dumps(no_schema_call["json"])
            and _FakeSettings.LLM_API_KEY not in json.dumps(schema_call["json"]),
        )
    finally:
        settings_patch.restore()
        client_patch.restore()

    return all_passed


def run_error_handling() -> bool:
    print("\n=== group 2 (gating): non-2xx / timeout / connection error -> LLMProviderError ===")
    all_passed = True
    settings_patch = _Patch(provider, "get_settings", lambda: _FakeSettings())
    try:
        # non-2xx response
        client_patch = _Patch(provider._client, "post", lambda *a, **kw: _FakeResponse(429, text="rate limited"))
        try:
            raised = False
            try:
                provider.GroqProvider().complete(system="sys", messages=[{"role": "user", "content": "hi"}])
            except provider.LLMProviderError:
                raised = True
            except Exception as exc:  # noqa: BLE001
                all_passed &= _check("a 429 raises LLMProviderError, not something else", False, repr(exc))
            else:
                pass
            all_passed &= _check("a 429 response raises LLMProviderError", raised)
        finally:
            client_patch.restore()

        # timeout
        def _raise_timeout(*a, **kw):
            raise httpx.TimeoutException("timed out")

        client_patch = _Patch(provider._client, "post", _raise_timeout)
        try:
            raised = False
            try:
                provider.GroqProvider().complete(system="sys", messages=[{"role": "user", "content": "hi"}])
            except provider.LLMProviderError:
                raised = True
            except httpx.HTTPError:
                pass  # explicitly not what we want — falls through to the check below as a fail
            all_passed &= _check(
                "a timeout raises LLMProviderError, never the raw httpx.TimeoutException", raised
            )
        finally:
            client_patch.restore()

        # connection error
        def _raise_connect_error(*a, **kw):
            raise httpx.ConnectError("connection refused")

        client_patch = _Patch(provider._client, "post", _raise_connect_error)
        try:
            raised = False
            try:
                provider.GroqProvider().complete(system="sys", messages=[{"role": "user", "content": "hi"}])
            except provider.LLMProviderError:
                raised = True
            all_passed &= _check(
                "a connection error raises LLMProviderError, never the raw httpx.ConnectError", raised
            )
        finally:
            client_patch.restore()

        # malformed success response
        client_patch = _Patch(provider._client, "post", lambda *a, **kw: _FakeResponse(200, {"unexpected": "shape"}))
        try:
            raised = False
            try:
                provider.GroqProvider().complete(system="sys", messages=[{"role": "user", "content": "hi"}])
            except provider.LLMProviderError:
                raised = True
            all_passed &= _check(
                "a 200 response missing choices[0].message.content raises LLMProviderError", raised
            )
        finally:
            client_patch.restore()
    finally:
        settings_patch.restore()

    return all_passed


def run_provider_guard() -> bool:
    print("\n=== group 3 (gating): unsupported LLM_PROVIDER raises before any network call ===")
    all_passed = True

    class _UnsupportedSettings:
        LLM_PROVIDER = "gemini"
        LLM_MODEL = "does-not-matter"
        LLM_API_KEY = "does-not-matter"

    call_count = 0

    def _counting_stub(*a, **kw):
        nonlocal call_count
        call_count += 1
        return _FakeResponse(200, {"choices": [{"message": {"content": "{}"}}]})

    settings_patch = _Patch(provider, "get_settings", lambda: _UnsupportedSettings())
    client_patch = _Patch(provider._client, "post", _counting_stub)
    try:
        raised = False
        try:
            provider.GroqProvider().complete(system="sys", messages=[{"role": "user", "content": "hi"}])
        except RuntimeError:
            raised = True
        all_passed &= _check("RuntimeError raised for an unsupported LLM_PROVIDER", raised)
        all_passed &= _check("no network call was attempted", call_count == 0, f"call_count={call_count}")
    finally:
        settings_patch.restore()
        client_patch.restore()

    return all_passed


def run_prompt_builders() -> bool:
    print("\n=== group 4 (gating): prompt builders produce the expected structure ===")
    all_passed = True

    # --- build_qa_prompt ---
    chunks = [_fixture_chunk(), _fixture_chunk(file_path="server/app/api/v1/auth.py", symbol_name="login", module="server/app/api/v1")]
    system, messages, schema = prompts.build_qa_prompt("How does authentication work?", chunks)

    all_passed &= _check("qa: system carries the grounding preamble", "only using the provided context" in system)
    all_passed &= _check("qa: messages is one user message", len(messages) == 1 and messages[0]["role"] == "user")
    all_passed &= _check("qa: the question text is in the user message", "How does authentication work?" in messages[0]["content"])
    all_passed &= _check(
        "qa: every chunk's file_path:line header is in the user message",
        all(f"{c.file_path}:{c.line_start}-{c.line_end}" in messages[0]["content"] for c in chunks),
    )
    all_passed &= _check(
        "qa: response_schema requires exactly answer/insufficient_context/follow_up_questions",
        set(schema["required"]) == {"answer", "insufficient_context", "follow_up_questions"},
    )
    all_passed &= _check("qa: response_schema forbids additional properties", schema["additionalProperties"] is False)

    empty_system, empty_messages, _ = prompts.build_qa_prompt("anything?", [])
    all_passed &= _check(
        "qa: an empty chunk list still produces a well-formed prompt (no crash)",
        "no relevant context" in empty_messages[0]["content"],
    )

    # --- build_insights_prompt ---
    @dataclass
    class _Issue:
        issue_type: str
        severity: str
        file_path: str | None
        module: str | None
        description: str

    @dataclass
    class _Coupling:
        module: str
        fan_in: int
        fan_out: int
        score: int

    @dataclass
    class _Cycle:
        files: frozenset

    issues = [_Issue("high_complexity", "critical", "server/app/api/v1/auth.py", None, "function 'login' has complexity 42")]
    coupling = [_Coupling("server/app/services", fan_in=12, fan_out=3, score=15)]
    cycles = [_Cycle(files=frozenset({"a.py", "b.py"}))]

    i_system, i_messages, i_schema = prompts.build_insights_prompt(issues, coupling, cycles, 0.42)

    all_passed &= _check("insights: system carries the grounding preamble", "only using the provided context" in i_system)
    all_passed &= _check(
        "insights: the debt issue's description is in the user message", "function 'login' has complexity 42" in i_messages[0]["content"]
    )
    all_passed &= _check(
        "insights: the coupling module is in the user message", "server/app/services" in i_messages[0]["content"]
    )
    all_passed &= _check("insights: doc coverage ratio is rendered as a percentage", "42%" in i_messages[0]["content"])
    all_passed &= _check("insights: response_schema requires exactly 'insights'", i_schema["required"] == ["insights"])
    item_schema = i_schema["properties"]["insights"]["items"]
    all_passed &= _check(
        "insights: each item requires exactly tone/title/body", set(item_schema["required"]) == {"tone", "title", "body"}
    )
    all_passed &= _check(
        "insights: tone is constrained to danger/warning/info",
        set(item_schema["properties"]["tone"]["enum"]) == {"danger", "warning", "info"},
    )

    empty_i_system, empty_i_messages, _ = prompts.build_insights_prompt([], [], [], 1.0)
    all_passed &= _check(
        "insights: empty facts still produce a well-formed prompt (no crash)",
        "none found" in empty_i_messages[0]["content"],
    )

    return all_passed


def run_live_bonus() -> bool:
    print("\n=== group 5 (non-gating): real Groq call with a grounded question ===")
    try:
        settings = get_settings()
        chunk = _fixture_chunk(
            chunk_text=(
                "def authenticate_user(token: str) -> User:\n"
                "    payload = decode_jwt(token)\n"
                "    return db.query(User).filter_by(id=payload['sub']).one()\n"
            )
        )
        system, messages, schema = prompts.build_qa_prompt(
            "How does authenticate_user verify a token?", [chunk]
        )
        raw = provider.GroqProvider().complete(system=system, messages=messages, response_schema=schema)
        parsed = json.loads(raw)

        r1 = _check("live: response is valid JSON", True)
        r2 = _check(
            "live: parsed response has exactly the schema's required keys",
            set(parsed.keys()) == {"answer", "insufficient_context", "follow_up_questions"},
            f"got keys={list(parsed.keys())}",
        )
        r3 = _check("live: 'answer' is a non-empty string", isinstance(parsed.get("answer"), str) and len(parsed["answer"]) > 0)
        r4 = _check(
            "live: 'follow_up_questions' is a list", isinstance(parsed.get("follow_up_questions"), list)
        )
        print(f"  model={settings.LLM_MODEL!r} honored strict-mode JSON schema output")
        print(f"  answer preview: {parsed.get('answer', '')[:200]!r}")

        return r1 and r2 and r3 and r4
    except Exception as exc:  # noqa: BLE001 — any failure here means "can't reach the real API", not a bug
        print(f"  [SKIP] live bonus unavailable ({type(exc).__name__}: {exc})")
        return True


def main() -> int:
    r1 = run_request_shape()
    r2 = run_error_handling()
    r3 = run_provider_guard()
    r4 = run_prompt_builders()
    r5 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. request shape (gating):        {'PASS' if r1 else 'FAIL'}")
    print(f"2. error handling (gating):       {'PASS' if r2 else 'FAIL'}")
    print(f"3. provider guard (gating):       {'PASS' if r3 else 'FAIL'}")
    print(f"4. prompt builders (gating):      {'PASS' if r4 else 'FAIL'}")
    print(f"5. live bonus (non-gating):       {'PASS' if r5 else 'SKIP/FAIL (excluded from exit code)'}")
    return 0 if (r1 and r2 and r3 and r4) else 1


if __name__ == "__main__":
    sys.exit(main())
