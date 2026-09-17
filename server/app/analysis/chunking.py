"""Phase 7.2: pure RAG-chunk text + metadata extraction, built entirely
from Phase 3's already-computed facts (symbols, API routes, file metrics,
dependency edges) — no arbitrary fixed-size splitting of raw source.

Pure with respect to the rest of the app, matching every other module
under `analysis/`: no DB session, no ORM models, no Celery task decorator,
no embedding-model call. `rag/` (Phase 7.4+) is where embedding, reranking,
and retrieval — the parts that actually touch a model or the DB — live;
this module only ever returns plain `RagChunkDraft` dataclasses.

Four chunk types, each producing zero or more `RagChunkDraft`s:
- `SYMBOL` — one per extracted `Symbol` (function/class/method/component).
- `API_ENDPOINT` — one per extracted `ApiRoute`, kept distinct from SYMBOL
  since a route is a different kind of fact than a code unit.
- `FILE_OVERVIEW` — only for a file with >=1 symbol (a synthesized
  structural summary, never a raw dump — that would just duplicate the
  file's own SYMBOL chunks) or a file matching a fixed manifest-filename
  allowlist (raw content, already short). README is deliberately excluded
  from this allowlist — see DOCUMENTATION below, keeping it in both would
  produce duplicate near-identical chunks for the same content.
- `DOCUMENTATION` — `README*` and `docs/**/*.{md,mdx}` only, split on
  markdown heading boundaries (a plain, fence-aware regex scan, not a full
  markdown parser — this platform's "don't add unnecessary technologies"
  stance applies here too).

Every builder routes through one shared `_build_chunk_group` splitter, so
an oversized body (rare in practice — mainly a safety net for a huge
README section or a monolithic manifest file, not expected to trigger for
a single real function/route given the target embedding model's ~8192
token input ceiling) is handled identically everywhere, producing ordered,
overlapping, line-range-accurate sub-chunks rather than one enormous one.
"""

from __future__ import annotations

import enum
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.analysis.apis import ApiRoute
from app.analysis.binary_detect import is_binary
from app.analysis.dependencies import DependencyEdge, DependencyEdgeType
from app.analysis.language_detect import Language
from app.analysis.metrics import FileMetrics
from app.analysis.symbols import Symbol

# Under this many characters (header + body combined), a chunk group is a
# single RagChunkDraft. Deliberately small relative to the embedding
# model's real ~8192-token ceiling — the goal is retrieval precision (a
# smaller, more focused chunk embeds to a more distinctive vector), not
# staying under the model's hard limit.
MAX_CHUNK_CHARS = 4000
# Soft per-window target once splitting is actually triggered.
SPLIT_WINDOW_CHARS = 1200
# Soft overlap target between consecutive split windows.
SPLIT_OVERLAP_CHARS = 200

# Exact basename match, case-sensitive — the conventional casing for each
# of these. README is intentionally NOT here; see the module docstring.
_MANIFEST_BASENAMES = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "tsconfig.json",
        "docker-compose.yml",
        "docker-compose.yaml",
        "schema.prisma",
        "Dockerfile",
        "go.mod",
        "pom.xml",
        ".env.example",
    }
)

# Matched against the basename stem (no extension), lowercased, with "-"
# normalized to "_" — low-value doc content excluded even under docs/**.
_EXCLUDED_DOC_STEMS = frozenset(
    {"changelog", "license", "contributing", "code_of_conduct", "security"}
)

# Up to 3 leading spaces allowed (CommonMark's own allowance for a
# heading), at least one char of heading text after the #s.
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+\S")


class RagChunkType(str, enum.Enum):
    SYMBOL = "symbol"
    API_ENDPOINT = "api_endpoint"
    FILE_OVERVIEW = "file_overview"
    DOCUMENTATION = "documentation"


@dataclass(frozen=True)
class RagChunkDraft:
    """One chunk's text + metadata, with no embedding yet and no DB id
    resolved yet. `file_path`/`symbol` are resolved to real `File.id`/
    `Symbol.id` foreign keys by Phase 7.5's `index_analysis_run`, reusing
    the exact `file_id_by_path`/`symbol_id_by_symbol` lookup maps
    `persist_analysis_run` already builds (Phase 7.1) — `symbol` carries
    the real `Symbol` instance, not just its name, since that map is keyed
    by the frozen `Symbol` object itself.
    """

    chunk_type: RagChunkType
    chunk_text: str
    file_path: str
    symbol: Symbol | None
    module: str
    language: Language | None
    line_start: int
    line_end: int
    chunk_index: int  # 0-based
    chunk_count: int
    # JSON-primitive values only (str/int/float/bool/list/dict/None) —
    # this becomes JSONB storage in Phase 7.3, never a Symbol/Path/enum.
    metadata: dict[str, object]


def build_rag_chunks(
    *,
    files_by_path: dict[str, bytes],
    symbols_by_file: dict[str, list[Symbol]],
    api_routes: list[ApiRoute],
    file_metrics: list[FileMetrics],
    dependency_edges: list[DependencyEdge],
) -> list[RagChunkDraft]:
    """Builds every chunk for one analysis pass. Fixed order: symbol,
    api_endpoint, file_overview, documentation — not load-bearing for
    correctness, just deterministic for tests/debugging."""
    metrics_by_file = {fm.file_path: fm for fm in file_metrics}
    dependencies_by_file = _dependencies_by_file(dependency_edges)

    chunks: list[RagChunkDraft] = []
    chunks.extend(_build_symbol_chunks(files_by_path, symbols_by_file, dependencies_by_file))
    chunks.extend(_build_api_endpoint_chunks(files_by_path, api_routes, metrics_by_file, dependencies_by_file))
    chunks.extend(_build_file_overview_chunks(files_by_path, symbols_by_file, file_metrics, dependencies_by_file))
    chunks.extend(_build_documentation_chunks(files_by_path, metrics_by_file))
    return chunks


def _dependencies_by_file(dependency_edges: list[DependencyEdge]) -> dict[str, list[str]]:
    """Per-file outgoing dependency targets (internal file paths or
    external package names), deduped and sorted for determinism. File-
    grain only, matching `DependencyEdge` itself (Known Issue #7) — not
    attributable to a specific symbol, so this is only ever attached to
    SYMBOL/API_ENDPOINT/FILE_OVERVIEW chunk metadata, never DOCUMENTATION."""
    by_file: dict[str, set[str]] = defaultdict(set)
    for edge in dependency_edges:
        target = edge.target_file if edge.edge_type is DependencyEdgeType.INTERNAL else edge.target_package
        if target:
            by_file[edge.source_file].add(target)
    return {file_path: sorted(targets) for file_path, targets in by_file.items()}


def _decode(source: bytes) -> str:
    return source.decode("utf-8", errors="replace")


def _slice_lines(lines: list[str], line_start: int, line_end: int) -> tuple[list[str], int]:
    """Clamps a 1-indexed inclusive [line_start, line_end] range into
    `lines`, returning the sliced lines plus the clamped start line. Always
    returns at least one (possibly empty-string) line."""
    if not lines:
        return [""], max(1, line_start)
    start = max(1, min(line_start, len(lines)))
    end = max(start, min(line_end, len(lines)))
    return lines[start - 1 : end], start


def _build_symbol_chunks(
    files_by_path: dict[str, bytes],
    symbols_by_file: dict[str, list[Symbol]],
    dependencies_by_file: dict[str, list[str]],
) -> list[RagChunkDraft]:
    chunks: list[RagChunkDraft] = []
    for file_path, symbols in symbols_by_file.items():
        source = files_by_path.get(file_path)
        if source is None:
            continue
        lines = _decode(source).splitlines()
        for symbol in symbols:
            body_lines, clamped_start = _slice_lines(lines, symbol.line_start, symbol.line_end)
            header = f"[{symbol.kind.value.upper()}] {symbol.name}\nfile: {file_path}\nmodule: {symbol.module}"
            metadata: dict[str, object] = {"dependencies": dependencies_by_file.get(file_path, [])}
            chunks.extend(
                _build_chunk_group(
                    chunk_type=RagChunkType.SYMBOL,
                    header=header,
                    body_lines=body_lines,
                    source_line_start=clamped_start,
                    file_path=file_path,
                    symbol=symbol,
                    module=symbol.module,
                    language=symbol.language,
                    metadata=metadata,
                )
            )
    return chunks


def _build_api_endpoint_chunks(
    files_by_path: dict[str, bytes],
    api_routes: list[ApiRoute],
    metrics_by_file: dict[str, FileMetrics],
    dependencies_by_file: dict[str, list[str]],
) -> list[RagChunkDraft]:
    chunks: list[RagChunkDraft] = []
    for route in api_routes:
        source = files_by_path.get(route.file_path)
        if source is None:
            continue
        lines = _decode(source).splitlines()
        body_lines, clamped_start = _slice_lines(lines, route.line_start, route.line_end)
        header = (
            f"[API_ENDPOINT] {route.method} {route.path}\n"
            f"framework: {route.framework.value}\n"
            f"file: {route.file_path}\n"
            f"handler: {route.handler_name}"
        )
        fm = metrics_by_file.get(route.file_path)
        language = fm.language if fm is not None else None
        metadata: dict[str, object] = {"dependencies": dependencies_by_file.get(route.file_path, [])}
        chunks.extend(
            _build_chunk_group(
                chunk_type=RagChunkType.API_ENDPOINT,
                header=header,
                body_lines=body_lines,
                source_line_start=clamped_start,
                file_path=route.file_path,
                symbol=route.handler_symbol,
                module=route.module,
                language=language,
                metadata=metadata,
            )
        )
    return chunks


def _build_file_overview_chunks(
    files_by_path: dict[str, bytes],
    symbols_by_file: dict[str, list[Symbol]],
    file_metrics: list[FileMetrics],
    dependencies_by_file: dict[str, list[str]],
) -> list[RagChunkDraft]:
    chunks: list[RagChunkDraft] = []
    for fm in file_metrics:
        source = files_by_path.get(fm.file_path)
        if source is None or is_binary(source):
            continue

        basename = PurePosixPath(fm.file_path).name
        symbols = symbols_by_file.get(fm.file_path, [])
        is_manifest = basename in _MANIFEST_BASENAMES
        if not symbols and not is_manifest:
            continue

        language_label = fm.language.value if fm.language is not None else "unknown"
        header = f"[FILE_OVERVIEW] {fm.file_path}\nmodule: {fm.module}\nlanguage: {language_label}"
        metadata: dict[str, object] = {"dependencies": dependencies_by_file.get(fm.file_path, [])}

        if symbols:
            body_lines = [f"Lines of code: {fm.loc}", "", "Symbols in this file:"]
            for symbol in sorted(symbols, key=lambda s: s.line_start):
                body_lines.append(f"- {symbol.kind.value} {symbol.name} (lines {symbol.line_start}-{symbol.line_end})")
        else:
            body_lines = _decode(source).splitlines() or [""]

        chunks.extend(
            _build_chunk_group(
                chunk_type=RagChunkType.FILE_OVERVIEW,
                header=header,
                body_lines=body_lines,
                source_line_start=1,
                file_path=fm.file_path,
                symbol=None,
                module=fm.module,
                language=fm.language,
                metadata=metadata,
            )
        )
    return chunks


def _is_readme(file_path: str) -> bool:
    return PurePosixPath(file_path).name.upper().startswith("README")


def _is_docs_markdown(file_path: str) -> bool:
    posix = PurePosixPath(file_path)
    if posix.suffix.lower() not in (".md", ".mdx"):
        return False
    return "docs" in posix.parts[:-1]


def _is_excluded_doc(file_path: str) -> bool:
    stem = PurePosixPath(file_path).stem.lower().replace("-", "_")
    return stem in _EXCLUDED_DOC_STEMS


def _find_heading_line_indices(lines: list[str]) -> list[int]:
    """0-indexed line numbers that are real markdown headings — fence-
    aware, so a `#`-prefixed comment line inside a code block (very common
    in real READMEs) is never misread as a heading."""
    indices: list[int] = []
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _HEADING_RE.match(line):
            indices.append(i)
    return indices


def _split_markdown_sections(text: str) -> list[tuple[list[str], int]]:
    """Returns `[(section_lines, section_line_start), ...]`, 1-indexed.
    A file with no headings at all is one whole-file section; leading
    content before the first heading becomes its own section when
    non-empty."""
    lines = text.splitlines()
    if not lines:
        return []
    heading_indices = _find_heading_line_indices(lines)
    if not heading_indices:
        return [(lines, 1)]

    sections: list[tuple[list[str], int]] = []
    if heading_indices[0] > 0:
        leading = lines[: heading_indices[0]]
        if any(line.strip() for line in leading):
            sections.append((leading, 1))
    for idx, start in enumerate(heading_indices):
        end = heading_indices[idx + 1] if idx + 1 < len(heading_indices) else len(lines)
        sections.append((lines[start:end], start + 1))
    return sections


def _build_documentation_chunks(
    files_by_path: dict[str, bytes],
    metrics_by_file: dict[str, FileMetrics],
) -> list[RagChunkDraft]:
    chunks: list[RagChunkDraft] = []
    for file_path, source in files_by_path.items():
        if not (_is_readme(file_path) or _is_docs_markdown(file_path)):
            continue
        if _is_excluded_doc(file_path):
            continue
        if is_binary(source):
            continue

        fm = metrics_by_file.get(file_path)
        module = fm.module if fm is not None else "."
        header = f"[DOCUMENTATION] {file_path}"

        for section_lines, section_line_start in _split_markdown_sections(_decode(source)):
            if not any(line.strip() for line in section_lines):
                continue
            metadata: dict[str, object] = {}
            chunks.extend(
                _build_chunk_group(
                    chunk_type=RagChunkType.DOCUMENTATION,
                    header=header,
                    body_lines=section_lines,
                    source_line_start=section_line_start,
                    file_path=file_path,
                    symbol=None,
                    module=module,
                    language=None,
                    metadata=metadata,
                )
            )
    return chunks


def _build_chunk_group(
    *,
    chunk_type: RagChunkType,
    header: str,
    body_lines: list[str],
    source_line_start: int,
    file_path: str,
    symbol: Symbol | None,
    module: str,
    language: Language | None,
    metadata: dict[str, object],
) -> list[RagChunkDraft]:
    """The one shared splitter every builder routes through. Operates on
    whole lines, never raw character offsets — a character-sliced window
    would make `line_start`/`line_end` ambiguous the moment a boundary
    fell mid-line. Under `MAX_CHUNK_CHARS` (header+body combined) this is
    always exactly one `RagChunkDraft`; the header is repeated verbatim on
    every window if splitting is triggered."""
    if not body_lines:
        body_lines = [""]

    body_char_len = sum(len(line) + 1 for line in body_lines)
    if len(header) + 2 + body_char_len <= MAX_CHUNK_CHARS:
        windows = [(body_lines, source_line_start, source_line_start + len(body_lines) - 1)]
    else:
        windows = _split_lines_into_windows(body_lines, source_line_start)

    count = len(windows)
    drafts: list[RagChunkDraft] = []
    for idx, (window_lines, line_start, line_end) in enumerate(windows):
        chunk_text = f"{header}\n\n" + "\n".join(window_lines)
        drafts.append(
            RagChunkDraft(
                chunk_type=chunk_type,
                chunk_text=chunk_text,
                file_path=file_path,
                symbol=symbol,
                module=module,
                language=language,
                line_start=line_start,
                line_end=line_end,
                chunk_index=idx,
                chunk_count=count,
                metadata=metadata,
            )
        )
    return drafts


def _split_lines_into_windows(
    body_lines: list[str], source_line_start: int
) -> list[tuple[list[str], int, int]]:
    """Greedily packs whole lines into ~`SPLIT_WINDOW_CHARS`-sized windows
    (always >=1 line per window, so one pathological giant line still
    terminates the loop), with a ~`SPLIT_OVERLAP_CHARS` line-based overlap
    between consecutive windows. Each window's start strictly advances
    past the previous window's start, guaranteeing termination in at most
    `len(body_lines)` iterations. Returns `[(window_lines, line_start,
    line_end), ...]` with exact, unambiguous absolute line numbers."""
    n = len(body_lines)
    windows: list[tuple[list[str], int, int]] = []
    start_idx = 0
    while start_idx < n:
        size = 0
        end_idx = start_idx
        while end_idx < n:
            line_len = len(body_lines[end_idx]) + 1
            if size + line_len > SPLIT_WINDOW_CHARS and end_idx > start_idx:
                break
            size += line_len
            end_idx += 1

        window_lines = body_lines[start_idx:end_idx]
        windows.append((window_lines, source_line_start + start_idx, source_line_start + end_idx - 1))

        if end_idx >= n:
            break

        overlap_size = 0
        next_start = end_idx
        while next_start > start_idx + 1 and overlap_size < SPLIT_OVERLAP_CHARS:
            next_start -= 1
            overlap_size += len(body_lines[next_start]) + 1
        start_idx = next_start

    return windows
