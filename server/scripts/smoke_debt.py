"""Manual smoke test for Phase 4.3's `app.analysis.debt`.

Unlike `smoke_graph.py`/`smoke_dependencies.py` (which feed real fixture
*source files* through the real Tree-sitter pipeline), `debt.py` operates
purely on already-computed plain fact dataclasses — so this script builds
those dataclasses directly by hand rather than parsing anything, exactly as
the plan's own 4.3 "Check" instruction describes ("fixtures (plain fact
dataclasses, no DB involved) engineered to trip each rule at least once").

Two fixtures:

- `run_dirty()` — one deliberately-planted violation per rule, assembled
  into a single combined input (matching how `repo_service.py`, Phase 4.4,
  will actually call this module: once per analysis run, with every rule's
  facts present at once, not one call per rule). Each planted value is
  chosen to land in a different severity tier on purpose (not just
  MEDIUM/HIGH/CRITICAL picked at random) so this also exercises `_tier`'s
  three-way boundary, not only "the rule fires at all":
    1. high complexity: 35 -> CRITICAL (threshold 30)
    2. large file: 350 LOC -> MEDIUM (threshold 300)
    3. large module: 4000 LOC -> HIGH (threshold 3000)
    4. excessive coupling: score 40 -> CRITICAL (threshold 35)
    5. circular dependency: a 2-file cycle -> MEDIUM (threshold 1)
    6. missing documentation: 3/5 undocumented in one module -> MEDIUM (ratio 0.6, threshold 0.5)
    7. outdated dependency: npm `react@13.2.0` vs the table's known-current
       major 18 -> 5 majors behind -> CRITICAL (threshold 4)
  Each fixture piece lives in its own distinctly-named file/module so none
  of the seven rules accidentally cross-trigger on another rule's planted
  data (e.g. the complexity fixture's own file is far too short to also
  trip the large-file rule) — the dirty run asserts exactly 7 issues, one
  per type, with the exact expected severity and metric value, plus the
  aggregate score as the hand-computed sum of each one's severity weight
  (5.0 CRITICAL + 2.0 MEDIUM + 3.0 HIGH + 5.0 CRITICAL + 2.0 MEDIUM + 2.0
  MEDIUM + 5.0 CRITICAL = 24.0).
- `run_clean()` — one small, fully-documented, up-to-date, acyclic,
  uncoupled fixture -> asserts zero issues and a 0.0 score.
- `run_empty()` — every input empty -> asserts zero issues and a 0.0 score,
  same spirit as `smoke_graph.py::run_empty`.

Run: `uv run python scripts/smoke_debt.py` (from `server/`).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.debt import (  # noqa: E402
    DebtIssueType,
    DebtSeverity,
    analyze_technical_debt,
)
from app.analysis.dependencies import Ecosystem, ExternalDependency  # noqa: E402
from app.analysis.graph import CouplingScore, CycleResult, GraphAnalysis  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.metrics import FileMetrics, FunctionComplexity, ModuleMetrics  # noqa: E402
from app.analysis.symbols import Symbol, SymbolKind  # noqa: E402


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def make_symbol(name: str, file_path: str, module: str, *, has_docstring: bool) -> Symbol:
    return Symbol(
        name=name,
        kind=SymbolKind.FUNCTION,
        file_path=file_path,
        line_start=1,
        line_end=2,
        module=module,
        language=Language.PYTHON,
        has_docstring=has_docstring,
    )


@dataclass(frozen=True)
class DebtFixture:
    """Bundles one `analyze_technical_debt` call's worth of inputs. Pulled
    out into its own type (rather than inlined directly in `run_dirty`/
    `run_clean` below) so `scripts/smoke_storage.py` (Phase 4.4) can build
    a matching `AnalysisResult` from these *exact* already-hand-verified
    fixture values, instead of inventing a new algorithmic fixture of its
    own for `persist_analysis_run`'s own debt-issue-persistence check."""

    file_metrics: list[FileMetrics]
    module_metrics: list[ModuleMetrics]
    function_complexity: list[FunctionComplexity]
    symbols: list[Symbol]
    graph_analysis: GraphAnalysis
    external_dependencies: list[ExternalDependency]


def build_dirty_fixture() -> DebtFixture:
    """One deliberately-planted violation per rule — see this module's own
    docstring for the exact planted values/expected tiers."""
    # 1. high complexity -> CRITICAL (>= 30)
    complex_symbol = make_symbol(
        "do_everything", "app/complex_module/big_function.py", "app/complex_module", has_docstring=True
    )
    function_complexity = [FunctionComplexity(symbol=complex_symbol, complexity=35)]

    # 2. large file -> MEDIUM (>= 300, < 600)
    file_metrics = [
        FileMetrics(
            file_path="app/large_file_module/giant.py",
            module="app/large_file_module",
            language=Language.PYTHON,
            total_lines=400,
            loc=350,
        )
    ]

    # 3. large module -> HIGH (>= 3000, < 6000)
    module_metrics = [
        ModuleMetrics(
            module="app/big_module",
            file_count=10,
            total_lines=4500,
            loc=4000,
            function_count=10,
            avg_complexity=5.0,
            max_complexity=12,
        )
    ]

    # 4. excessive coupling -> CRITICAL (>= 35)
    coupling = [CouplingScore(module="app/coupled_module", fan_in=20, fan_out=20, score=40)]

    # 5. circular dependency -> MEDIUM (a 2-file cycle, size >= 1, < 3)
    cyclic_files = frozenset({"app/cyclic_module/a.py", "app/cyclic_module/b.py"})
    graph_analysis = GraphAnalysis(
        cycles=[CycleResult(files=cyclic_files, edges=())],
        circular_files=cyclic_files,
        circular_edges=(),
        coupling=coupling,
        orphans=[],
    )

    # 6. missing documentation -> MEDIUM (3/5 undocumented = 0.6 ratio, >= 0.5, < 0.75)
    doc_module = "app/doc_module"
    doc_symbols = [
        make_symbol("a", "app/doc_module/x.py", doc_module, has_docstring=True),
        make_symbol("b", "app/doc_module/x.py", doc_module, has_docstring=False),
        make_symbol("c", "app/doc_module/x.py", doc_module, has_docstring=False),
        make_symbol("d", "app/doc_module/x.py", doc_module, has_docstring=False),
        make_symbol("e", "app/doc_module/x.py", doc_module, has_docstring=True),
    ]
    # complex_symbol's own module ("app/complex_module") has only 1 symbol —
    # below MIN_SYMBOLS_FOR_DOC_CHECK, so it can't also register a (spurious)
    # missing-documentation issue.
    symbols = [complex_symbol, *doc_symbols]

    # 7. outdated dependency -> CRITICAL (react's known-current major 18,
    # declared 13 -> 5 majors behind, >= 4)
    external_dependencies = [
        ExternalDependency(name="react", version="13.2.0", manifest_path="package.json", ecosystem=Ecosystem.NPM)
    ]

    return DebtFixture(
        file_metrics=file_metrics,
        module_metrics=module_metrics,
        function_complexity=function_complexity,
        symbols=symbols,
        graph_analysis=graph_analysis,
        external_dependencies=external_dependencies,
    )


def build_clean_fixture() -> DebtFixture:
    """One small, fully-documented, up-to-date, acyclic, uncoupled
    fixture — expected to produce zero issues / a 0.0 score."""
    clean_symbol = make_symbol("clean_fn", "app/clean_module/clean.py", "app/clean_module", has_docstring=True)
    return DebtFixture(
        file_metrics=[
            FileMetrics(
                file_path="app/clean_module/clean.py",
                module="app/clean_module",
                language=Language.PYTHON,
                total_lines=10,
                loc=8,
            )
        ],
        module_metrics=[
            ModuleMetrics(
                module="app/clean_module",
                file_count=1,
                total_lines=10,
                loc=8,
                function_count=1,
                avg_complexity=2.0,
                max_complexity=2,
            )
        ],
        function_complexity=[FunctionComplexity(symbol=clean_symbol, complexity=2)],
        symbols=[clean_symbol],  # a single symbol — below MIN_SYMBOLS_FOR_DOC_CHECK regardless of doc ratio
        graph_analysis=GraphAnalysis(cycles=[], circular_files=frozenset(), circular_edges=(), coupling=[], orphans=[]),
        external_dependencies=[
            ExternalDependency(name="react", version="18.2.0", manifest_path="package.json", ecosystem=Ecosystem.NPM)
        ],
    )


def run_dirty() -> bool:
    print("=== dirty fixture — one planted violation per rule ===")
    all_passed = True

    fixture = build_dirty_fixture()
    result = analyze_technical_debt(
        file_metrics=fixture.file_metrics,
        module_metrics=fixture.module_metrics,
        function_complexity=fixture.function_complexity,
        symbols=fixture.symbols,
        graph_analysis=fixture.graph_analysis,
        external_dependencies=fixture.external_dependencies,
    )

    all_passed &= _check("exactly 7 issues (one per rule)", len(result.issues) == 7, str(result.issues))
    by_type = {issue.issue_type: issue for issue in result.issues}
    all_passed &= _check(
        "no rule fired more than once (7 distinct issue types)", len(by_type) == len(result.issues), str(by_type)
    )

    expectations = [
        (DebtIssueType.HIGH_COMPLEXITY, DebtSeverity.CRITICAL, 35.0),
        (DebtIssueType.LARGE_FILE, DebtSeverity.MEDIUM, 350.0),
        (DebtIssueType.LARGE_MODULE, DebtSeverity.HIGH, 4000.0),
        (DebtIssueType.EXCESSIVE_COUPLING, DebtSeverity.CRITICAL, 40.0),
        (DebtIssueType.CIRCULAR_DEPENDENCY, DebtSeverity.MEDIUM, 2.0),
        (DebtIssueType.MISSING_DOCUMENTATION, DebtSeverity.MEDIUM, 0.6),
        (DebtIssueType.OUTDATED_DEPENDENCY, DebtSeverity.CRITICAL, 5.0),
    ]
    for issue_type, expected_severity, expected_metric in expectations:
        issue = by_type.get(issue_type)
        ok = (
            issue is not None
            and issue.severity is expected_severity
            and abs(issue.metric_value - expected_metric) < 1e-9
        )
        all_passed &= _check(
            f"{issue_type.value}: severity={expected_severity.value}, metric_value={expected_metric}",
            ok,
            str(issue),
        )

    all_passed &= _check(
        "circular_dependency issue has no single file/module target (spans >1 file/module)",
        by_type[DebtIssueType.CIRCULAR_DEPENDENCY].file_path is None
        and by_type[DebtIssueType.CIRCULAR_DEPENDENCY].module is None
        if DebtIssueType.CIRCULAR_DEPENDENCY in by_type
        else False,
    )

    expected_score = 5.0 + 2.0 + 3.0 + 5.0 + 2.0 + 2.0 + 5.0  # CRITICAL+MEDIUM+HIGH+CRITICAL+MEDIUM+MEDIUM+CRITICAL
    all_passed &= _check(
        f"aggregate score == {expected_score} (sum of each issue's severity weight)",
        abs(result.score - expected_score) < 1e-9,
        str(result.score),
    )

    return all_passed


def run_clean() -> bool:
    print("\n=== clean fixture — small, documented, up-to-date, acyclic, uncoupled ===")
    all_passed = True

    fixture = build_clean_fixture()
    result = analyze_technical_debt(
        file_metrics=fixture.file_metrics,
        module_metrics=fixture.module_metrics,
        function_complexity=fixture.function_complexity,
        symbols=fixture.symbols,
        graph_analysis=fixture.graph_analysis,
        external_dependencies=fixture.external_dependencies,
    )

    all_passed &= _check("no issues", result.issues == [], str(result.issues))
    all_passed &= _check("score is 0.0", result.score == 0.0, str(result.score))
    return all_passed


def run_empty() -> bool:
    print("\n=== empty input ===")
    result = analyze_technical_debt(
        file_metrics=[],
        module_metrics=[],
        function_complexity=[],
        symbols=[],
        graph_analysis=GraphAnalysis(cycles=[], circular_files=frozenset(), circular_edges=(), coupling=[], orphans=[]),
        external_dependencies=[],
    )
    return _check(
        "no facts in -> no issues, 0.0 score", result.issues == [] and result.score == 0.0, str(result)
    )


if __name__ == "__main__":
    r1 = run_dirty()
    r2 = run_clean()
    r3 = run_empty()
    print("\n=== Summary ===")
    print(f"dirty: {'PASS' if r1 else 'FAIL'}")
    print(f"clean: {'PASS' if r2 else 'FAIL'}")
    print(f"empty: {'PASS' if r3 else 'FAIL'}")
    sys.exit(0 if (r1 and r2 and r3) else 1)
