"""Tree-sitter parser registry (Phase 3.2): maps a `Language`
(`app/analysis/language_detect.py`) to a compiled Tree-sitter `Parser`.

Each grammar is compiled once and cached for the process lifetime via
`lru_cache` — grammars are static per language, so there's no reason to
rebuild a `Parser` per file. This also satisfies the plan's "loaded once at
worker startup" framing without needing an actual worker to exist yet
(Phase 3 has none): the cache fills lazily on first use, which is exactly
equivalent to eager loading for a synchronous, single-process pipeline.
`warm_up_all()` below is provided for Phase 5's Celery worker to call
explicitly at startup once that exists, if eager loading is ever preferred
over lazy.

Pure with respect to the rest of the app: no DB, no FastAPI, no Celery.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

import tree_sitter_java as _tsjava
import tree_sitter_javascript as _tsjavascript
import tree_sitter_python as _tspython
import tree_sitter_typescript as _tstypescript
from tree_sitter import Language as TSLanguage
from tree_sitter import Parser

from app.analysis.language_detect import Language, detect_language

# Each loader returns the raw grammar capsule `tree_sitter.Language(...)`
# expects — one loader per `Language` member, matching 1:1 since `Language`
# was already defined at Tree-sitter's actual grammar granularity (see
# language_detect.py's module docstring for why TS/TSX/JS are kept distinct).
_GRAMMAR_LOADERS: dict[Language, Callable[[], object]] = {
    Language.JAVASCRIPT: _tsjavascript.language,
    Language.TYPESCRIPT: _tstypescript.language_typescript,
    Language.TSX: _tstypescript.language_tsx,
    Language.PYTHON: _tspython.language,
    Language.JAVA: _tsjava.language,
}


class UnsupportedLanguageError(Exception):
    """Raised when asked for a parser for a `Language` with no registered
    grammar loader — shouldn't happen in practice since `Language` and
    `_GRAMMAR_LOADERS` are defined 1:1, but guards against the two drifting
    apart if `Language` ever grows a member before its loader is wired up."""


@lru_cache(maxsize=None)
def get_parser(language: Language) -> Parser:
    """Returns a compiled `Parser` for `language`, built once and reused
    for every subsequent call with the same language. A `Parser` instance
    is safe to reuse across many sequential `.parse(...)` calls (each
    returns a fresh `Tree`); it is not safe to share across concurrent
    threads without external locking, which doesn't arise in Phase 3's
    synchronous pipeline.
    """
    loader = _GRAMMAR_LOADERS.get(language)
    if loader is None:
        raise UnsupportedLanguageError(f"no Tree-sitter grammar registered for {language!r}")
    return Parser(TSLanguage(loader()))


def get_parser_for_file(path: str) -> tuple[Language, Parser] | None:
    """Convenience combining `language_detect.detect_language` with
    `get_parser` in one call. Returns `None` (rather than raising) for a
    file outside the supported languages, matching `detect_language`'s own
    "not every file is parseable" contract."""
    language = detect_language(path)
    if language is None:
        return None
    return language, get_parser(language)


def warm_up_all() -> list[Language]:
    """Eagerly builds and caches a `Parser` for every registered language.
    Not required for correctness (lazy caching in `get_parser` already
    means each grammar compiles at most once) — useful for a future worker
    startup hook (Phase 5) that wants the first real parse call to not pay
    a one-time compilation cost, and for smoke-testing that every
    registered grammar loads cleanly. Returns the list of languages warmed.
    """
    for language in _GRAMMAR_LOADERS:
        get_parser(language)
    return list(_GRAMMAR_LOADERS)
