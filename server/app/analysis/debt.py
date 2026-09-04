"""Rule-based technical-debt engine (Phase 4.3): six MVP heuristics over
already-computed Phase 3/4.2 facts, producing plain debt-result dataclasses
plus one aggregate per-run score.

Same DB-free discipline as `graph.py` (4.2), not an exception to it: takes
plain fact dataclasses in — `FileMetrics`/`ModuleMetrics`/`FunctionComplexity`
(`app.analysis.metrics`, Phase 3.7), `Symbol` (`app.analysis.symbols`, Phase
3.3-3.5), `GraphAnalysis` (`app.analysis.graph`, Phase 4.2), and
`ExternalDependency` (`app.analysis.dependencies`, Phase 3.7) — and returns
plain dataclasses out. No SQLAlchemy/ORM rows, no DB session, no FastAPI, no
Celery. `repo_service.py` (Phase 4.4) is the caller that builds these input
facts from just-inserted DB rows and turns this module's `DebtIssue` results
into real `DebtIssue` *rows* (same name, different thing — mirrors
`graph.py`'s own `DependencyEdge`-dataclass-vs-`DependencyEdge`-DB-model
naming collision, see that module's docstring).

**`DebtIssueType`/`DebtSeverity` close the gap flagged by
`docs/BACKEND_IMPLEMENTATION_PLAN.md`'s Known Issue #10**: Phase 4.1's
`DebtIssue.issue_type`/`.severity` DB columns were left as plain `String`
because this module didn't exist yet at migration time, so there was no
Python enum to mirror (unlike `Symbol.kind`, `DependencyEdge.edge_type`,
etc., which all mirror a `analysis/`-defined enum already). Now that this
module defines a closed set of both, upgrading those two DB columns to
`Enum(..., native_enum=False)` is a reasonable follow-up migration —
deliberately not done here, since 4.3 has no DB dependency at all; left for
whoever next touches Phase 4.4 or its migration to decide.

**Every `DebtIssue.metric_value` below is a single `float`**, per Known
Issue #11 (`DebtIssue.metric_value` is one `Float` DB column covering every
rule's number, not per-type columns) — each rule resolves its own natural
metric (a complexity count, a LOC count, a coupling score, a cycle size, a
missing-docs ratio, a major-version gap) down to one float-compatible
number up front, rather than leaving that resolution to a later phase.

Six rules, each independent and additive to the same flat `issues` list:

1. **High per-function complexity** — `FunctionComplexity.complexity` (already
   computed by Phase 3.7's `metrics.py`) against three ascending thresholds.
2. **Large files** — `FileMetrics.loc` against three ascending thresholds.
3. **Large modules** — `ModuleMetrics.loc` against three ascending thresholds
   (distinct constants from #2 — a module is an aggregate of many files, so
   its "large" bar is set higher, not reused from the per-file one).
4. **Excessive coupling** — `GraphAnalysis.coupling` (4.2's fan-in + fan-out
   `CouplingScore.score`) against three ascending thresholds.
5. **Circular dependencies** — one issue per `GraphAnalysis.cycles` entry
   (4.2's Tarjan SCC output, already filtered down to real cycles — see that
   module's docstring), severity tiered by cycle size (file count) rather
   than fixed, so a same-file self-loop and a ten-file cycle don't read as
   equally bad.
6. **Missing documentation** — `Symbol.has_docstring` ratio per module (any
   `Symbol` kind counts — function, class, method, or component all can
   carry a docstring), only for modules with at least
   `MIN_SYMBOLS_FOR_DOC_CHECK` symbols (skips flagging a module of one
   trivial helper as "100% undocumented" — too small a sample to mean
   anything).
7. *(Bonus, not separately numbered in the plan's own list but a genuinely
   distinct check)* **Outdated dependencies** — a **naive version-age
   heuristic only**, exactly as scoped: a small, hardcoded MVP table of
   well-known packages' current major version (`_KNOWN_MINIMUM_MAJOR_
   VERSIONS`, snapshotted by hand, not fetched from anywhere), compared
   against each `ExternalDependency.version`'s parsed leading major-version
   number. **No CVE lookup, no package-registry call, no external
   vulnerability service** — a dependency not in the table (including every
   real dependency of a repo this table doesn't happen to know about) is
   silently skipped, not flagged as "unknown" or "fine"; this only ever
   fires for the handful of packages the table actually lists. A package
   that stays pre-1.0 by design indefinitely (e.g. FastAPI) is deliberately
   left out of the table — a major-version-gap heuristic doesn't mean
   anything for a package whose major version doesn't track "how current
   it is".

All thresholds below are plain module-level constants, deliberately not
configurable yet (matches the plan's "simple MVP constants" instruction) —
picked as reasonable-looking round numbers, not derived from any external
study. Revisit if real-repo dogfooding (4.4+) shows them firing too often
or too rarely.

Pure with respect to the rest of the app: no DB, no ORM, no FastAPI, no
Celery.
"""

from __future__ import annotations

import enum
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.analysis.dependencies import Ecosystem, ExternalDependency
from app.analysis.graph import CouplingScore, CycleResult, GraphAnalysis
from app.analysis.metrics import FileMetrics, FunctionComplexity, ModuleMetrics
from app.analysis.symbols import Symbol


class DebtIssueType(str, enum.Enum):
    HIGH_COMPLEXITY = "high_complexity"
    LARGE_FILE = "large_file"
    LARGE_MODULE = "large_module"
    EXCESSIVE_COUPLING = "excessive_coupling"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    MISSING_DOCUMENTATION = "missing_documentation"
    OUTDATED_DEPENDENCY = "outdated_dependency"


class DebtSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Ascending rank purely for sorting the final issue list (most severe
# first) — not exposed as part of any rule's own logic.
_SEVERITY_RANK: dict[DebtSeverity, int] = {
    DebtSeverity.LOW: 0,
    DebtSeverity.MEDIUM: 1,
    DebtSeverity.HIGH: 2,
    DebtSeverity.CRITICAL: 3,
}

# Aggregate-score weights (see `_aggregate_score`): a plain, undocumented-
# anywhere-else MVP choice, same spirit as `CouplingScore.score`'s own "plain
# sum ... not normalized ... a later phase's call" stance (`graph.py`) — not
# derived from anything, just monotonically increasing with severity.
_SEVERITY_WEIGHT: dict[DebtSeverity, float] = {
    DebtSeverity.LOW: 1.0,
    DebtSeverity.MEDIUM: 2.0,
    DebtSeverity.HIGH: 3.0,
    DebtSeverity.CRITICAL: 5.0,
}


@dataclass(frozen=True)
class DebtIssue:
    """One rule violation. `file_path`/`module` are independently nullable
    (mirrors Phase 4.1's `DebtIssue.file_id`/`.module` DB columns, both
    nullable) — a rule that doesn't have a single natural file target
    (large-module, excessive-coupling, circular-dependency, missing-docs)
    leaves `file_path=None` and sets `module` instead; a rule that does
    (high-complexity, large-file, outdated-dependency) sets `file_path` and
    leaves `module` as whatever `Symbol`/`FileMetrics` already call the
    containing directory, or `None` when there isn't one meaningful reading
    (see `_check_circular_dependencies`, which sets neither: a cycle
    routinely spans more than one file *and* more than one module, so
    forcing either field to one arbitrary representative would be
    misleading — the full file list already lives in `description`)."""

    issue_type: DebtIssueType
    severity: DebtSeverity
    file_path: str | None
    module: str | None
    description: str
    metric_value: float


@dataclass(frozen=True)
class DebtAnalysis:
    issues: list[DebtIssue]
    score: float


# --- thresholds (medium, high, critical) — see module docstring ---------------

COMPLEXITY_THRESHOLDS: tuple[float, float, float] = (10, 20, 30)
LARGE_FILE_LOC_THRESHOLDS: tuple[float, float, float] = (300, 600, 1000)
LARGE_MODULE_LOC_THRESHOLDS: tuple[float, float, float] = (1500, 3000, 6000)
COUPLING_SCORE_THRESHOLDS: tuple[float, float, float] = (10, 20, 35)
# Cycle size (file count) — 1 as the MEDIUM floor, not 2, so a same-file
# self-loop (`graph.py`'s smallest possible real cycle) still registers as
# debt rather than silently passing every rule.
CIRCULAR_DEPENDENCY_SIZE_THRESHOLDS: tuple[float, float, float] = (1, 3, 6)
MISSING_DOCUMENTATION_RATIO_THRESHOLDS: tuple[float, float, float] = (0.5, 0.75, 0.95)
OUTDATED_DEPENDENCY_MAJORS_BEHIND_THRESHOLDS: tuple[float, float, float] = (1, 2, 4)

# A module needs at least this many total symbols before its doc-coverage
# ratio is even considered — otherwise one undocumented helper in an
# otherwise-tiny module reads as "100% undocumented", which isn't a
# meaningful signal at that sample size.
MIN_SYMBOLS_FOR_DOC_CHECK = 3

# A small, hand-maintained MVP snapshot of well-known packages' current
# major version — **not** fetched from a registry or CVE feed, per the
# plan's explicit "naive version-age heuristic ... not a real CVE lookup"
# scoping. Keys are `(ecosystem, normalized_name)` — see
# `_normalize_package_name` for what "normalized" means; Maven's `name` is
# always `f"{group_id}:{artifact_id}"` (see `imports.py::parse_pom_xml`), so
# its table entries carry the full `group:artifact` key, not just the
# artifact id. A dependency not listed here is never flagged either way —
# this only ever fires for the handful of packages actually in the table.
#
# Snapshotted 2026-09 — a hand-picked, point-in-time reading of each
# package's "current" major version, not re-validated automatically. Expect
# this table to drift stale; refresh by hand occasionally, there's no
# mechanism that does it for you (see `docs/BACKEND_IMPLEMENTATION_PLAN.md`'s
# "What Does NOT Get Built in the MVP Cut" for why this stays a hand-
# maintained table rather than a real registry/CVE integration).
_KNOWN_MINIMUM_MAJOR_VERSIONS: dict[tuple[Ecosystem, str], int] = {
    (Ecosystem.NPM, "react"): 18,
    (Ecosystem.NPM, "react-dom"): 18,
    (Ecosystem.NPM, "express"): 4,
    (Ecosystem.NPM, "lodash"): 4,
    (Ecosystem.NPM, "axios"): 1,
    (Ecosystem.PIP, "django"): 4,
    (Ecosystem.PIP, "flask"): 2,
    (Ecosystem.PIP, "requests"): 2,
    (Ecosystem.PIP, "sqlalchemy"): 2,
    (Ecosystem.MAVEN, "org.springframework.boot:spring-boot-starter-web"): 3,
}

_LEADING_INT_RE = re.compile(r"(\d+)")


def analyze_technical_debt(
    *,
    file_metrics: list[FileMetrics],
    module_metrics: list[ModuleMetrics],
    function_complexity: list[FunctionComplexity],
    symbols: list[Symbol],
    graph_analysis: GraphAnalysis,
    external_dependencies: list[ExternalDependency],
) -> DebtAnalysis:
    """The single entry point: runs all six/seven rules (see module
    docstring) over the given Phase 3.7 + 4.2 facts and returns one bundled
    result — the flat issue list (sorted most-severe-first, then by
    issue type, then by target, purely for deterministic output; the plan
    doesn't otherwise prescribe an order) plus the aggregate score
    (`_aggregate_score`)."""
    issues: list[DebtIssue] = [
        *_check_high_complexity(function_complexity),
        *_check_large_files(file_metrics),
        *_check_large_modules(module_metrics),
        *_check_excessive_coupling(graph_analysis.coupling),
        *_check_circular_dependencies(graph_analysis.cycles),
        *_check_missing_documentation(symbols),
        *_check_outdated_dependencies(external_dependencies),
    ]
    issues.sort(
        key=lambda i: (
            -_SEVERITY_RANK[i.severity],
            i.issue_type.value,
            i.file_path or "",
            i.module or "",
            i.description,
        )
    )
    return DebtAnalysis(issues=issues, score=_aggregate_score(issues))


def _aggregate_score(issues: list[DebtIssue]) -> float:
    """Plain sum of each issue's severity weight (`_SEVERITY_WEIGHT`) — not
    normalized to any particular range, same "that's a later phase's call"
    stance `graph.py::CouplingScore.score` already takes for its own
    unnormalized sum. Zero issues -> score 0.0, the clean-repo case."""
    return sum(_SEVERITY_WEIGHT[issue.severity] for issue in issues)


def _tier(value: float, thresholds: tuple[float, float, float]) -> DebtSeverity | None:
    """Maps `value` against `(medium, high, critical)` ascending
    thresholds. `None` means the rule doesn't fire at all — `value` didn't
    clear even the lowest bar."""
    medium, high, critical = thresholds
    if value >= critical:
        return DebtSeverity.CRITICAL
    if value >= high:
        return DebtSeverity.HIGH
    if value >= medium:
        return DebtSeverity.MEDIUM
    return None


# --- rule 1: high per-function complexity --------------------------------------


def _check_high_complexity(function_complexity: list[FunctionComplexity]) -> list[DebtIssue]:
    issues: list[DebtIssue] = []
    for fc in function_complexity:
        severity = _tier(fc.complexity, COMPLEXITY_THRESHOLDS)
        if severity is None:
            continue
        issues.append(
            DebtIssue(
                issue_type=DebtIssueType.HIGH_COMPLEXITY,
                severity=severity,
                file_path=fc.symbol.file_path,
                module=fc.symbol.module,
                description=(
                    f"{fc.symbol.kind.value} '{fc.symbol.name}' (lines {fc.symbol.line_start}-"
                    f"{fc.symbol.line_end}) has cyclomatic complexity {fc.complexity}"
                ),
                metric_value=float(fc.complexity),
            )
        )
    return issues


# --- rule 2: large files -------------------------------------------------------


def _check_large_files(file_metrics: list[FileMetrics]) -> list[DebtIssue]:
    issues: list[DebtIssue] = []
    for fm in file_metrics:
        severity = _tier(fm.loc, LARGE_FILE_LOC_THRESHOLDS)
        if severity is None:
            continue
        issues.append(
            DebtIssue(
                issue_type=DebtIssueType.LARGE_FILE,
                severity=severity,
                file_path=fm.file_path,
                module=fm.module,
                description=f"file has {fm.loc} non-blank lines of code ({fm.total_lines} total)",
                metric_value=float(fm.loc),
            )
        )
    return issues


# --- rule 3: large modules -----------------------------------------------------


def _check_large_modules(module_metrics: list[ModuleMetrics]) -> list[DebtIssue]:
    issues: list[DebtIssue] = []
    for mm in module_metrics:
        severity = _tier(mm.loc, LARGE_MODULE_LOC_THRESHOLDS)
        if severity is None:
            continue
        issues.append(
            DebtIssue(
                issue_type=DebtIssueType.LARGE_MODULE,
                severity=severity,
                file_path=None,
                module=mm.module,
                description=f"module has {mm.loc} non-blank lines of code across {mm.file_count} file(s)",
                metric_value=float(mm.loc),
            )
        )
    return issues


# --- rule 4: excessive coupling ------------------------------------------------


def _check_excessive_coupling(coupling: list[CouplingScore]) -> list[DebtIssue]:
    issues: list[DebtIssue] = []
    for c in coupling:
        severity = _tier(c.score, COUPLING_SCORE_THRESHOLDS)
        if severity is None:
            continue
        issues.append(
            DebtIssue(
                issue_type=DebtIssueType.EXCESSIVE_COUPLING,
                severity=severity,
                file_path=None,
                module=c.module,
                description=f"module has coupling score {c.score} (fan_in={c.fan_in}, fan_out={c.fan_out})",
                metric_value=float(c.score),
            )
        )
    return issues


# --- rule 5: circular dependencies ---------------------------------------------


def _check_circular_dependencies(cycles: list[CycleResult]) -> list[DebtIssue]:
    issues: list[DebtIssue] = []
    for cycle in cycles:
        size = len(cycle.files)
        severity = _tier(size, CIRCULAR_DEPENDENCY_SIZE_THRESHOLDS)
        if severity is None:
            continue
        files_sorted = sorted(cycle.files)
        issues.append(
            DebtIssue(
                issue_type=DebtIssueType.CIRCULAR_DEPENDENCY,
                severity=severity,
                file_path=None,  # spans >=1 file and often >1 module — no single natural target, see DebtIssue's own docstring
                module=None,
                description=f"circular dependency across {size} file(s): {', '.join(files_sorted)}",
                metric_value=float(size),
            )
        )
    return issues


# --- rule 6: missing documentation ---------------------------------------------


def _check_missing_documentation(symbols: list[Symbol]) -> list[DebtIssue]:
    by_module: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in symbols:
        by_module[symbol.module].append(symbol)

    issues: list[DebtIssue] = []
    for module, module_symbols in by_module.items():
        total = len(module_symbols)
        if total < MIN_SYMBOLS_FOR_DOC_CHECK:
            continue
        missing = sum(1 for s in module_symbols if not s.has_docstring)
        ratio = missing / total
        severity = _tier(ratio, MISSING_DOCUMENTATION_RATIO_THRESHOLDS)
        if severity is None:
            continue
        issues.append(
            DebtIssue(
                issue_type=DebtIssueType.MISSING_DOCUMENTATION,
                severity=severity,
                file_path=None,
                module=module,
                description=f"{missing}/{total} symbols in module lack a docstring ({ratio:.0%})",
                metric_value=ratio,
            )
        )
    return issues


# --- rule 7: outdated dependencies (naive version-age heuristic) --------------


def _check_outdated_dependencies(external_dependencies: list[ExternalDependency]) -> list[DebtIssue]:
    issues: list[DebtIssue] = []
    for dep in external_dependencies:
        floor_major = _KNOWN_MINIMUM_MAJOR_VERSIONS.get((dep.ecosystem, _normalize_package_name(dep.name)))
        if floor_major is None:
            continue  # not in the MVP table — silently skipped, not flagged either way
        declared_major = _parse_major_version(dep.version)
        if declared_major is None:
            continue  # unparseable (e.g. "latest", "*", an unresolved Maven property) — can't judge age
        majors_behind = floor_major - declared_major
        if majors_behind <= 0:
            continue
        severity = _tier(majors_behind, OUTDATED_DEPENDENCY_MAJORS_BEHIND_THRESHOLDS)
        if severity is None:
            continue
        issues.append(
            DebtIssue(
                issue_type=DebtIssueType.OUTDATED_DEPENDENCY,
                severity=severity,
                file_path=dep.manifest_path,
                module=str(PurePosixPath(dep.manifest_path).parent),
                description=(
                    f"{dep.name}@{dep.version} is {majors_behind} major version(s) behind the "
                    f"known-current major ({floor_major}.x)"
                ),
                metric_value=float(majors_behind),
            )
        )
    return issues


def _normalize_package_name(name: str) -> str:
    """Case/separator normalization only (`Flask` vs `flask`, `some_pkg` vs
    `some-pkg`) — enough to match `_KNOWN_MINIMUM_MAJOR_VERSIONS`'s
    hand-written keys against a manifest's as-written spelling without
    trying to fully replicate either ecosystem's real name-normalization
    rules."""
    return name.strip().lower().replace("_", "-")


def _parse_major_version(version: str | None) -> int | None:
    """Naive by design (see module docstring): finds the first run of
    digits anywhere in the raw version string and treats it as the major
    version — handles a plain `"18.2.0"`, a range/prefix form
    (`"^18.2.0"`, `"~=2.0"`, `">=2.0,<3.0"` -> the lower bound's major), and
    reports `None` (unparseable) for anything with no leading digit at all
    (`"latest"`, `"*"`, an unresolved Maven `${property}` placeholder) —
    never guesses in that case."""
    if not version:
        return None
    match = _LEADING_INT_RE.search(version)
    if match is None:
        return None
    return int(match.group(1))
