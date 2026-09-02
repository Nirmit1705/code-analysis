"""Phase 3 final pipeline assembly (3.7): wires ingestion (3.1) -> language
detection + Tree-sitter parsing (3.2) -> symbol extraction (3.3-3.5) ->
framework-aware API extraction (3.6) -> dependency-edge extraction +
manifest discovery + code metrics (3.7's own new modules) into the single
`AnalysisResult` this phase's Definition of Done describes.

Hard constraint (CLAUDE.md, `docs/BACKEND_IMPLEMENTATION_PLAN.md`'s Phase 3
section): `analyze_commit` takes an explicit `commit_sha` — it never
resolves "the latest commit of a branch" itself, and never prompts for
one. That resolution is Phase 4/5's job, done above this module.

Pure with respect to the rest of the app, matching every other module
under `analysis/`: no DB session, no ORM models, no Celery task decorator.
`analyze_commit` calls out to `app.services.github_service` (via
`ingestion.ingest_repository`) for the one real I/O this phase does —
downloading the repository archive — but nothing here imports SQLAlchemy
or a `User`/`Repository` row.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from app.analysis import ingestion, metrics
from app.analysis.apis import ApiRoute
from app.analysis.apis.express import compose_routes as compose_express_routes
from app.analysis.apis.fastapi_flask import compose_routes as compose_fastapi_flask_routes
from app.analysis.apis.spring_boot import compose_routes as compose_spring_boot_routes
from app.analysis.dependencies import DependencyEdge, ExternalDependency
from app.analysis.dependencies.imports import (
    discover_manifest_dependencies,
    discover_ts_path_aliases,
    extract_import_edges,
)
from app.analysis.language_detect import Language, detect_language
from app.analysis.metrics import FileMetrics, FunctionComplexity, ModuleMetrics
from app.analysis.symbols import Symbol
from app.analysis.symbols.java import extract_symbols as extract_java_symbols
from app.analysis.symbols.js_ts import extract_symbols as extract_js_ts_symbols
from app.analysis.symbols.python import extract_symbols as extract_python_symbols

logger = logging.getLogger(__name__)

# One entry per language Phase 3.3-3.5 actually extracts symbols for; the
# three JS-family grammars share `js_ts.extract_symbols` (it dispatches on
# the `language` arg it's given, same as `parser_registry.get_parser`).
_SYMBOL_EXTRACTORS: dict[Language, Callable[..., list[Symbol]]] = {
    Language.JAVASCRIPT: extract_js_ts_symbols,
    Language.TYPESCRIPT: extract_js_ts_symbols,
    Language.TSX: extract_js_ts_symbols,
    Language.PYTHON: extract_python_symbols,
    Language.JAVA: extract_java_symbols,
}


@dataclass(frozen=True)
class AnalysisResult:
    """Every in-memory fact Phase 3 produces for one repository at one
    commit. Nothing here is persisted — Phase 4 maps this onto DB rows."""

    commit_sha: str
    files: list[ingestion.IngestedFile]
    ignored_paths: list[str]
    language_counts: dict[str, int]  # Language.value -> file count, unparsed languages excluded
    symbols: list[Symbol]
    api_routes: list[ApiRoute]
    dependency_edges: list[DependencyEdge]
    external_dependencies: list[ExternalDependency]
    file_metrics: list[FileMetrics]
    function_complexity: list[FunctionComplexity]
    module_metrics: list[ModuleMetrics]


def analyze_commit(
    *,
    installation_token: str,
    owner: str,
    repo: str,
    commit_sha: str,
    ignore_patterns: tuple[str, ...] = ingestion.DEFAULT_IGNORE_PATTERNS,
    max_archive_bytes: int = ingestion.DEFAULT_MAX_ARCHIVE_BYTES,
    max_extracted_bytes: int = ingestion.DEFAULT_MAX_EXTRACTED_BYTES,
    max_file_count: int = ingestion.DEFAULT_MAX_FILE_COUNT,
) -> AnalysisResult:
    """Runs the full Phase 3 pipeline against `repo` at the *exact*
    `commit_sha` given via GitHub's tarball API (Phase 3.1). A thin
    wrapper over `ingest_repository` + `analyze_ingested` below — kept
    separate so the latter (the actual parsing/extraction/metrics logic)
    is independently testable against any already-available file set,
    including this repo's own source read straight off disk, without
    needing live GitHub credentials for every test run.
    """
    with ingestion.ingest_repository(
        installation_token=installation_token,
        owner=owner,
        repo=repo,
        commit_sha=commit_sha,
        ignore_patterns=ignore_patterns,
        max_archive_bytes=max_archive_bytes,
        max_extracted_bytes=max_extracted_bytes,
        max_file_count=max_file_count,
    ) as ingestion_result:
        return analyze_ingested(ingestion_result, commit_sha=commit_sha)


def analyze_ingested(ingestion_result: ingestion.IngestionResult, *, commit_sha: str) -> AnalysisResult:
    """The Phase 3.2-3.7 analysis pipeline over an already-ingested file
    set — 3.1's own `IngestionResult`, or an equivalent one built another
    way (any caller that already has `IngestedFile`s, e.g. pointing
    straight at files already on disk)."""
    files_by_path: dict[str, bytes] = {}
    file_metrics: list[FileMetrics] = []
    language_counts: Counter[str] = Counter()

    for ingested_file in ingestion_result.files:
        language = detect_language(ingested_file.path)
        try:
            source = ingested_file.absolute_path.read_bytes()
        except OSError as exc:
            # e.g. a file that vanished or became unreadable between
            # ingestion and analysis — skip it rather than fail the whole run.
            logger.warning("skipping unreadable file %r: %s", ingested_file.path, exc)
            continue

        files_by_path[ingested_file.path] = source
        file_metrics.append(metrics.compute_file_metrics(source, file_path=ingested_file.path, language=language))
        if language is not None:
            language_counts[language.value] += 1

    symbols: list[Symbol] = []
    symbols_by_file: dict[str, list[Symbol]] = {}
    for file_path, source in files_by_path.items():
        language = detect_language(file_path)
        extractor = _SYMBOL_EXTRACTORS.get(language) if language is not None else None
        if extractor is None:
            continue
        file_symbols = extractor(source, file_path=file_path, language=language)
        symbols_by_file[file_path] = file_symbols
        symbols.extend(file_symbols)

    # Each `compose_routes` already filters `files_by_path` down to its own
    # framework's language(s) internally (see each module's own docstring),
    # so the same full maps are passed to all three rather than pre-split.
    api_routes: list[ApiRoute] = [
        *compose_express_routes(files_by_path, symbols_by_file=symbols_by_file),
        *compose_fastapi_flask_routes(files_by_path, symbols_by_file=symbols_by_file),
        *compose_spring_boot_routes(files_by_path, symbols_by_file=symbols_by_file),
    ]

    ts_path_aliases = discover_ts_path_aliases(files_by_path)
    dependency_edges = extract_import_edges(files_by_path, ts_path_aliases=ts_path_aliases)
    external_dependencies = discover_manifest_dependencies(files_by_path)

    function_complexity: list[FunctionComplexity] = []
    for file_path, source in files_by_path.items():
        language = detect_language(file_path)
        if language is None:
            continue
        function_complexity.extend(
            metrics.compute_function_complexity(
                source, file_path=file_path, language=language, symbols=symbols_by_file.get(file_path, [])
            )
        )

    module_metrics = metrics.aggregate_module_metrics(file_metrics, function_complexity)

    return AnalysisResult(
        commit_sha=commit_sha,
        files=ingestion_result.files,
        ignored_paths=ingestion_result.ignored_paths,
        language_counts=dict(language_counts),
        symbols=symbols,
        api_routes=api_routes,
        dependency_edges=dependency_edges,
        external_dependencies=external_dependencies,
        file_metrics=file_metrics,
        function_complexity=function_complexity,
        module_metrics=module_metrics,
    )
