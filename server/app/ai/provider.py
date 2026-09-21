"""LLM provider abstraction (Phase 8.1) — a minimal interface over a chat-
completions API, plus one concrete Groq-backed implementation.

Mirrors `app/services/github_service.py`'s established shape exactly: a
module-level `httpx.Client` (one shared connection pool for the process
lifetime), a custom exception type wrapping every non-2xx/timeout/connection
failure so a raw `httpx` exception never escapes the module, and no
third-party SDK dependency — Groq's API is OpenAI-compatible, so a direct
`httpx` call is all that's needed (this codebase's established stance on
avoiding a dependency for something a direct HTTP call already covers, see
also `analysis/chunking.py`'s own markdown-parsing-library avoidance).

No factory/registry — `GroqProvider` guards `settings.LLM_PROVIDER` inline
and raises `RuntimeError` for anything unsupported, matching
`rag/embeddings.py`/`rag/reranker.py`'s established per-module provider
guard (`RuntimeError` for a misconfigured provider, distinct from
`LLMProviderError`, which is reserved for a real request/response failure
against the configured provider).
"""

from typing import Protocol

import httpx

from app.core.config import get_settings

GROQ_API_BASE = "https://api.groq.com/openai/v1"
REQUEST_TIMEOUT = 60.0

# Shared connection pool for the process lifetime, rather than opening a new
# TCP/TLS connection per call — same pattern as `github_service.py::_client`.
_client = httpx.Client(base_url=GROQ_API_BASE, timeout=REQUEST_TIMEOUT)


class LLMProviderError(Exception):
    """Raised when the LLM API returns a non-2xx response, the request times
    out/fails to connect, or the response is missing the fields a successful
    call is expected to have. Never a raw `httpx` exception."""


class LLMProvider(Protocol):
    def complete(
        self, *, system: str, messages: list[dict[str, str]], response_schema: dict | None = None
    ) -> str:
        """Returns the raw text of the model's response — a JSON string
        conforming to `response_schema` when one was given, plain text
        otherwise. Raises `LLMProviderError` on any provider-side failure."""
        ...


class GroqProvider:
    """Calls Groq's OpenAI-compatible `POST /chat/completions` endpoint
    directly (`Authorization: Bearer {LLM_API_KEY}` header, not a
    query-string key — a real difference from this project's earlier Gemini
    plan, worth getting right on the first attempt). When `response_schema`
    is given, requests Groq's strict-mode structured output
    (`response_format: {type: "json_schema", json_schema: {..., strict:
    True}}`) — genuine constrained decoding, not a best-effort hint."""

    def complete(
        self, *, system: str, messages: list[dict[str, str]], response_schema: dict | None = None
    ) -> str:
        settings = get_settings()
        if settings.LLM_PROVIDER != "groq":
            raise RuntimeError(
                f"LLM_PROVIDER={settings.LLM_PROVIDER!r} is not supported — "
                "only 'groq' is implemented (app/ai/provider.py)"
            )

        payload: dict = {
            "model": settings.LLM_MODEL,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": response_schema, "strict": True},
            }

        try:
            response = _client.post(
                "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            )
        except httpx.TimeoutException as exc:
            raise LLMProviderError(f"Groq API request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Groq API request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMProviderError(f"Groq API error ({response.status_code}): {response.text}")

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(f"Groq API response missing expected fields: {data!r}") from exc
