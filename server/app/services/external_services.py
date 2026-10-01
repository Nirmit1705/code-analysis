"""Recognition of real imported external packages as named architecture
services (Database / Redis / curated SaaS SDKs), shared by the Architecture
graph (`api/v1/architecture.py`) and the API Reference "what it touches" panel
(Phase 10.1) so both classify a package identically. Extracted from
`architecture.py` unchanged (docs/PHASE_10_PLAN.md decision #6) — same
precedent as `module_classification.py`.
"""

from __future__ import annotations

from app.schemas.architecture import ModuleKindLiteral

# Synthetic node id for the generic "external packages" catch-all node —
# a package that's real and external but not individually recognized
# below. Can never collide with a real module's id (every real group id
# is either a role-based key, a path fallback, or an "other:<kind>" fold —
# never the bare string "external").
EXTERNAL_NODE_ID = "external"

# --- named external-service recognition (Phase 6.5/6.6 follow-up) -----------
#
# Replaces a single generic "external" blob with recognized, individually
# labeled components when there's real evidence for one — grounded in
# persisted `DependencyEdge` facts (which package a file actually imports),
# never guessed from a route path or file name. A curated, documented MVP
# vocabulary, same spirit as `analysis/debt.py`'s own well-known-package
# table — a real external service this doesn't recognize still shows up
# honestly in the generic "External Dependencies" catch-all, not hidden.

# Prisma's real engine (from `AnalysisRun.database_provider`, set by
# `analysis/datasource.py` at analysis time) maps to a proper display name;
# unrecognized/absent falls back to a still-honest generic label.
_DB_PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "sqlserver": "SQL Server",
    "mongodb": "MongoDB",
    "cockroachdb": "CockroachDB",
}

# Known SaaS/infra service SDKs -> display label. Not exhaustive — an
# unrecognized real external package still shows up honestly in the
# generic "External Dependencies" node below, never hidden or guessed at.
_KNOWN_EXTERNAL_SERVICES: dict[str, str] = {
    "@deepgram/sdk": "Deepgram",
    "openai": "OpenAI",
    "@anthropic-ai/sdk": "Anthropic",
    "stripe": "Stripe",
    "twilio": "Twilio",
    "@sendgrid/mail": "SendGrid",
    "sendgrid": "SendGrid",
    "aws-sdk": "AWS",
    "googleapis": "Google Cloud",
    "@supabase/supabase-js": "Supabase",
}
# Direct DB drivers (not via Prisma) — their own node, kind=data, same as
# the Prisma-derived database node below but never merged with it (no
# reliable signal that they point at the same physical database).
_KNOWN_DATABASE_PACKAGES: dict[str, str] = {
    "pg": "PostgreSQL",
    "mysql2": "MySQL",
    "mongodb": "MongoDB",
    "mongoose": "MongoDB",
}
_KNOWN_CACHE_PACKAGES = {"ioredis", "redis"}

# Not real external services: Node.js builtins and dev/build-only tooling.
# Dropped from the external surface entirely — showing them as
# "architecture" would be noise, not signal.
_EXCLUDED_EXTERNAL_PACKAGES: set[str] = {
    # Node.js builtins.
    "fs", "path", "util", "os", "http", "https", "crypto", "stream",
    "events", "child_process", "url", "querystring", "buffer", "assert",
    "net", "zlib", "readline", "timers", "process",
    # Dev/build-only tooling.
    "vite", "eslint", "typescript-eslint", "prettier", "jest", "ts-jest",
    "ts-node", "nodemon", "supertest", "husky", "lint-staged", "globals",
    "typescript", "@vitejs/plugin-react", "@eslint/js",
    "eslint-plugin-react-hooks", "eslint-plugin-react-refresh",
}


def _database_node_id_and_label(database_provider: str | None) -> tuple[str, str]:
    if database_provider is None:
        return "data:database", "Database (Prisma)"
    label = _DB_PROVIDER_DISPLAY_NAMES.get(database_provider, database_provider.title())
    return "data:database", label


def resolve_external_target(
    package: str, database_provider: str | None
) -> tuple[str, str, ModuleKindLiteral] | None:
    """Maps one real, imported external package to the synthetic node it
    should route to — or `None` if it's excluded entirely (a language
    builtin or dev/build tooling, not real runtime architecture)."""
    if package in _EXCLUDED_EXTERNAL_PACKAGES:
        return None
    if package == "@prisma/client":
        node_id, label = _database_node_id_and_label(database_provider)
        return node_id, label, "data"
    if package in _KNOWN_DATABASE_PACKAGES:
        return f"data:{package}", _KNOWN_DATABASE_PACKAGES[package], "data"
    if package in _KNOWN_CACHE_PACKAGES:
        return "cache:redis", "Redis", "cache"
    if package in _KNOWN_EXTERNAL_SERVICES:
        node_id = "external:" + package.lstrip("@").replace("/", "-")
        return node_id, _KNOWN_EXTERNAL_SERVICES[package], "external"
    return EXTERNAL_NODE_ID, "External Dependencies", "external"
