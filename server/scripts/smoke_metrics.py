"""Manual smoke test for Phase 3.7's `app.analysis.metrics`.

For each language, extracts `scripts/fixtures/metrics/<lang>/*`'s symbols
(via the matching Phase 3.3-3.5 extractor) and computes their
`FunctionComplexity`, checking the hand-verified complexity noted in each
fixture function's own comment. Also checks `compute_file_metrics`'s LOC
count and `aggregate_module_metrics`'s rollup on a small in-memory case.

Run: `uv run python scripts/smoke_metrics.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import metrics  # noqa: E402
from app.analysis.binary_detect import is_binary  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.metrics import FileMetrics, FunctionComplexity  # noqa: E402
from app.analysis.symbols.java import extract_symbols as extract_java_symbols  # noqa: E402
from app.analysis.symbols.js_ts import extract_symbols as extract_js_ts_symbols  # noqa: E402
from app.analysis.symbols.python import extract_symbols as extract_python_symbols  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "metrics"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _by_name(results: list[FunctionComplexity], name: str) -> FunctionComplexity | None:
    return next((r for r in results if r.symbol.name == name), None)


def run_js_ts() -> bool:
    print("=== JS/TS complexity checks ===")
    all_passed = True
    file_path = "complexity.ts"
    source = (FIXTURES_DIR / "js_ts" / file_path).read_bytes()
    symbols = extract_js_ts_symbols(source, file_path=file_path, language=Language.TYPESCRIPT)
    results = metrics.compute_function_complexity(source, file_path=file_path, language=Language.TYPESCRIPT, symbols=symbols)

    expectations = [
        ("noBranches", 1),
        ("withIf", 2),
        ("withIfElseIf", 3),
        ("withLoops", 6),
        ("withTryCatch", 2),
        ("withSwitch", 3),
        ("withTernary", 2),
        ("withLogicalAnd", 2),
        ("withLogicalOr", 2),
        ("withNullishCoalescing", 2),
        ("withNestedFunction", 2),
        ("helper", 2),
        ("render", 2),
        ("riskyThing", 1),
    ]
    for name, expected in expectations:
        found = _by_name(results, name)
        all_passed &= _check(
            f"{name} complexity == {expected}", found is not None and found.complexity == expected, str(found)
        )
    all_passed &= _check(f"exactly {len(expectations)} functions measured", len(results) == len(expectations), str(len(results)))
    return all_passed


def run_python() -> bool:
    print("\n=== Python complexity checks ===")
    all_passed = True
    file_path = "complexity.py"
    source = (FIXTURES_DIR / "python" / file_path).read_bytes()
    symbols = extract_python_symbols(source, file_path=file_path)
    results = metrics.compute_function_complexity(source, file_path=file_path, language=Language.PYTHON, symbols=symbols)

    expectations = [
        ("no_branches", 1),
        ("with_if", 2),
        ("with_if_elif_else", 3),
        ("with_loops", 4),
        ("with_try_except", 3),
        ("with_match", 4),
        ("with_ternary", 2),
        ("with_boolean_and", 2),
        ("with_boolean_or", 2),
        ("with_comprehension_filter", 2),
        ("with_nested_function", 2),
        ("helper", 2),
        ("render", 2),
        ("risky_thing", 1),
    ]
    for name, expected in expectations:
        found = _by_name(results, name)
        all_passed &= _check(
            f"{name} complexity == {expected}", found is not None and found.complexity == expected, str(found)
        )
    all_passed &= _check(f"exactly {len(expectations)} functions measured", len(results) == len(expectations), str(len(results)))
    return all_passed


def run_java() -> bool:
    print("\n=== Java complexity checks ===")
    all_passed = True
    file_path = "Complexity.java"
    source = (FIXTURES_DIR / "java" / file_path).read_bytes()
    symbols = extract_java_symbols(source, file_path=file_path)
    results = metrics.compute_function_complexity(source, file_path=file_path, language=Language.JAVA, symbols=symbols)

    expectations = [
        ("noBranches", 1),
        ("withIf", 2),
        ("withIfElseIf", 3),
        ("withLoops", 6),
        ("withTryCatch", 2),
        ("withSwitch", 3),
        ("withTernary", 2),
        ("withLogicalAnd", 2),
        ("withLogicalOr", 2),
        ("riskyThing", 1),
    ]
    for name, expected in expectations:
        found = _by_name(results, name)
        all_passed &= _check(
            f"{name} complexity == {expected}", found is not None and found.complexity == expected, str(found)
        )
    all_passed &= _check(f"exactly {len(expectations)} methods measured", len(results) == len(expectations), str(len(results)))
    return all_passed


def run_file_and_module_metrics() -> bool:
    print("\n=== File / module metrics checks ===")
    all_passed = True

    source_a = b"line one\n\nline three\n"  # 3 total lines, 2 non-blank
    fm_a = metrics.compute_file_metrics(source_a, file_path="pkg/a.py", language=Language.PYTHON)
    all_passed &= _check("file a: total_lines == 3", fm_a.total_lines == 3, str(fm_a))
    all_passed &= _check("file a: loc == 2", fm_a.loc == 2, str(fm_a))
    all_passed &= _check("file a: module == 'pkg'", fm_a.module == "pkg", str(fm_a))

    source_b = b"x = 1\ny = 2\n"
    fm_b = metrics.compute_file_metrics(source_b, file_path="pkg/b.py", language=Language.PYTHON)

    # Known Issue #18 regression: a binary file must not be counted as if
    # it were text — `loc`/`total_lines` are 0, not a garbled decode count.
    binary_source = b"\xff\xfe\x00\x01\x02\x03garbage\x00\x04\x05"
    fm_binary = metrics.compute_file_metrics(binary_source, file_path="assets/logo.png", language=None)
    all_passed &= _check("binary file: loc == 0", fm_binary.loc == 0, str(fm_binary))
    all_passed &= _check("binary file: total_lines == 0", fm_binary.total_lines == 0, str(fm_binary))

    all_passed &= _check("is_binary: True for invalid UTF-8", is_binary(binary_source))
    all_passed &= _check("is_binary: False for real text", not is_binary(source_a))
    all_passed &= _check("is_binary: False for empty bytes", not is_binary(b""))

    py_source = (FIXTURES_DIR / "python" / "complexity.py").read_bytes()
    py_symbols = extract_python_symbols(py_source, file_path="pkg/a.py")
    complexities = metrics.compute_function_complexity(
        py_source, file_path="pkg/a.py", language=Language.PYTHON, symbols=py_symbols
    )

    modules = metrics.aggregate_module_metrics([fm_a, fm_b], complexities)
    pkg_module = next((m for m in modules if m.module == "pkg"), None)
    all_passed &= _check("module 'pkg': file_count == 2", pkg_module is not None and pkg_module.file_count == 2, str(pkg_module))
    all_passed &= _check(
        "module 'pkg': function_count == len(complexities)",
        pkg_module is not None and pkg_module.function_count == len(complexities),
        str(pkg_module),
    )
    all_passed &= _check(
        "module 'pkg': max_complexity == max(complexities)",
        pkg_module is not None and pkg_module.max_complexity == max(c.complexity for c in complexities),
        str(pkg_module),
    )

    empty_metrics = metrics.aggregate_module_metrics([FileMetrics(file_path="empty/x.md", module="empty", language=None, total_lines=1, loc=1)], [])
    empty_module = next((m for m in empty_metrics if m.module == "empty"), None)
    all_passed &= _check(
        "module with no measured functions: avg/max complexity are None",
        empty_module is not None and empty_module.avg_complexity is None and empty_module.max_complexity is None,
        str(empty_module),
    )

    return all_passed


if __name__ == "__main__":
    r1 = run_js_ts()
    r2 = run_python()
    r3 = run_java()
    r4 = run_file_and_module_metrics()
    print("\n=== Summary ===")
    print(f"JS/TS:               {'PASS' if r1 else 'FAIL'}")
    print(f"Python:               {'PASS' if r2 else 'FAIL'}")
    print(f"Java:                 {'PASS' if r3 else 'FAIL'}")
    print(f"file/module metrics:  {'PASS' if r4 else 'FAIL'}")
    sys.exit(0 if (r1 and r2 and r3 and r4) else 1)
