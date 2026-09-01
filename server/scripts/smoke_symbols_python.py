"""Manual smoke test for Phase 3.4 (Python symbol extraction).

Two parts:

1. `run_fixture_tests()` — hand-written `.py` fixtures under
   `scripts/fixtures/python/`, covering plain functions, `async def`,
   docstring detection, classes, instance/static/property methods,
   decorator-span inclusion, arbitrary nesting depth (function-in-function,
   class-in-class), and the negative case (a lambda bound to a name, a
   plain constant, and a class attribute all produce no symbol of their
   own).
2. `run_real_spot_check()` — runs the same extractor against this repo's
   own real `server/app` source (`services/github_service.py` and
   `analysis/ingestion.py`), asserting a few symbols we can read directly
   in that source are found with the expected kind and docstring status.

Run: `uv run python scripts/smoke_symbols_python.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.symbols import Symbol, SymbolKind  # noqa: E402
from app.analysis.symbols.python import extract_symbols  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "python"
SERVER_APP = Path(__file__).resolve().parent.parent / "app"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _extract(rel_path: str, source: bytes) -> list[Symbol]:
    return extract_symbols(source, file_path=rel_path)


def _extract_fixture(name: str) -> list[Symbol]:
    return _extract(name, (FIXTURES_DIR / name).read_bytes())


def _by_name(symbols: list[Symbol], name: str) -> Symbol | None:
    return next((s for s in symbols if s.name == name), None)


def run_fixture_tests() -> bool:
    print("=== Fixture-based symbol extraction checks ===")
    all_passed = True

    symbols = _extract_fixture("basic.py")
    all_passed &= _check("basic.py: 3 symbols", len(symbols) == 3, str(symbols))
    add = _by_name(symbols, "add")
    all_passed &= _check(
        "add: FUNCTION + docstring",
        add is not None and add.kind == SymbolKind.FUNCTION and add.has_docstring,
        str(add),
    )
    subtract = _by_name(symbols, "subtract")
    all_passed &= _check(
        "subtract: FUNCTION, no docstring",
        subtract is not None and subtract.kind == SymbolKind.FUNCTION and not subtract.has_docstring,
        str(subtract),
    )
    fetch_data = _by_name(symbols, "fetch_data")
    all_passed &= _check(
        "fetch_data: async def still FUNCTION + docstring",
        fetch_data is not None and fetch_data.kind == SymbolKind.FUNCTION and fetch_data.has_docstring,
        str(fetch_data),
    )

    symbols = _extract_fixture("classes.py")
    all_passed &= _check("classes.py: 6 symbols", len(symbols) == 6, str(symbols))
    greeter = _by_name(symbols, "Greeter")
    all_passed &= _check(
        "Greeter: CLASS + docstring",
        greeter is not None and greeter.kind == SymbolKind.CLASS and greeter.has_docstring,
        str(greeter),
    )
    init = _by_name(symbols, "__init__")
    all_passed &= _check(
        "__init__: METHOD, no docstring",
        init is not None and init.kind == SymbolKind.METHOD and not init.has_docstring,
        str(init),
    )
    greet = _by_name(symbols, "greet")
    all_passed &= _check(
        "greet: METHOD + docstring",
        greet is not None and greet.kind == SymbolKind.METHOD and greet.has_docstring,
        str(greet),
    )
    create = _by_name(symbols, "create")
    all_passed &= _check(
        "create (@staticmethod): METHOD, span includes decorator line",
        create is not None and create.kind == SymbolKind.METHOD and create.line_start == 11,
        str(create),
    )
    label = _by_name(symbols, "label")
    all_passed &= _check(
        "label (@property): METHOD", label is not None and label.kind == SymbolKind.METHOD, str(label)
    )
    empty = _by_name(symbols, "Empty")
    all_passed &= _check(
        "Empty: CLASS, no docstring",
        empty is not None and empty.kind == SymbolKind.CLASS and not empty.has_docstring,
        str(empty),
    )

    symbols = _extract_fixture("nesting.py")
    all_passed &= _check("nesting.py: 5 symbols", len(symbols) == 5, str(symbols))
    outer = _by_name(symbols, "outer")
    inner = _by_name(symbols, "inner")
    all_passed &= _check(
        "outer/inner: both FUNCTION (nested function is not a METHOD)",
        outer is not None
        and inner is not None
        and outer.kind == SymbolKind.FUNCTION
        and inner.kind == SymbolKind.FUNCTION,
        f"outer={outer} inner={inner}",
    )
    method = _by_name(symbols, "method")
    all_passed &= _check(
        "method (inside nested class Outer.Inner): METHOD",
        method is not None and method.kind == SymbolKind.METHOD,
        str(method),
    )
    all_passed &= _check(
        "Outer and Inner both extracted as CLASS",
        _by_name(symbols, "Outer") is not None
        and _by_name(symbols, "Inner") is not None
        and _by_name(symbols, "Outer").kind == SymbolKind.CLASS
        and _by_name(symbols, "Inner").kind == SymbolKind.CLASS,
    )

    symbols = _extract_fixture("negative.py")
    all_passed &= _check(
        "negative.py: only Config (lambda/constant/class-attribute produce nothing)",
        len(symbols) == 1 and symbols[0].name == "Config" and symbols[0].kind == SymbolKind.CLASS,
        str(symbols),
    )

    return all_passed


def run_real_spot_check() -> bool:
    print("\n=== Real spot-check: this repo's own server/app ===")
    all_passed = True

    rel_path = "services/github_service.py"
    symbols = _extract(rel_path, (SERVER_APP / rel_path).read_bytes())
    print(f"  {rel_path}: {len(symbols)} symbols")
    expectations = {
        "generate_app_jwt": (SymbolKind.FUNCTION, True),
        "_private_key_bytes": (SymbolKind.FUNCTION, False),  # decorated with @lru_cache, no docstring
        "_get_all_pages": (SymbolKind.FUNCTION, True),
        "download_repository_archive": (SymbolKind.FUNCTION, True),
        "GitHubServiceError": (SymbolKind.CLASS, True),
        "GitHubArchiveTooLargeError": (SymbolKind.CLASS, True),
    }
    for name, (expected_kind, expected_doc) in expectations.items():
        found = _by_name(symbols, name)
        all_passed &= _check(
            f"{rel_path}: {name} -> {expected_kind.value}, has_docstring={expected_doc}",
            found is not None and found.kind == expected_kind and found.has_docstring == expected_doc,
            f"got {found}",
        )
    # GitHubServiceError.__init__ has no docstring — confirms methods inside
    # a real exception class are still correctly classified.
    init_symbols = [s for s in symbols if s.name == "__init__"]
    all_passed &= _check(
        "two __init__ methods found (GitHubServiceError, GitHubArchiveTooLargeError), both METHOD",
        len(init_symbols) == 2 and all(s.kind == SymbolKind.METHOD for s in init_symbols),
        str(init_symbols),
    )

    rel_path = "analysis/ingestion.py"
    symbols = _extract(rel_path, (SERVER_APP / rel_path).read_bytes())
    print(f"  {rel_path}: {len(symbols)} symbols")
    ingest = _by_name(symbols, "ingest_repository")
    all_passed &= _check(
        "ingest_repository (@contextmanager): FUNCTION + docstring, span includes decorator line",
        ingest is not None
        and ingest.kind == SymbolKind.FUNCTION
        and ingest.has_docstring
        and ingest.line_start is not None,
        str(ingest),
    )

    return all_passed


if __name__ == "__main__":
    r1 = run_fixture_tests()
    r2 = run_real_spot_check()
    print("\n=== Summary ===")
    print(f"fixture tests:   {'PASS' if r1 else 'FAIL'}")
    print(f"real spot-check: {'PASS' if r2 else 'FAIL'}")
    sys.exit(0 if (r1 and r2) else 1)
