"""Shared `DependencyEdge`/`ExternalDependency` representation for Phase
3.7's dependency extraction (`imports.py`). Mirrors `app.analysis.symbols`
and `app.analysis.apis`'s pattern (Phase 3.3-3.6): one small shared
dataclass module here, all the actual parsing logic in the sibling module,
which imports from here rather than the other way around.

Pure with respect to the rest of the app: no DB, no ORM, no FastAPI, no
Celery.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.analysis.language_detect import Language


class DependencyEdgeType(str, enum.Enum):
    # Resolves to another file within the ingested file set (a relative
    # import/require, or an absolute import that matches a real file by
    # suffix — see `imports.py`).
    INTERNAL = "internal"
    # Doesn't resolve to a file in the ingested file set — a third-party
    # package/module (or a relative import that points outside the
    # ingested set, e.g. into an ignored directory; see `imports.py`'s
    # module docstring for that edge case).
    EXTERNAL = "external"


@dataclass(frozen=True)
class DependencyEdge:
    """One file-level import/require dependency, source -> target. Not
    symbol-level — the plan's own `DependencyEdge` bullet ("resolve
    Tree-sitter import/require/from-import nodes to other files... or mark
    as external") is a file-to-file/package fact, matching the later
    Phase 4 `DependencyEdge` DB model's own `source_file_id` grain."""

    source_file: str  # POSIX-relative path of the importing file
    module: str  # source file's containing directory, same convention as Symbol.module
    language: Language
    edge_type: DependencyEdgeType
    target_file: str | None  # set when edge_type is INTERNAL
    target_package: str | None  # set when edge_type is EXTERNAL (a package/module name)
    raw_specifier: str  # the import/require/from-import text exactly as written
    line: int  # 1-indexed line of the import statement


class Ecosystem(str, enum.Enum):
    NPM = "npm"
    PIP = "pip"
    MAVEN = "maven"


@dataclass(frozen=True)
class ExternalDependency:
    """One dependency declared in a project manifest (`package.json`,
    `requirements.txt`, `pyproject.toml`, `pom.xml`) — independent of
    whether anything in the repo actually imports it. Cross-referencing
    this list against `DependencyEdge`s of type EXTERNAL (which packages
    are declared-but-unused, or imported-but-undeclared) is left to a
    later phase's debt-detection rules (`analysis/debt.py`, Phase 4) —
    this module only reports the raw fact of what's declared where."""

    name: str
    version: str | None  # the raw version specifier/string as written, when present
    manifest_path: str  # POSIX-relative path of the manifest file that declared it
    ecosystem: Ecosystem
