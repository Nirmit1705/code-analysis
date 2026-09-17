"""Manual smoke test for Phase 7.2's `app.analysis.chunking`.

Group 1 reuses the `fastapi_flask` fixture already hand-verified by
`smoke_analysis.py`/`smoke_apis_fastapi_flask.py` (this codebase's
established "reuse existing fixtures, don't invent new algorithmic ones"
convention) rather than building yet another symbol-extraction fixture
from scratch. Groups 2-4 hand-build small dataclass fixtures directly
(`files_by_path`/`symbols_by_file`/`file_metrics`), the same style
`smoke_debt.py` uses for `analyze_technical_debt`'s pure-function inputs —
`chunking.py`'s builders operate on exactly those already-extracted facts,
not on real Tree-sitter parsing, so there's no need to go through the real
pipeline for them.

Five groups:

1. `run_symbol_and_api_endpoint_chunks()` — real `fastapi_flask` fixture
   through the real `analyze_ingested`. Asserts first-piece coverage
   (`chunk_index == 0`) equals `len(result.symbols)`/`len(result.api_routes)`
   — robust to the oversized-chunk splitter firing on any one real symbol,
   unlike a raw total-count equality — plus real `chunk_text`/`metadata`
   content for two known fixture symbols/routes (`register`/`POST
   /api/v1/auth/register`, `root`/`GET /`).
2. `run_file_overview_scoping()` — hand-built fixture covering: a
   symbol-owning file, a manifest file with no symbols, an unrelated file,
   a binary file that happens to match a manifest name, and a README with
   no symbols — locking in the builder-3/builder-4 dedup fix (README is
   DOCUMENTATION-only, never also FILE_OVERVIEW).
3. `run_markdown_heading_split()` — hand-built markdown text: pre-heading
   content, several real headings, a fenced code block containing a
   `#`-prefixed comment (locks in fence-awareness), and one artificially
   oversized section to also exercise the shared splitter from the
   markdown path.
4. `run_oversized_symbol_split()` — one very large hand-built symbol body
   to force a real multi-piece split; asserts exact, deterministic line-
   range coverage (not "roughly reconstructs").
5. `run_live_bonus()` (non-gating) — real `Nirmit1705/HireSight` via
   `pipeline.analyze_commit`, mirroring `smoke_analysis.py::run_live_analyze_commit`'s
   exact skip-if-no-installation pattern.

Run: `uv run python scripts/smoke_chunking.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import pipeline  # noqa: E402
from app.analysis.chunking import RagChunkType, build_rag_chunks  # noqa: E402
from app.analysis.ingestion import IngestedFile, IngestionResult  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.metrics import FileMetrics  # noqa: E402
from app.analysis.symbols import Symbol, SymbolKind  # noqa: E402
from app.services import github_service  # noqa: E402

SERVER_ROOT = Path(__file__).resolve().parent.parent
FASTAPI_FIXTURES_DIR = SERVER_ROOT / "scripts" / "fixtures" / "fastapi_flask"

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _ingested_file(base: Path, rel_path: str) -> IngestedFile:
    absolute = base / rel_path
    return IngestedFile(path=rel_path, absolute_path=absolute, size_bytes=absolute.stat().st_size)


def make_symbol(
    name: str,
    file_path: str,
    module: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    line_start: int = 1,
    line_end: int = 2,
    has_docstring: bool = False,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        module=module,
        language=Language.PYTHON,
        has_docstring=has_docstring,
    )


def run_symbol_and_api_endpoint_chunks() -> bool:
    print("=== group 1: symbol/API-endpoint chunk coverage + content correctness (fastapi_flask fixture) ===")
    all_passed = True

    rel_paths = ["simple_app.py", "app/api/v1/auth.py", "app/api/v1/items.py", "app/api/v1/router.py"]
    files = [_ingested_file(FASTAPI_FIXTURES_DIR, rel) for rel in rel_paths]
    ingestion_result = IngestionResult(root_dir=FASTAPI_FIXTURES_DIR, files=files, ignored_paths=[])
    result = pipeline.analyze_ingested(ingestion_result, commit_sha="chunking0000000000000000000000000000000")

    symbol_chunks = [c for c in result.rag_chunks if c.chunk_type is RagChunkType.SYMBOL]
    api_chunks = [c for c in result.rag_chunks if c.chunk_type is RagChunkType.API_ENDPOINT]
    first_piece_symbols = [c for c in symbol_chunks if c.chunk_index == 0]
    first_piece_apis = [c for c in api_chunks if c.chunk_index == 0]

    all_passed &= _check(
        "one SYMBOL chunk (first piece) per extracted symbol",
        len(first_piece_symbols) == len(result.symbols),
        f"{len(first_piece_symbols)} vs {len(result.symbols)}",
    )
    all_passed &= _check(
        "one API_ENDPOINT chunk (first piece) per extracted route",
        len(first_piece_apis) == len(result.api_routes),
        f"{len(first_piece_apis)} vs {len(result.api_routes)}",
    )

    register_chunks = [c for c in symbol_chunks if c.symbol is not None and c.symbol.name == "register"]
    all_passed &= _check("register() symbol chunk exists", len(register_chunks) == 1, str(register_chunks))
    if register_chunks:
        chunk = register_chunks[0]
        all_passed &= _check("register chunk_text mentions 'register'", "register" in chunk.chunk_text)
        all_passed &= _check(
            "register chunk_text mentions its own file path", "app/api/v1/auth.py" in chunk.chunk_text
        )
        deps = chunk.metadata.get("dependencies", [])
        all_passed &= _check(
            "register chunk metadata carries 'fastapi' as a dependency", "fastapi" in deps, str(deps)
        )

    register_route_chunks = [c for c in api_chunks if "/api/v1/auth/register" in c.chunk_text]
    all_passed &= _check(
        "POST /api/v1/auth/register route chunk exists", len(register_route_chunks) == 1, str(register_route_chunks)
    )
    if register_route_chunks:
        chunk = register_route_chunks[0]
        all_passed &= _check("route chunk_text mentions POST", "POST" in chunk.chunk_text)
        all_passed &= _check("route chunk module is app/api/v1", chunk.module == "app/api/v1", chunk.module)

    root_chunks = [c for c in symbol_chunks if c.symbol is not None and c.symbol.name == "root"]
    all_passed &= _check("root() symbol chunk exists", len(root_chunks) == 1, str(root_chunks))

    root_route_chunks = [c for c in api_chunks if c.chunk_text.startswith("[API_ENDPOINT] GET /\n")]
    all_passed &= _check("GET / route chunk exists", len(root_route_chunks) == 1, str(root_route_chunks))

    return all_passed


def run_file_overview_scoping() -> bool:
    print("\n=== group 2: file-overview scoping (has-symbols-or-manifest, binary skipped, README excluded) ===")
    all_passed = True

    symbol_file = "app/service.py"
    manifest_file = "package.json"
    unrelated_file = "app/notes.txt"
    binary_manifest_file = "pyproject.toml"
    readme_file = "README.md"

    files_by_path = {
        symbol_file: b"def handler():\n    pass\n",
        manifest_file: b'{"name": "demo"}\n',
        unrelated_file: b"just some notes\n",
        binary_manifest_file: b"\xff\xfe\x00\x01binary-garbage-that-is-not-valid-utf8",
        readme_file: b"# Demo\n\nSome docs.\n",
    }
    symbol = make_symbol("handler", symbol_file, "app", line_start=1, line_end=2)
    symbols_by_file = {symbol_file: [symbol]}
    file_metrics = [
        FileMetrics(file_path=symbol_file, module="app", language=Language.PYTHON, total_lines=2, loc=2),
        FileMetrics(file_path=manifest_file, module=".", language=None, total_lines=1, loc=1),
        FileMetrics(file_path=unrelated_file, module=".", language=None, total_lines=1, loc=1),
        FileMetrics(file_path=binary_manifest_file, module=".", language=None, total_lines=0, loc=0),
        FileMetrics(file_path=readme_file, module=".", language=None, total_lines=3, loc=2),
    ]

    chunks = build_rag_chunks(
        files_by_path=files_by_path,
        symbols_by_file=symbols_by_file,
        api_routes=[],
        file_metrics=file_metrics,
        dependency_edges=[],
    )
    overview_by_file: dict[str, list] = {}
    for c in chunks:
        if c.chunk_type is RagChunkType.FILE_OVERVIEW:
            overview_by_file.setdefault(c.file_path, []).append(c)

    all_passed &= _check(
        "symbol-owning file gets a FILE_OVERVIEW chunk (structural summary, not a raw dump)",
        symbol_file in overview_by_file and "Symbols in this file" in overview_by_file[symbol_file][0].chunk_text,
        str(overview_by_file.get(symbol_file)),
    )
    all_passed &= _check(
        "manifest file gets a FILE_OVERVIEW chunk (raw content)",
        manifest_file in overview_by_file and '"name": "demo"' in overview_by_file[manifest_file][0].chunk_text,
        str(overview_by_file.get(manifest_file)),
    )
    all_passed &= _check("unrelated file gets no FILE_OVERVIEW chunk", unrelated_file not in overview_by_file)
    all_passed &= _check(
        "binary file matching a manifest name gets no FILE_OVERVIEW chunk (skipped regardless)",
        binary_manifest_file not in overview_by_file,
    )
    all_passed &= _check(
        "README with no symbols gets NO FILE_OVERVIEW chunk (documentation-only, dedup fix)",
        readme_file not in overview_by_file,
    )

    doc_chunks = [c for c in chunks if c.chunk_type is RagChunkType.DOCUMENTATION and c.file_path == readme_file]
    all_passed &= _check("README gets a DOCUMENTATION chunk instead", len(doc_chunks) >= 1, str(doc_chunks))

    return all_passed


def run_markdown_heading_split() -> bool:
    print("\n=== group 3: markdown heading split (fence-aware) + shared splitter from the doc path ===")
    all_passed = True

    doc_file = "docs/guide.md"
    oversized_body = "\n".join(f"line {i} of the giant reference section" for i in range(400))
    text = (
        "Intro paragraph before any heading.\n\n"
        "# Getting Started\n\n"
        "Some setup instructions.\n\n"
        "```python\n"
        "# this is a code comment, not a heading\n"
        "def f(): pass\n"
        "```\n\n"
        "## Configuration\n\n"
        "Config details.\n\n"
        "# Reference\n\n"
        f"{oversized_body}\n"
    )
    lines = text.splitlines()
    file_metrics = [
        FileMetrics(file_path=doc_file, module="docs", language=None, total_lines=len(lines), loc=len(lines))
    ]

    chunks = build_rag_chunks(
        files_by_path={doc_file: text.encode("utf-8")},
        symbols_by_file={},
        api_routes=[],
        file_metrics=file_metrics,
        dependency_edges=[],
    )
    doc_chunks = [c for c in chunks if c.chunk_type is RagChunkType.DOCUMENTATION]

    headings_seen = {h: any(f"# {h}" in c.chunk_text for c in doc_chunks) for h in ("Getting Started", "Configuration", "Reference")}
    all_passed &= _check("all three real headings produced their own section", all(headings_seen.values()), str(headings_seen))

    intro_chunks = [c for c in doc_chunks if "Intro paragraph before any heading" in c.chunk_text]
    all_passed &= _check("leading content before the first heading becomes its own section", len(intro_chunks) == 1, str(intro_chunks))

    getting_started_chunks = [c for c in doc_chunks if "# Getting Started" in c.chunk_text]
    all_passed &= _check(
        "the fenced '#'-comment line did not fool the heading scanner into a spurious extra section",
        len(getting_started_chunks) == 1 and "this is a code comment" in getting_started_chunks[0].chunk_text,
        str(getting_started_chunks),
    )

    max_count = max((c.chunk_count for c in doc_chunks), default=1)
    all_passed &= _check("the oversized Reference section was split into more than one piece", max_count > 1, str(max_count))
    split_pieces = sorted((c for c in doc_chunks if c.chunk_count == max_count), key=lambda c: c.chunk_index)
    all_passed &= _check(
        "split pieces have sequential 0-based chunk_index",
        [c.chunk_index for c in split_pieces] == list(range(max_count)),
        str([c.chunk_index for c in split_pieces]),
    )
    all_passed &= _check(
        "split pieces' line_start is non-decreasing",
        all(a.line_start <= b.line_start for a, b in zip(split_pieces, split_pieces[1:])),
    )

    return all_passed


def run_oversized_symbol_split() -> bool:
    print("\n=== group 4: oversized-symbol splitter — exact deterministic line-range coverage ===")
    all_passed = True

    file_path = "app/giant.py"
    body_line_count = 500
    body_lines = [f"    x = {i}  # padding line to force a real split boundary" for i in range(body_line_count)]
    source = ("def giant():\n" + "\n".join(body_lines) + "\n").encode("utf-8")

    symbol = make_symbol("giant", file_path, "app", line_start=1, line_end=body_line_count + 1)
    file_metrics = [
        FileMetrics(
            file_path=file_path, module="app", language=Language.PYTHON,
            total_lines=body_line_count + 1, loc=body_line_count + 1,
        )
    ]

    chunks = build_rag_chunks(
        files_by_path={file_path: source},
        symbols_by_file={file_path: [symbol]},
        api_routes=[],
        file_metrics=file_metrics,
        dependency_edges=[],
    )
    symbol_chunks = sorted((c for c in chunks if c.chunk_type is RagChunkType.SYMBOL), key=lambda c: c.chunk_index)

    all_passed &= _check("the giant symbol split into multiple pieces", len(symbol_chunks) > 1, str(len(symbol_chunks)))
    all_passed &= _check(
        "chunk_count matches the actual piece count on every piece",
        all(c.chunk_count == len(symbol_chunks) for c in symbol_chunks),
    )
    all_passed &= _check(
        "chunk_index is sequential 0-based", [c.chunk_index for c in symbol_chunks] == list(range(len(symbol_chunks)))
    )
    all_passed &= _check(
        "first piece starts exactly at the symbol's own line_start",
        symbol_chunks[0].line_start == symbol.line_start,
        str(symbol_chunks[0].line_start),
    )
    all_passed &= _check(
        "last piece ends exactly at the symbol's own line_end",
        symbol_chunks[-1].line_end == symbol.line_end,
        str(symbol_chunks[-1].line_end),
    )
    all_passed &= _check(
        "line_start is non-decreasing across pieces",
        all(a.line_start <= b.line_start for a, b in zip(symbol_chunks, symbol_chunks[1:])),
    )
    all_passed &= _check(
        "consecutive pieces genuinely overlap (next piece starts before the previous piece ends)",
        all(b.line_start <= a.line_end for a, b in zip(symbol_chunks, symbol_chunks[1:])),
    )

    return all_passed


def run_live_bonus() -> bool:
    print(f"\n=== group 5 (non-gating): live analyze_commit() rag_chunks against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
    try:
        installations = github_service.list_installations()
        installation = next(
            (i for i in installations if i["account"]["login"].lower() == LIVE_TEST_OWNER.lower()), None
        )
        if installation is None:
            print(f"  [SKIP] no installation found for account {LIVE_TEST_OWNER!r}")
            return True

        token_data = github_service.get_installation_access_token(str(installation["id"]))
        token = token_data["token"]

        branch = github_service.get_branch(token, LIVE_TEST_OWNER, LIVE_TEST_REPO, "main")
        commit_sha = branch["commit"]["sha"]

        result = pipeline.analyze_commit(
            installation_token=token, owner=LIVE_TEST_OWNER, repo=LIVE_TEST_REPO, commit_sha=commit_sha
        )
        by_type: dict[str, int] = {}
        for c in result.rag_chunks:
            by_type[c.chunk_type.value] = by_type.get(c.chunk_type.value, 0) + 1
        print(f"  rag_chunks by type: {by_type}")

        first_piece_symbols = sum(1 for c in result.rag_chunks if c.chunk_type.value == "symbol" and c.chunk_index == 0)
        first_piece_apis = sum(1 for c in result.rag_chunks if c.chunk_type.value == "api_endpoint" and c.chunk_index == 0)
        ok = first_piece_symbols == len(result.symbols) and first_piece_apis == len(result.api_routes)
        return _check(
            "first-piece SYMBOL/API_ENDPOINT counts match the real analysis result",
            ok,
            f"symbols {first_piece_symbols} vs {len(result.symbols)}, apis {first_piece_apis} vs {len(result.api_routes)}",
        )
    except Exception as exc:  # noqa: BLE001 — smoke script, report and continue
        print(f"  [ERROR] live bonus test failed: {exc!r}")
        return False


if __name__ == "__main__":
    r1 = run_symbol_and_api_endpoint_chunks()
    r2 = run_file_overview_scoping()
    r3 = run_markdown_heading_split()
    r4 = run_oversized_symbol_split()
    r5 = run_live_bonus()
    print("\n=== Summary ===")
    print(f"1. Symbol/API-endpoint coverage: {'PASS' if r1 else 'FAIL'}")
    print(f"2. File-overview scoping:        {'PASS' if r2 else 'FAIL'}")
    print(f"3. Markdown heading split:       {'PASS' if r3 else 'FAIL'}")
    print(f"4. Oversized-symbol split:       {'PASS' if r4 else 'FAIL'}")
    print(f"5. Live bonus (non-gating):      {'PASS' if r5 else 'FAIL (see above — may be a network/credentials issue)'}")
    sys.exit(0 if (r1 and r2 and r3 and r4) else 1)
