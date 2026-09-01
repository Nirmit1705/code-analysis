"""Manual smoke test for Phase 3.5 (Java symbol extraction).

Fixture-based only: this project's own repo has no Java source (confirmed
during Phase 3 planning), so — as the plan itself anticipates — verification
here relies entirely on hand-written `.java` fixtures under
`scripts/fixtures/java/`, covering:

- a plain class with a constructor, an instance method, and a static
  method, plus Javadoc detection
- interfaces and abstract classes: a signature-only method (interface
  method with no `default`, an `abstract` method) produces no symbol,
  while a method with a real body does
- enums and records: both are real Java types with real methods
  (`SymbolKind.CLASS` + `SymbolKind.METHOD`), and an enum constant
  (`ACTIVE`, `INACTIVE`) is not itself a symbol
- arbitrary nesting depth (a class nested inside another class)

Run: `uv run python scripts/smoke_symbols_java.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.symbols import Symbol, SymbolKind  # noqa: E402
from app.analysis.symbols.java import extract_symbols  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "java"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _extract_fixture(name: str) -> list[Symbol]:
    return extract_symbols((FIXTURES_DIR / name).read_bytes(), file_path=name)


def _by_name(symbols: list[Symbol], name: str) -> Symbol | None:
    return next((s for s in symbols if s.name == name), None)


def run_fixture_tests() -> bool:
    print("=== Fixture-based symbol extraction checks ===")
    all_passed = True

    symbols = _extract_fixture("Basic.java")
    all_passed &= _check("Basic.java: 4 symbols", len(symbols) == 4, str(symbols))
    greeter = _by_name(symbols, "Greeter")
    all_passed &= _check(
        "Greeter (class): CLASS + docstring, appears twice (class + constructor share the name)",
        greeter is not None and greeter.kind == SymbolKind.CLASS and greeter.has_docstring,
        str(greeter),
    )
    ctors = [s for s in symbols if s.name == "Greeter"]
    all_passed &= _check(
        "Greeter: exactly one CLASS + one METHOD (the constructor), both with docstrings",
        len(ctors) == 2
        and {s.kind for s in ctors} == {SymbolKind.CLASS, SymbolKind.METHOD}
        and all(s.has_docstring for s in ctors),
        str(ctors),
    )
    greet = _by_name(symbols, "greet")
    all_passed &= _check(
        "greet: METHOD + docstring",
        greet is not None and greet.kind == SymbolKind.METHOD and greet.has_docstring,
        str(greet),
    )
    create = _by_name(symbols, "create")
    all_passed &= _check(
        "create (static method): METHOD, no docstring",
        create is not None and create.kind == SymbolKind.METHOD and not create.has_docstring,
        str(create),
    )

    symbols = _extract_fixture("InterfacesAndAbstract.java")
    all_passed &= _check(
        "InterfacesAndAbstract.java: 4 symbols (signature-only methods excluded)",
        len(symbols) == 4,
        str(symbols),
    )
    all_passed &= _check(
        "Callback (interface): CLASS", _by_name(symbols, "Callback") is not None and _by_name(symbols, "Callback").kind == SymbolKind.CLASS
    )
    all_passed &= _check(
        "onStart (default method): METHOD",
        _by_name(symbols, "onStart") is not None and _by_name(symbols, "onStart").kind == SymbolKind.METHOD,
    )
    all_passed &= _check("onDone (signature only, no body): not extracted", _by_name(symbols, "onDone") is None)
    all_passed &= _check(
        "Base (abstract class): CLASS", _by_name(symbols, "Base") is not None and _by_name(symbols, "Base").kind == SymbolKind.CLASS
    )
    all_passed &= _check(
        "concrete: METHOD", _by_name(symbols, "concrete") is not None and _by_name(symbols, "concrete").kind == SymbolKind.METHOD
    )
    all_passed &= _check("doThing (abstract, no body): not extracted", _by_name(symbols, "doThing") is None)

    symbols = _extract_fixture("EnumAndRecord.java")
    all_passed &= _check(
        "EnumAndRecord.java: 4 symbols (enum constants are not symbols)", len(symbols) == 4, str(symbols)
    )
    all_passed &= _check(
        "Status (enum): CLASS", _by_name(symbols, "Status") is not None and _by_name(symbols, "Status").kind == SymbolKind.CLASS
    )
    all_passed &= _check(
        "log (enum method): METHOD", _by_name(symbols, "log") is not None and _by_name(symbols, "log").kind == SymbolKind.METHOD
    )
    all_passed &= _check(
        "Point (record): CLASS", _by_name(symbols, "Point") is not None and _by_name(symbols, "Point").kind == SymbolKind.CLASS
    )
    all_passed &= _check(
        "sum (record method): METHOD", _by_name(symbols, "sum") is not None and _by_name(symbols, "sum").kind == SymbolKind.METHOD
    )
    all_passed &= _check(
        "ACTIVE/INACTIVE (enum constants): not extracted",
        _by_name(symbols, "ACTIVE") is None and _by_name(symbols, "INACTIVE") is None,
    )

    symbols = _extract_fixture("Nesting.java")
    all_passed &= _check("Nesting.java: 4 symbols", len(symbols) == 4, str(symbols))
    all_passed &= _check(
        "Outer and nested Inner both CLASS",
        _by_name(symbols, "Outer") is not None
        and _by_name(symbols, "Inner") is not None
        and _by_name(symbols, "Outer").kind == SymbolKind.CLASS
        and _by_name(symbols, "Inner").kind == SymbolKind.CLASS,
    )
    all_passed &= _check(
        "method (inside nested Inner) and outerMethod both METHOD",
        _by_name(symbols, "method") is not None
        and _by_name(symbols, "outerMethod") is not None
        and _by_name(symbols, "method").kind == SymbolKind.METHOD
        and _by_name(symbols, "outerMethod").kind == SymbolKind.METHOD,
    )

    return all_passed


if __name__ == "__main__":
    r1 = run_fixture_tests()
    print("\n=== Summary ===")
    print(f"fixture tests: {'PASS' if r1 else 'FAIL'}")
    sys.exit(0 if r1 else 1)
