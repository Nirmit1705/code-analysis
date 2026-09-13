"""Prisma datasource-provider detection — a small, targeted addition to
Phase 3, prompted by the Architecture screen (Phase 6.5/6.6) needing to
label a real "Database" component with the actual engine (e.g.
"PostgreSQL") rather than a generic "uses Prisma" guess.

Not a general schema/ORM parser: this reads exactly one fact — the
`provider = "..."` line inside a `schema.prisma`'s `datasource` block —
via a targeted regex, the same "text scan, not full parsing" style
`imports.py::discover_manifest_dependencies` already uses for
`package.json`/`requirements.txt`/etc. (`.prisma` has no Tree-sitter
grammar in this pipeline and isn't one of Phase 3's 5 detected languages,
so this is deliberately outside that machinery, not a gap in it).

Scoped to Prisma only, on purpose: other ORMs (SQLAlchemy, TypeORM,
Mongoose, Drizzle, ...) declare their datasource differently and aren't
detected here. A repo using one of those gets no `DatabaseProvider` at
all — callers must treat that as "unknown," never assume "no database."

Pure with respect to the rest of the app, matching every other module
under `analysis/`: no DB session, no ORM models, no Celery task decorator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches a Prisma `datasource <name> { ... }` block and captures its body
# (non-greedy so a file with multiple blocks — unusual, but not invalid
# Prisma schema syntax — matches each one independently via `finditer`).
_DATASOURCE_BLOCK_RE = re.compile(r"datasource\s+\w+\s*\{([^}]*)\}")
# Within a captured block body, the `provider = "..."` assignment.
_PROVIDER_RE = re.compile(r'provider\s*=\s*"(\w+)"')


@dataclass(frozen=True)
class DatabaseProvider:
    """`engine` is Prisma's own raw datasource-provider value (e.g.
    `"postgresql"`, `"mysql"`, `"sqlite"`, `"sqlserver"`, `"mongodb"`,
    `"cockroachdb"`) — never invented or normalized here; display-label
    mapping is a read-time concern (`app/api/v1/architecture.py`)."""

    engine: str
    manifest_path: str  # POSIX-relative path of the schema.prisma file


def detect_database_provider(files: dict[str, bytes]) -> DatabaseProvider | None:
    """Finds a `schema.prisma` anywhere in `files` and extracts its
    datasource provider. `None` when no such file exists, or one exists
    but its `datasource` block / `provider` line couldn't be found (a
    real "insufficient evidence" outcome, not an error — callers must not
    guess a default in its place). If more than one `schema.prisma`
    exists (a monorepo with separate services), the first match in
    dict-iteration order wins — matches `discover_manifest_dependencies`'s
    own "first real answer wins" stance for an edge case this codebase
    doesn't currently need to resolve more precisely."""
    for file_path, source in files.items():
        if not file_path.endswith("schema.prisma"):
            continue
        try:
            text = source.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — a decode bug must not abort the whole run
            continue
        block_match = _DATASOURCE_BLOCK_RE.search(text)
        if block_match is None:
            continue
        provider_match = _PROVIDER_RE.search(block_match.group(1))
        if provider_match is None:
            continue
        return DatabaseProvider(engine=provider_match.group(1), manifest_path=file_path)
    return None
