"""Manual smoke test for Phase 4.2's `app.analysis.graph`.

Feeds `scripts/fixtures/dependency_cycle/{cyclic,acyclic}/` through the
real Phase 3.7 `extract_import_edges` (exactly like `smoke_dependencies.py`
does) to get a real `DependencyEdge` list, then runs
`analyze_dependency_graph` over it and checks the result against
hand-computed expectations:

- `cyclic/` — `module_a/a.py` <-> `module_b/b.py` deliberately import each
  other (a 2-file cycle); `module_c/c.py` imports `module_a/a.py` but isn't
  imported back (non-cyclic, but adds `module_a` fan-in for the coupling
  check) — via *two* separate import statements targeting the same file
  (mirroring TypeScript's routine type/value import split), regression-
  testing that coupling dedupes to one count per file pair rather than one
  per import statement; `orphan_module/isolated.py` only imports the
  external `os` module, so it has zero internal edges (an orphan of the
  internal graph).
- `acyclic/` — a separate, disjoint fixture tree with a single one-way
  import (`module_x/x.py` -> `module_y/y.py`, `y.py` importing nothing
  back) — confirms a non-cyclic graph reports zero cycles.
- `self_loop/` — a third, disjoint fixture tree: `module_s/s.py`'s own
  absolute import resolves back to itself, a deliberate 1-node cycle.
  Confirms `_detect_cycles`'s self-loop path (untested before this pass)
  actually fires, and pins down `coupling`'s exact edge-case behavior for
  it: the self-edge is same-file (hence same-module), so it contributes to
  neither fan-in nor fan-out, but the module still gets registered by
  merely touching an internal edge — so it appears with
  `fan_in=0, fan_out=0, score=0` rather than being omitted outright. (This
  smoke script's own first draft assumed the opposite — omission — until
  this exact check caught it; see `_compute_coupling`'s comments.)

Run: `uv run python scripts/smoke_graph.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.dependencies.imports import extract_import_edges  # noqa: E402
from app.analysis.graph import analyze_dependency_graph  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "dependency_cycle"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _load_tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)).replace("\\", "/"): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def run_cyclic() -> bool:
    print("=== cyclic/ — deliberate import cycle, non-cyclic edge, and an orphan ===")
    all_passed = True

    root = FIXTURES_DIR / "cyclic"
    files = {f"cyclic/{path}": source for path, source in _load_tree(root).items()}
    edges = extract_import_edges(files)
    result = analyze_dependency_graph(edges)

    all_passed &= _check("exactly one cycle detected", len(result.cycles) == 1, str(result.cycles))
    if result.cycles:
        cycle = result.cycles[0]
        all_passed &= _check(
            "cycle spans exactly module_a/a.py and module_b/b.py",
            cycle.files == frozenset({"cyclic/module_a/a.py", "cyclic/module_b/b.py"}),
            str(cycle.files),
        )
        all_passed &= _check("cycle carries both of its two internal edges", len(cycle.edges) == 2, str(cycle.edges))
    all_passed &= _check(
        "circular_files matches the cycle's own files",
        result.circular_files == frozenset({"cyclic/module_a/a.py", "cyclic/module_b/b.py"}),
        str(result.circular_files),
    )
    all_passed &= _check(
        "module_c/c.py (imports into the cycle, but isn't imported back) is NOT flagged circular",
        "cyclic/module_c/c.py" not in result.circular_files,
        str(result.circular_files),
    )

    coupling_by_module = {c.module: c for c in result.coupling}
    all_passed &= _check(
        "coupling covers exactly module_a, module_b, module_c (orphan_module has no internal edges)",
        set(coupling_by_module) == {"cyclic/module_a", "cyclic/module_b", "cyclic/module_c"},
        str(sorted(coupling_by_module)),
    )
    module_a = coupling_by_module.get("cyclic/module_a")
    all_passed &= _check(
        "module_a: fan_in=2 (from module_b, module_c — module_c's *duplicate* import of the same"
        " file dedupes to 1, not 2), fan_out=1 (to module_b), score=3",
        module_a is not None and (module_a.fan_in, module_a.fan_out, module_a.score) == (2, 1, 3),
        str(module_a),
    )
    module_b = coupling_by_module.get("cyclic/module_b")
    all_passed &= _check(
        "module_b: fan_in=1 (from module_a), fan_out=1 (to module_a), score=2",
        module_b is not None and (module_b.fan_in, module_b.fan_out, module_b.score) == (1, 1, 2),
        str(module_b),
    )
    module_c = coupling_by_module.get("cyclic/module_c")
    all_passed &= _check(
        "module_c: fan_in=0, fan_out=1 (to module_a), score=1",
        module_c is not None and (module_c.fan_in, module_c.fan_out, module_c.score) == (0, 1, 1),
        str(module_c),
    )
    all_passed &= _check(
        "coupling sorted by score desc: module_a, module_b, module_c",
        [c.module for c in result.coupling] == ["cyclic/module_a", "cyclic/module_b", "cyclic/module_c"],
        str([c.module for c in result.coupling]),
    )

    all_passed &= _check(
        "orphan_module/isolated.py (external-only import) reported as the sole orphan",
        result.orphans == ["cyclic/orphan_module/isolated.py"],
        str(result.orphans),
    )

    return all_passed


def run_acyclic() -> bool:
    print("\n=== acyclic/ — one-way import, no cycle ===")
    all_passed = True

    root = FIXTURES_DIR / "acyclic"
    files = {f"acyclic/{path}": source for path, source in _load_tree(root).items()}
    edges = extract_import_edges(files)
    result = analyze_dependency_graph(edges)

    all_passed &= _check("zero cycles reported", result.cycles == [], str(result.cycles))
    all_passed &= _check("circular_files is empty", result.circular_files == frozenset(), str(result.circular_files))
    all_passed &= _check("circular_edges is empty", result.circular_edges == (), str(result.circular_edges))

    coupling_by_module = {c.module: c for c in result.coupling}
    module_x = coupling_by_module.get("acyclic/module_x")
    all_passed &= _check(
        "module_x: fan_in=0, fan_out=1 (to module_y), score=1",
        module_x is not None and (module_x.fan_in, module_x.fan_out, module_x.score) == (0, 1, 1),
        str(module_x),
    )
    module_y = coupling_by_module.get("acyclic/module_y")
    all_passed &= _check(
        "module_y: fan_in=1 (from module_x), fan_out=0, score=1",
        module_y is not None and (module_y.fan_in, module_y.fan_out, module_y.score) == (1, 0, 1),
        str(module_y),
    )

    all_passed &= _check(
        "no orphans (x.py has an internal edge, y.py is its internal target)",
        result.orphans == [],
        str(result.orphans),
    )

    return all_passed


def run_self_loop() -> bool:
    print("\n=== self_loop/ — a file that imports itself ===")
    all_passed = True

    root = FIXTURES_DIR / "self_loop"
    files = {f"self_loop/{path}": source for path, source in _load_tree(root).items()}
    edges = extract_import_edges(files)
    result = analyze_dependency_graph(edges)

    all_passed &= _check("exactly one cycle detected", len(result.cycles) == 1, str(result.cycles))
    if result.cycles:
        cycle = result.cycles[0]
        all_passed &= _check(
            "cycle is the single self-importing file",
            cycle.files == frozenset({"self_loop/module_s/s.py"}),
            str(cycle.files),
        )
        all_passed &= _check(
            "cycle carries its one self-referencing edge",
            len(cycle.edges) == 1
            and cycle.edges[0].source_file == cycle.edges[0].target_file == "self_loop/module_s/s.py",
            str(cycle.edges),
        )
    all_passed &= _check(
        "circular_files flags the self-importing file",
        result.circular_files == frozenset({"self_loop/module_s/s.py"}),
        str(result.circular_files),
    )
    coupling = result.coupling
    all_passed &= _check(
        "self-loop module still registers in coupling (fan_in=0, fan_out=0, score=0) — touching an"
        " internal edge registers it even though a same-module edge counts toward neither side",
        len(coupling) == 1
        and coupling[0].module == "self_loop/module_s"
        and (coupling[0].fan_in, coupling[0].fan_out, coupling[0].score) == (0, 0, 0),
        str(coupling),
    )
    all_passed &= _check(
        "not reported as an orphan (it touches an internal edge, even though it's its own)",
        result.orphans == [],
        str(result.orphans),
    )

    return all_passed


def run_empty() -> bool:
    print("\n=== empty input ===")
    result = analyze_dependency_graph([])
    return _check(
        "no edges in -> no cycles, no coupling, no orphans",
        result.cycles == [] and result.coupling == [] and result.orphans == [],
        str(result),
    )


if __name__ == "__main__":
    r1 = run_cyclic()
    r2 = run_acyclic()
    r3 = run_self_loop()
    r4 = run_empty()
    print("\n=== Summary ===")
    print(f"cyclic:     {'PASS' if r1 else 'FAIL'}")
    print(f"acyclic:    {'PASS' if r2 else 'FAIL'}")
    print(f"self_loop:  {'PASS' if r3 else 'FAIL'}")
    print(f"empty:      {'PASS' if r4 else 'FAIL'}")
    sys.exit(0 if (r1 and r2 and r3 and r4) else 1)
