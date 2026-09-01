"""Manual smoke test for Phase 3.3 (JS/TS/TSX symbol extraction).

Two parts:

1. `run_fixture_tests()` — hand-written `.ts`/`.tsx`/`.jsx` fixtures under
   `scripts/fixtures/js_ts/`, covering every symbol kind the plan calls for:
   plain functions, exported const/arrow functions, classes, instance/static
   methods, a class-field-as-method, JSDoc detection, components via both
   heuristics (JSX-returning function and `extends React.Component`), and
   the negative case (interface/type-alias declarations, an anonymous
   default export, a non-function const, and an unbound inline callback all
   produce zero symbols).
2. `run_real_spot_check()` — runs the same extractor against a handful of
   this repo's own real `client/src` files, asserting a few symbols we can
   read directly in that source are found with the expected kind.

Run: `uv run python scripts/smoke_symbols_js_ts.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.language_detect import detect_language  # noqa: E402
from app.analysis.symbols import Symbol, SymbolKind  # noqa: E402
from app.analysis.symbols.js_ts import extract_symbols  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "js_ts"
CLIENT_SRC = Path(__file__).resolve().parent.parent.parent / "client" / "src"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _extract(rel_path: str, source: bytes) -> list[Symbol]:
    language = detect_language(rel_path)
    assert language is not None, f"{rel_path} has an unrecognized extension"
    return extract_symbols(source, file_path=rel_path, language=language)


def _extract_fixture(name: str) -> list[Symbol]:
    return _extract(name, (FIXTURES_DIR / name).read_bytes())


def _by_name(symbols: list[Symbol], name: str) -> Symbol | None:
    return next((s for s in symbols if s.name == name), None)


def run_fixture_tests() -> bool:
    print("=== Fixture-based symbol extraction checks ===")
    all_passed = True

    symbols = _extract_fixture("basic.ts")
    all_passed &= _check("basic.ts: 3 symbols", len(symbols) == 3, str(symbols))
    add = _by_name(symbols, "add")
    all_passed &= _check(
        "add: FUNCTION + docstring",
        add is not None and add.kind == SymbolKind.FUNCTION and add.has_docstring,
        str(add),
    )
    multiply = _by_name(symbols, "multiply")
    all_passed &= _check(
        "multiply: FUNCTION, no docstring",
        multiply is not None and multiply.kind == SymbolKind.FUNCTION and not multiply.has_docstring,
        str(multiply),
    )
    divide = _by_name(symbols, "divide")
    all_passed &= _check(
        "divide: FUNCTION (exported const arrow)",
        divide is not None and divide.kind == SymbolKind.FUNCTION,
        str(divide),
    )
    all_passed &= _check(
        "basic.ts: interface/type produce no symbols",
        _by_name(symbols, "Point") is None and _by_name(symbols, "Pair") is None,
    )

    symbols = _extract_fixture("classes.ts")
    all_passed &= _check("classes.ts: 6 symbols", len(symbols) == 6, str(symbols))
    counter = _by_name(symbols, "Counter")
    all_passed &= _check(
        "Counter: CLASS + docstring",
        counter is not None and counter.kind == SymbolKind.CLASS and counter.has_docstring,
        str(counter),
    )
    increment = _by_name(symbols, "increment")
    all_passed &= _check(
        "increment: METHOD + docstring",
        increment is not None and increment.kind == SymbolKind.METHOD and increment.has_docstring,
        str(increment),
    )
    zero = _by_name(symbols, "zero")
    all_passed &= _check(
        "zero (static method): METHOD", zero is not None and zero.kind == SymbolKind.METHOD, str(zero)
    )
    reset = _by_name(symbols, "reset")
    all_passed &= _check(
        "reset (field arrow method): METHOD",
        reset is not None and reset.kind == SymbolKind.METHOD,
        str(reset),
    )
    shape = _by_name(symbols, "Shape")
    all_passed &= _check(
        "Shape (abstract class): CLASS", shape is not None and shape.kind == SymbolKind.CLASS, str(shape)
    )

    symbols = _extract_fixture("components.tsx")
    all_passed &= _check("components.tsx: 8 symbols", len(symbols) == 8, str(symbols))
    greeting = _by_name(symbols, "Greeting")
    all_passed &= _check(
        "Greeting: COMPONENT + docstring (JSX-returning function)",
        greeting is not None and greeting.kind == SymbolKind.COMPONENT and greeting.has_docstring,
        str(greeting),
    )
    arrow = _by_name(symbols, "Arrow")
    all_passed &= _check(
        "Arrow: COMPONENT (JSX-returning block arrow)",
        arrow is not None and arrow.kind == SymbolKind.COMPONENT,
        str(arrow),
    )
    implicit = _by_name(symbols, "Implicit")
    all_passed &= _check(
        "Implicit: COMPONENT (JSX-returning implicit-return arrow)",
        implicit is not None and implicit.kind == SymbolKind.COMPONENT,
        str(implicit),
    )
    widget = _by_name(symbols, "Widget")
    all_passed &= _check(
        "Widget: COMPONENT (extends React.Component)",
        widget is not None and widget.kind == SymbolKind.COMPONENT,
        str(widget),
    )
    plain_helper = _by_name(symbols, "PlainHelper")
    all_passed &= _check(
        "PlainHelper: CLASS (not a component)",
        plain_helper is not None and plain_helper.kind == SymbolKind.CLASS,
        str(plain_helper),
    )
    helper = _by_name(symbols, "helper")
    all_passed &= _check(
        "helper: METHOD (not a component)", helper is not None and helper.kind == SymbolKind.METHOD, str(helper)
    )
    use_count = _by_name(symbols, "useCount")
    all_passed &= _check(
        "useCount: FUNCTION (not a component)",
        use_count is not None and use_count.kind == SymbolKind.FUNCTION,
        str(use_count),
    )

    symbols = _extract_fixture("negative.jsx")
    all_passed &= _check(
        "negative.jsx: 0 symbols (anonymous export, non-function const, unbound callback)",
        len(symbols) == 0,
        str(symbols),
    )

    return all_passed


def run_real_spot_check() -> bool:
    print("\n=== Real spot-check: this repo's own client/src ===")
    targets: list[tuple[str, dict[str, SymbolKind]]] = [
        ("hooks/use-mobile.tsx", {"useIsMobile": SymbolKind.FUNCTION}),
        ("lib/utils.ts", {"cn": SymbolKind.FUNCTION}),
        (
            "components/app-shell.tsx",
            {
                "Logo": SymbolKind.COMPONENT,
                "TopBar": SymbolKind.COMPONENT,
                "SideNav": SymbolKind.COMPONENT,
                "AppShell": SymbolKind.COMPONENT,
            },
        ),
    ]
    all_passed = True
    for rel_path, expected in targets:
        source = (CLIENT_SRC / rel_path).read_bytes()
        symbols = _extract(rel_path, source)
        print(f"  {rel_path}: {len(symbols)} symbols -> {[(s.name, s.kind.value) for s in symbols]}")
        for name, expected_kind in expected.items():
            found = _by_name(symbols, name)
            all_passed &= _check(
                f"{rel_path}: {name} -> {expected_kind.value}",
                found is not None and found.kind == expected_kind,
                f"got {found}",
            )
    return all_passed


if __name__ == "__main__":
    r1 = run_fixture_tests()
    r2 = run_real_spot_check()
    print("\n=== Summary ===")
    print(f"fixture tests:   {'PASS' if r1 else 'FAIL'}")
    print(f"real spot-check: {'PASS' if r2 else 'FAIL'}")
    sys.exit(0 if (r1 and r2) else 1)
