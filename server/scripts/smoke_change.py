"""Manual smoke test for Phase 9.1's `app.analysis.change`.

Fully offline, no DB/Celery involved (per `docs/PHASE_9_PLAN.md`'s own 9.1
Definition of Done) — hand-builds two `RunFacts` bundles directly rather than
loading fixture files from disk, since this module's input is already the
plain dataclass shape a real `analysis_read_service.load_run_facts` call
would produce, not raw source text to parse.

Fixture shape, `base` -> `head`:
- `removed.py` exists only in `base` (with one API endpoint on it) -> reported
  added/removed at the file and API level.
- `added.py` exists only in `head` (with one internal dependency edge onto
  `unchanged.py`) -> reported added at the file and edge level.
- `modified.py` exists in both with identical `loc` (20) but a different
  symbol (`old_func` -> `new_func`) -> exercises the "symbol set differs"
  half of the modified-file rule independently of the "loc differs" half,
  and its own internal edge onto `unchanged.py` is dropped in `head` ->
  reported as a removed edge.
- `unchanged.py` is byte-for-byte identical in both runs (same file fact,
  same symbol, same API, same external dependency edge) -> a regression
  guard that it never shows up in any added/removed/modified list.

Run: `uv run python scripts/smoke_change.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.change import (  # noqa: E402
    ApiFact,
    FileFact,
    RunFacts,
    SymbolFact,
    classify_change,
    diff_analysis_runs,
)
from app.analysis.dependencies import DependencyEdge, DependencyEdgeType  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.symbols import SymbolKind  # noqa: E402


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _build_fixture() -> tuple[RunFacts, RunFacts]:
    unchanged_file = FileFact(path="unchanged.py", language=Language.PYTHON, loc=5)
    unchanged_symbol = SymbolFact(file_path="unchanged.py", name="stable_func", kind=SymbolKind.FUNCTION)
    unchanged_api = ApiFact(method="GET", path="/stable", file_path="unchanged.py")
    unchanged_edge = DependencyEdge(
        source_file="unchanged.py",
        module=".",
        language=Language.PYTHON,
        edge_type=DependencyEdgeType.EXTERNAL,
        target_file=None,
        target_package="requests",
        raw_specifier="",
        line=0,
    )

    base = RunFacts(
        files=[
            FileFact(path="removed.py", language=Language.PYTHON, loc=10),
            FileFact(path="modified.py", language=Language.PYTHON, loc=20),
            unchanged_file,
        ],
        symbols=[
            SymbolFact(file_path="modified.py", name="old_func", kind=SymbolKind.FUNCTION),
            unchanged_symbol,
        ],
        apis=[
            ApiFact(method="GET", path="/old", file_path="removed.py"),
            unchanged_api,
        ],
        edges=[
            DependencyEdge(
                source_file="modified.py",
                module=".",
                language=Language.PYTHON,
                edge_type=DependencyEdgeType.INTERNAL,
                target_file="unchanged.py",
                target_package=None,
                raw_specifier="",
                line=0,
            ),
            unchanged_edge,
        ],
    )

    head = RunFacts(
        files=[
            FileFact(path="added.py", language=Language.PYTHON, loc=15),
            FileFact(path="modified.py", language=Language.PYTHON, loc=20),  # same loc as base
            unchanged_file,
        ],
        symbols=[
            SymbolFact(file_path="modified.py", name="new_func", kind=SymbolKind.FUNCTION),
            unchanged_symbol,
        ],
        apis=[
            ApiFact(method="POST", path="/new", file_path="added.py"),
            unchanged_api,
        ],
        edges=[
            DependencyEdge(
                source_file="added.py",
                module=".",
                language=Language.PYTHON,
                edge_type=DependencyEdgeType.INTERNAL,
                target_file="unchanged.py",
                target_package=None,
                raw_specifier="",
                line=0,
            ),
            unchanged_edge,
        ],
    )
    return base, head


def run_full_diff() -> bool:
    print("=== base -> head: every diff category exercised at once ===")
    all_passed = True
    base, head = _build_fixture()
    result = diff_analysis_runs(base, head)

    all_passed &= _check(
        "added_files == [added.py]",
        [f.path for f in result.added_files] == ["added.py"],
        str(result.added_files),
    )
    all_passed &= _check(
        "removed_files == [removed.py]",
        [f.path for f in result.removed_files] == ["removed.py"],
        str(result.removed_files),
    )
    all_passed &= _check(
        "modified_files == [modified.py] (symbol set differs, loc identical)",
        result.modified_files == ["modified.py"],
        str(result.modified_files),
    )
    all_passed &= _check(
        "added_symbols == [modified.py::new_func]",
        [(s.file_path, s.name) for s in result.added_symbols] == [("modified.py", "new_func")],
        str(result.added_symbols),
    )
    all_passed &= _check(
        "removed_symbols == [modified.py::old_func]",
        [(s.file_path, s.name) for s in result.removed_symbols] == [("modified.py", "old_func")],
        str(result.removed_symbols),
    )
    all_passed &= _check(
        "added_apis == [POST /new]",
        [(a.method, a.path) for a in result.added_apis] == [("POST", "/new")],
        str(result.added_apis),
    )
    all_passed &= _check(
        "removed_apis == [GET /old]",
        [(a.method, a.path) for a in result.removed_apis] == [("GET", "/old")],
        str(result.removed_apis),
    )
    all_passed &= _check(
        "added_edges == [added.py -> unchanged.py]",
        [(e.source_file, e.target_file) for e in result.added_edges] == [("added.py", "unchanged.py")],
        str(result.added_edges),
    )
    all_passed &= _check(
        "removed_edges == [modified.py -> unchanged.py]",
        [(e.source_file, e.target_file) for e in result.removed_edges] == [("modified.py", "unchanged.py")],
        str(result.removed_edges),
    )
    all_passed &= _check(
        "unchanged.py never appears in any added/removed/modified list",
        "unchanged.py" not in result.modified_files
        and all(f.path != "unchanged.py" for f in result.added_files + result.removed_files)
        and all(s.file_path != "unchanged.py" for s in result.added_symbols + result.removed_symbols)
        and all(a.file_path != "unchanged.py" for a in result.added_apis + result.removed_apis)
        and all(
            e.source_file != "unchanged.py" for e in result.added_edges + result.removed_edges
        ),
    )
    return all_passed


def run_no_difference() -> bool:
    print("\n=== identical base/head -> empty ChangeSet ===")
    _, head = _build_fixture()
    result = diff_analysis_runs(head, head)
    return _check(
        "every category empty",
        result.added_files == []
        and result.removed_files == []
        and result.modified_files == []
        and result.added_symbols == []
        and result.removed_symbols == []
        and result.added_apis == []
        and result.removed_apis == []
        and result.added_edges == []
        and result.removed_edges == [],
        str(result),
    )


def run_classify_change() -> bool:
    print("\n=== classify_change: breaking iff a file, symbol or API was removed ===")
    base, head = _build_fixture()
    ok = _check(
        "fixture diff (removed file/symbol/API) -> breaking",
        classify_change(diff_analysis_runs(base, head)) == "breaking",
    )
    ok &= _check(
        "identical runs -> non_breaking", classify_change(diff_analysis_runs(head, head)) == "non_breaking"
    )

    f = FileFact(path="a.py", language=Language.PYTHON, loc=10)
    s1 = SymbolFact(file_path="a.py", name="f", kind=SymbolKind.FUNCTION)
    s2 = SymbolFact(file_path="a.py", name="g", kind=SymbolKind.FUNCTION)
    api1 = ApiFact(method="GET", path="/a", file_path="a.py")
    api2 = ApiFact(method="POST", path="/a", file_path="a.py")
    edge = DependencyEdge(
        source_file="a.py", module=".", language=Language.PYTHON, edge_type=DependencyEdgeType.EXTERNAL,
        target_file=None, target_package="requests", raw_specifier="", line=0,
    )
    old = RunFacts(files=[f], symbols=[s1], apis=[api1], edges=[edge])

    additive = RunFacts(
        files=[f, FileFact(path="b.py", language=Language.PYTHON, loc=3)], symbols=[s1, s2], apis=[api1, api2], edges=[edge]
    )
    ok &= _check(
        "only additions (file + symbol + API) -> non_breaking", classify_change(diff_analysis_runs(old, additive)) == "non_breaking"
    )

    edited = RunFacts(files=[FileFact(path="a.py", language=Language.PYTHON, loc=99)], symbols=[s1], apis=[api1], edges=[])
    ok &= _check(
        "edit inside a body (LOC changed) + an import dropped -> non_breaking",
        classify_change(diff_analysis_runs(old, edited)) == "non_breaking",
    )

    renamed = RunFacts(files=[f], symbols=[s2], apis=[api1], edges=[edge])
    ok &= _check("symbol renamed (removal + addition) -> breaking", classify_change(diff_analysis_runs(old, renamed)) == "breaking")

    api_gone = RunFacts(files=[f], symbols=[s1], apis=[], edges=[edge])
    ok &= _check("API endpoint removed -> breaking", classify_change(diff_analysis_runs(old, api_gone)) == "breaking")

    file_gone = RunFacts(files=[], symbols=[], apis=[], edges=[])
    ok &= _check("file removed -> breaking", classify_change(diff_analysis_runs(old, file_gone)) == "breaking")
    return ok


if __name__ == "__main__":
    r1 = run_full_diff()
    r2 = run_no_difference()
    r3 = run_classify_change()
    print("\n=== Summary ===")
    print(f"full_diff:        {'PASS' if r1 else 'FAIL'}")
    print(f"no_difference:    {'PASS' if r2 else 'FAIL'}")
    print(f"classify_change:  {'PASS' if r3 else 'FAIL'}")
    sys.exit(0 if (r1 and r2 and r3) else 1)
