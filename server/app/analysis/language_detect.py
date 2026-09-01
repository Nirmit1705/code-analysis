"""Language classification by file extension (Phase 3.2).

A pure function of a file path — no filesystem access, no Tree-sitter
dependency (`parser_registry.py` depends on this module, not the other way
around). Classifies each file into one of this platform's initial-scope
languages; everything else is still counted toward file totals elsewhere in
the pipeline, just never parsed.

`Language` is deliberately finer-grained than the plan's ".ts/.tsx/.js/.jsx
-> JS/TS" prose (`docs/BACKEND_IMPLEMENTATION_PLAN.md`, Phase 3.2): Tree-sitter
has three distinct grammars here, not one — JSX syntax makes a `.tsx` file
ambiguous under the plain TypeScript grammar, so `parser_registry.py` needs
to pick a different compiled parser for `.tsx` than for `.ts`. `group_label`
below recovers the plan's coarser "JS/TS" reporting bucket for anything that
wants to display or aggregate at that level.
"""

from __future__ import annotations

import enum
from pathlib import PurePosixPath


class Language(str, enum.Enum):
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    PYTHON = "python"
    JAVA = "java"

    @property
    def group_label(self) -> str:
        """The coarser "JS/TS" / "Python" / "Java" grouping used in prose
        and stats elsewhere in the plan, collapsing the three JS-family
        grammars back into one reporting bucket."""
        if self in (Language.JAVASCRIPT, Language.TYPESCRIPT, Language.TSX):
            return "JS/TS"
        return "Python" if self is Language.PYTHON else "Java"


# Every extension this platform's initial scope recognizes. `.mjs`/`.cjs`
# and `.mts`/`.cts` are the same grammars as `.js`/`.ts` respectively (just
# explicit-module-system variants of the same syntax); `.pyi` (type stubs)
# parses fine under the regular Python grammar.
_EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".cts": Language.TYPESCRIPT,
    ".tsx": Language.TSX,
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".java": Language.JAVA,
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_EXTENSION_TO_LANGUAGE)


def detect_language(path: str) -> Language | None:
    """Classifies a file by its extension (case-insensitive). Returns
    `None` for anything outside the supported set — such files are still
    counted toward file totals by callers, just not parsed by any Phase 3
    Tree-sitter stage."""
    suffix = PurePosixPath(path).suffix.lower()
    return _EXTENSION_TO_LANGUAGE.get(suffix)
