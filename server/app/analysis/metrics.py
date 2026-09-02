"""Code metrics (Phase 3.7): an approximate per-function cyclomatic
complexity, per-file LOC, and per-module aggregation — feeds
`healthMetrics`/`fileInsight.complexity` in later phases.

`compute_function_complexity` deliberately reuses the already-extracted
`Symbol` list (`app.analysis.symbols`, Phase 3.3-3.5) for a function's
identity (name/kind/file_path/module/language/line range) rather than
re-deriving names/kinds itself — it locates the matching Tree-sitter node
for each function/method/component `Symbol` by its **end line** (a
symbol's reported span always ends exactly where its underlying
function-like AST node ends, even when the symbol's *start* line is
earlier — e.g. Python's decorator line(s), or a JS `const foo = () => {`
declaration's own statement start — see each `symbols/*.py` module's own
`_emit`/span convention), then counts "decision points" within that node's
own subtree, not descending into a *nested* function-like node (that
nested function gets its own separate complexity entry instead — matches
real cyclomatic-complexity semantics, and mirrors
`app.analysis.symbols.js_ts`'s own `_contains_jsx` scope-boundary
technique).

"Decision point" is a per-language, Tree-sitter node-type allowlist
(`if`/`elif`/`for`/`while`/`catch`/ternary/`case`/logical-and-or, verified
against each grammar's actual node types empirically rather than assumed —
see the language-specific comments below) — an approximation of
McCabe cyclomatic complexity (branches + 1), not the exact metric a
control-flow-graph-based tool would compute. Known, accepted gaps: Python's
`case _:` (wildcard match pattern) is counted the same as any other
`case_clause` since the grammar gives it no distinct "default" node type
(unlike JS `switch_default`/Java `default` label, both correctly excluded);
short-circuit boolean chains (`a and b and c`) count each pairwise operator
node once, matching the grammar's own left-associative nesting.

Pure with respect to the rest of the app: no DB, no ORM, no FastAPI, no
Celery.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter import Node

from app.analysis.language_detect import Language
from app.analysis.symbols import Symbol, SymbolKind
from app.analysis.treesitter import parser_registry


@dataclass(frozen=True)
class FunctionComplexity:
    symbol: Symbol
    complexity: int  # >= 1


@dataclass(frozen=True)
class FileMetrics:
    file_path: str
    module: str  # same "containing directory" convention as Symbol.module
    language: Language | None  # None for a file outside Phase 3's parsed languages
    total_lines: int
    loc: int  # non-blank lines — not "logical" LOC (comments/blank-in-string not distinguished)


@dataclass(frozen=True)
class ModuleMetrics:
    module: str
    file_count: int
    total_lines: int
    loc: int
    function_count: int  # symbols this module has a resolved FunctionComplexity for
    avg_complexity: float | None  # None when function_count == 0
    max_complexity: int | None


_COMPLEXITY_SYMBOL_KINDS = frozenset({SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.COMPONENT})

# One entry per language Tree-sitter grammar this platform parses; a
# language with no entry (or an empty set) contributes no complexity
# results — `compute_function_complexity` returns `[]` for it.
_FUNCTION_LIKE_TYPES: dict[Language, frozenset[str]] = {
    Language.JAVASCRIPT: frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "function_expression",
            "generator_function",
            "arrow_function",
            "method_definition",
        }
    ),
    Language.PYTHON: frozenset({"function_definition"}),
    Language.JAVA: frozenset({"method_declaration", "constructor_declaration"}),
}
_FUNCTION_LIKE_TYPES[Language.TYPESCRIPT] = _FUNCTION_LIKE_TYPES[Language.JAVASCRIPT]
_FUNCTION_LIKE_TYPES[Language.TSX] = _FUNCTION_LIKE_TYPES[Language.JAVASCRIPT]

# Node types that are unconditionally a decision point wherever they occur
# — verified against each grammar's real parse tree (not assumed from
# other grammars' node names, which differ: e.g. Java's `enhanced_for
# _statement` vs. JS's single `for_in_statement` covering both `for-in`
# and `for-of`).
_SIMPLE_DECISION_TYPES: dict[Language, frozenset[str]] = {
    Language.JAVASCRIPT: frozenset(
        {"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement", "catch_clause", "ternary_expression"}
    ),
    Language.PYTHON: frozenset(
        {
            "if_statement",
            "elif_clause",
            "for_statement",
            "while_statement",
            "except_clause",
            "conditional_expression",
            "boolean_operator",
            "if_clause",  # a comprehension's filtering `if`
            "case_clause",  # `match`/`case` — see module docstring on the `case _:` gap
        }
    ),
    Language.JAVA: frozenset(
        {"if_statement", "for_statement", "enhanced_for_statement", "while_statement", "do_statement", "catch_clause", "ternary_expression"}
    ),
}
_SIMPLE_DECISION_TYPES[Language.TYPESCRIPT] = _SIMPLE_DECISION_TYPES[Language.JAVASCRIPT]
_SIMPLE_DECISION_TYPES[Language.TSX] = _SIMPLE_DECISION_TYPES[Language.JAVASCRIPT]

_JS_LOGICAL_OPERATORS = frozenset({"&&", "||", "??"})
_JAVA_LOGICAL_OPERATORS = frozenset({"&&", "||"})
_JS_FAMILY = (Language.JAVASCRIPT, Language.TYPESCRIPT, Language.TSX)


def compute_function_complexity(
    source: bytes, *, file_path: str, language: Language, symbols: list[Symbol]
) -> list[FunctionComplexity]:
    """Computes an approximate cyclomatic complexity for every `symbols`
    entry (already extracted via the matching `app.analysis.symbols.*`
    module) that belongs to `file_path` and is a function-like kind
    (`FUNCTION`, `METHOD`, or `COMPONENT` — not `CLASS`, which has no
    complexity of its own, only its methods do). A symbol whose matching
    AST node can't be located (should not happen in practice — see the
    module docstring's end-line-matching invariant) is silently omitted
    rather than raising, consistent with the rest of Phase 3's "never fail
    the whole file over one unresolved thing" stance."""
    function_like_types = _FUNCTION_LIKE_TYPES.get(language)
    if not function_like_types:
        return []

    parser = parser_registry.get_parser(language)
    tree = parser.parse(source)
    nodes_by_end_line: dict[int, list[Node]] = defaultdict(list)
    _collect_function_nodes(tree.root_node, function_like_types, nodes_by_end_line)

    results: list[FunctionComplexity] = []
    for symbol in symbols:
        if symbol.file_path != file_path or symbol.kind not in _COMPLEXITY_SYMBOL_KINDS:
            continue
        candidates = [
            node for node in nodes_by_end_line.get(symbol.line_end, []) if node.start_point[0] + 1 >= symbol.line_start
        ]
        if not candidates:
            continue
        # On the rare collision (two function-like nodes ending on the same
        # line), the first in document order wins — an accepted, narrow
        # inaccuracy rather than added disambiguation machinery.
        node = candidates[0]
        complexity = 1 + _count_decision_points(node, language, function_like_types, is_root=True)
        results.append(FunctionComplexity(symbol=symbol, complexity=complexity))
    return results


def compute_file_metrics(source: bytes, *, file_path: str, language: Language | None) -> FileMetrics:
    """Line-count metrics for one file — computed for every ingested file
    regardless of language (matches the plan's "everything else is still
    counted toward file totals" stance for unparsed languages), using a
    best-effort text decode (`errors="replace"`) that never raises even on
    a binary file; the resulting counts are meaningless for a binary file
    but harmless (Phase 3 has no separate "is this file text" gate)."""
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    loc = sum(1 for line in lines if line.strip())
    return FileMetrics(
        file_path=file_path,
        module=str(PurePosixPath(file_path).parent),
        language=language,
        total_lines=len(lines),
        loc=loc,
    )


def aggregate_module_metrics(
    file_metrics: list[FileMetrics], function_complexities: list[FunctionComplexity]
) -> list[ModuleMetrics]:
    """Rolls per-file and per-function metrics up to `Symbol.module`'s
    "containing directory" grain, sorted by module path. A module that
    has files but zero resolved function complexities (e.g. a directory of
    only config/markdown files) still gets a `ModuleMetrics` row, with
    `avg_complexity`/`max_complexity` both `None`."""
    files_by_module: dict[str, list[FileMetrics]] = defaultdict(list)
    for fm in file_metrics:
        files_by_module[fm.module].append(fm)

    complexities_by_module: dict[str, list[int]] = defaultdict(list)
    for fc in function_complexities:
        complexities_by_module[fc.symbol.module].append(fc.complexity)

    modules = set(files_by_module) | set(complexities_by_module)
    results = [
        ModuleMetrics(
            module=module,
            file_count=len(files_by_module.get(module, [])),
            total_lines=sum(f.total_lines for f in files_by_module.get(module, [])),
            loc=sum(f.loc for f in files_by_module.get(module, [])),
            function_count=len(complexities_by_module.get(module, [])),
            avg_complexity=(
                sum(complexities_by_module[module]) / len(complexities_by_module[module])
                if complexities_by_module.get(module)
                else None
            ),
            max_complexity=max(complexities_by_module[module]) if complexities_by_module.get(module) else None,
        )
        for module in modules
    ]
    return sorted(results, key=lambda m: m.module)


# --- AST walking --------------------------------------------------------------


def _collect_function_nodes(node: Node, function_like_types: frozenset[str], out: dict[int, list[Node]]) -> None:
    if node.type in function_like_types:
        out[node.end_point[0] + 1].append(node)
    for child in node.children:
        _collect_function_nodes(child, function_like_types, out)


def _count_decision_points(node: Node, language: Language, function_like_types: frozenset[str], *, is_root: bool) -> int:
    """Counts decision points in `node`'s subtree, not descending into a
    *nested* function-like node (`is_root=False` and `node.type` is itself
    function-like) — that nested function's decisions belong to its own,
    separately-computed complexity, not this one's. Mirrors
    `app.analysis.symbols.js_ts._contains_jsx`'s identical scope-boundary
    technique."""
    if not is_root and node.type in function_like_types:
        return 0
    count = 1 if _is_decision_node(node, language) else 0
    for child in node.children:
        count += _count_decision_points(child, language, function_like_types, is_root=False)
    return count


def _is_decision_node(node: Node, language: Language) -> bool:
    if node.type in _SIMPLE_DECISION_TYPES.get(language, frozenset()):
        return True
    if language in _JS_FAMILY:
        if node.type == "switch_case":  # not `switch_default`
            return True
        if node.type == "binary_expression":
            return _has_operator_child(node, _JS_LOGICAL_OPERATORS)
    elif language is Language.JAVA:
        if node.type == "switch_label":
            return bool(node.children) and node.children[0].type == "case"  # not `default`
        if node.type == "binary_expression":
            return _has_operator_child(node, _JAVA_LOGICAL_OPERATORS)
    return False


def _has_operator_child(node: Node, operators: frozenset[str]) -> bool:
    return any(child.type in operators for child in node.children)
