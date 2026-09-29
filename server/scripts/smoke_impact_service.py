"""Manual smoke test for Phase 9.2's `app.services.impact_service`.

Needs a real DB (`compute_impact` queries `File`/`DependencyEdge`/
`ApiEndpoint` rows via `analysis_read_service.load_dependency_edges`, unlike
Phase 9.1's pure `analysis/change.py`) — hand-inserts `File`/`DependencyEdge`
/`ApiEndpoint` rows directly rather than running the real Tree-sitter
pipeline, since this sub-phase only needs precise control over the graph
shape, not real parsed source (same "insert directly, don't run the real
pipeline" precedent `smoke_rag_schema.py`/`smoke_file_insight.py` already
established for their own DB-only-shape fixtures).

Risk model (reworked after the first live run - see `docs/PHASE_9_PLAN.md`,
9.5 follow-up): `score = min(100, (file_points + api_points + cycle_bonus) *
multiplier)` where file_points weights each *non-test* dependent by hop
(6/3/1.5/1), api_points = 5*log2(1 + affected APIs), cycle_bonus = 15 only when
a *changed* file is in a real cycle, and multiplier = 1.0 for a "breaking"
change / 0.75 for "non_breaking". Every expected value below is hand-computed
from that, and chosen to avoid exact `.5` totals (Python's `round()` is
banker's rounding).

Four seeded DB scenarios, one `AnalysisRun` each (isolates each scenario's own
graph - no cross-scenario adjacency bleed):

- `low`    - a plain 3-hop chain (`d.py` <- `c.py` <- `b.py` <- `a.py`; the
  changed file is `d.py`), non-breaking: 6 + 3 + 1.5 = 10.5, x0.75 -> 8, LOW.
- `medium` - two direct + two indirect dependents and one API on a direct
  file, breaking: 12 + 6 + 5 = 23 -> MEDIUM.
- `high`   - the changed file `m.py` is itself in a real cycle (`m.py` <->
  `n.py`), with 4 direct + 2 indirect dependents and 3 APIs, breaking:
  30 + 10 + 15 = 55 -> HIGH.
- `tests`  - the changed file is reached by a real dependent, plus one direct
  and one indirect *test* file: the tests stay listed in `direct`/`indirect`
  (transparency) but score nothing, and `breakdown.excluded_test_files == 2`.

A fifth, DB-free group calls the private `_compute_risk` directly to pin the
formula's individual terms (hop decay, log-scaled APIs, the change multiplier,
cycle bonus only for a *changed* circular file, the 100 cap) and
`_is_test_path`.

Run: `uv run python scripts/smoke_impact_service.py` (from `server/`).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.analysis.apis import Framework  # noqa: E402
from app.analysis.dependencies import DependencyEdgeType  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.dependency import DependencyEdge as DependencyEdgeRow  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.services.impact_service import ImpactedFile, _compute_risk, _is_test_path, compute_impact  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

FIXTURE_USER_EMAIL = "smoke-impact-service@local.test"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _seed_run(
    db: Session,
    repository: Repository,
    *,
    commit_sha: str,
    file_paths: list[str],
    edges: list[tuple[str, str]],
    apis: list[tuple[str, str, str]],
) -> AnalysisRun:
    """Creates one `COMPLETED` `AnalysisRun` plus its `File`/`DependencyEdge`
    (all `INTERNAL`)/`ApiEndpoint` rows. `edges` is `(source_path,
    target_path)` pairs; `apis` is `(method, path, file_path)` triples."""
    run = AnalysisRun(repository_id=repository.id, branch="main", commit_sha=commit_sha, status=AnalysisRunStatus.COMPLETED)
    db.add(run)
    db.commit()
    db.refresh(run)

    file_id_by_path: dict[str, uuid.UUID] = {}
    for path in file_paths:
        file_row = File(
            repository_id=repository.id,
            analysis_run_id=run.id,
            path=path,
            language=Language.PYTHON,
            size_bytes=100,
            module=".",
            loc=10,
            last_analyzed_commit_sha=commit_sha,
        )
        db.add(file_row)
        db.flush()
        file_id_by_path[path] = file_row.id

    for source_path, target_path in edges:
        db.add(
            DependencyEdgeRow(
                repository_id=repository.id,
                analysis_run_id=run.id,
                source_file_id=file_id_by_path[source_path],
                target_file_id=file_id_by_path[target_path],
                edge_type=DependencyEdgeType.INTERNAL,
            )
        )

    for method, path, file_path in apis:
        db.add(
            ApiEndpoint(
                repository_id=repository.id,
                analysis_run_id=run.id,
                method=method,
                path=path,
                file_id=file_id_by_path[file_path],
                module=".",
                framework=Framework.FASTAPI,
            )
        )

    db.commit()
    return run


def run_low_risk(db: Session, repository: Repository) -> bool:
    print("=== low: a plain 3-hop chain, no APIs, no coupling/circular signal ===")
    all_passed = True
    run = _seed_run(
        db,
        repository,
        commit_sha="1111111111111111111111111111111111111a",
        file_paths=["a.py", "b.py", "c.py", "d.py"],
        edges=[("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "d.py")],
        apis=[],
    )
    result = compute_impact(
        db, head_analysis_run_id=run.id, changed_file_paths={"d.py"}, change_kind="non_breaking"
    )

    all_passed &= _check(
        "direct == [c.py @ hops=1]",
        [(f.path, f.hops) for f in result.direct] == [("c.py", 1)],
        str(result.direct),
    )
    all_passed &= _check(
        "indirect == [b.py @ hops=2, a.py @ hops=3]",
        [(f.path, f.hops) for f in result.indirect] == [("b.py", 2), ("a.py", 3)],
        str(result.indirect),
    )
    all_passed &= _check("no affected APIs", result.affected_apis == [], str(result.affected_apis))
    all_passed &= _check(
        "why names the direct parent",
        result.direct[0].why == "imports `d.py`",
        result.direct[0].why,
    )
    all_passed &= _check(
        "score == 8 ((1 direct * 6 + 1 hop-2 * 3 + 1 hop-3 * 1.5) * 0.75 = 7.875)",
        result.score == 8,
        str(result.score),
    )
    all_passed &= _check(
        "breakdown == 10.5 file pts / 0 api / 0 cycle / non_breaking x0.75 / 0 tests excluded",
        (
            result.breakdown.file_points,
            result.breakdown.api_points,
            result.breakdown.cycle_bonus,
            result.breakdown.change_kind,
            result.breakdown.multiplier,
            result.breakdown.excluded_test_files,
        )
        == (10.5, 0.0, 0.0, "non_breaking", 0.75, 0),
        str(result.breakdown),
    )
    all_passed &= _check("risk == LOW", result.risk == "LOW", result.risk)
    return all_passed


def run_medium_risk(db: Session, repository: Repository) -> bool:
    print("\n=== medium: 2 direct + 2 indirect, one API on a direct file ===")
    all_passed = True
    run = _seed_run(
        db,
        repository,
        commit_sha="2222222222222222222222222222222222222b",
        file_paths=["y.py", "p.py", "q.py", "r.py", "s.py"],
        edges=[("p.py", "y.py"), ("q.py", "y.py"), ("r.py", "p.py"), ("s.py", "q.py")],
        apis=[("GET", "/p", "p.py")],
    )
    result = compute_impact(db, head_analysis_run_id=run.id, changed_file_paths={"y.py"}, change_kind="breaking")

    all_passed &= _check(
        "direct == [p.py, q.py] @ hops=1",
        sorted((f.path, f.hops) for f in result.direct) == [("p.py", 1), ("q.py", 1)],
        str(result.direct),
    )
    all_passed &= _check(
        "indirect == [r.py, s.py] @ hops=2",
        sorted((f.path, f.hops) for f in result.indirect) == [("r.py", 2), ("s.py", 2)],
        str(result.indirect),
    )
    all_passed &= _check(
        "affected_apis == ['GET /p']",
        result.affected_apis == ["GET /p"],
        str(result.affected_apis),
    )
    all_passed &= _check(
        "score == 23 (2 direct * 6 + 2 hop-2 * 3 + 5*log2(1+1 api) = 12 + 6 + 5)",
        result.score == 23,
        str(result.score),
    )
    all_passed &= _check("risk == MEDIUM", result.risk == "MEDIUM", result.risk)
    return all_passed


def run_high_risk(db: Session, repository: Repository) -> bool:
    print("\n=== high: changed file is itself in a real cycle, 4 direct + 2 indirect dependents, 3 APIs ===")
    all_passed = True
    run = _seed_run(
        db,
        repository,
        commit_sha="3333333333333333333333333333333333333c",
        file_paths=["m.py", "n.py", "x1.py", "x2.py", "x3.py", "y1.py", "y2.py"],
        # m.py <-> n.py is a real 2-file cycle; x1..x3 also import m.py directly; y1/y2 import x1/x2.
        edges=[
            ("m.py", "n.py"), ("n.py", "m.py"),
            ("x1.py", "m.py"), ("x2.py", "m.py"), ("x3.py", "m.py"),
            ("y1.py", "x1.py"), ("y2.py", "x2.py"),
        ],
        apis=[("GET", "/n", "n.py"), ("GET", "/x1", "x1.py"), ("POST", "/y1", "y1.py")],
    )
    result = compute_impact(db, head_analysis_run_id=run.id, changed_file_paths={"m.py"}, change_kind="breaking")

    all_passed &= _check(
        "direct == [n, x1, x2, x3] @ hops=1",
        [(f.path, f.hops) for f in result.direct] == [("n.py", 1), ("x1.py", 1), ("x2.py", 1), ("x3.py", 1)],
        str(result.direct),
    )
    all_passed &= _check(
        "indirect == [y1, y2] @ hops=2",
        [(f.path, f.hops) for f in result.indirect] == [("y1.py", 2), ("y2.py", 2)],
        str(result.indirect),
    )
    all_passed &= _check(
        "affected_apis == ['GET /n', 'GET /x1', 'POST /y1']",
        result.affected_apis == ["GET /n", "GET /x1", "POST /y1"],
        str(result.affected_apis),
    )
    all_passed &= _check(
        "score == 55 (4 direct * 6 + 2 hop-2 * 3 = 30, + 5*log2(1+3 apis) = 10, + 15 cycle bonus)",
        result.score == 55,
        str(result.score),
    )
    all_passed &= _check(
        "breakdown.cycle_bonus == 15.0 (the *changed* file m.py is circular)",
        result.breakdown.cycle_bonus == 15.0,
        str(result.breakdown),
    )
    all_passed &= _check("risk == HIGH", result.risk == "HIGH", result.risk)
    return all_passed


def run_test_files_excluded(db: Session, repository: Repository) -> bool:
    print("\n=== tests: reached test files are listed but never scored ===")
    all_passed = True
    run = _seed_run(
        db,
        repository,
        commit_sha="4444444444444444444444444444444444444d",
        file_paths=["core.py", "svc.py", "tests/test_core.py", "tests/test_svc.py"],
        # svc and tests/test_core import core; tests/test_svc imports svc (hop 2).
        edges=[("svc.py", "core.py"), ("tests/test_core.py", "core.py"), ("tests/test_svc.py", "svc.py")],
        apis=[("GET", "/svc", "svc.py")],
    )
    result = compute_impact(db, head_analysis_run_id=run.id, changed_file_paths={"core.py"}, change_kind="breaking")

    all_passed &= _check(
        "both test files are still listed (transparency)",
        sorted(f.path for f in result.direct + result.indirect)
        == ["svc.py", "tests/test_core.py", "tests/test_svc.py"],
        str(result.direct + result.indirect),
    )
    all_passed &= _check("breakdown.excluded_test_files == 2", result.breakdown.excluded_test_files == 2, str(result.breakdown))
    all_passed &= _check(
        "file_points == 6.0 (only svc.py scores; tests add 0)", result.breakdown.file_points == 6.0, str(result.breakdown)
    )
    all_passed &= _check(
        "score == 11 (6 file pts + 5*log2(1+1 api) = 5)", result.score == 11 and result.risk == "LOW", f"{result.score} {result.risk}"
    )
    return all_passed


def run_formula_terms_offline() -> bool:
    print("\n=== offline: _compute_risk's individual terms + _is_test_path ===")
    all_passed = True

    def files(*hops: int) -> list[ImpactedFile]:
        return [ImpactedFile(path=f"src/f{i}.py", hops=h, why="imports `x`") for i, h in enumerate(hops)]

    def risk(direct_hops, indirect_hops, n_apis, *, changed=("m.py",), circular=(), kind="breaking"):
        direct = files(*direct_hops)
        indirect = [ImpactedFile(path=f"src/g{i}.py", hops=h, why="imports `x`") for i, h in enumerate(indirect_hops)]
        return _compute_risk(
            direct, indirect, [f"GET /{i}" for i in range(n_apis)],
            changed_file_paths=set(changed), circular_files=frozenset(circular), change_kind=kind,
        )

    # hop decay: hops 1, 2, 4, 5 -> 6 + 3 + 1 + 1 = 11
    r, sc, b = risk([1], [2, 4, 5], 0)
    all_passed &= _check("hop decay: hops 1,2,4,5 -> 11 file pts, score 11, LOW", b.file_points == 11.0 and sc == 11 and r == "LOW", f"{b} {sc} {r}")

    # log-scaled APIs: 0 -> 0, 1 -> 5, 3 -> 10, 7 -> 15, 11 -> 17.9
    pts = [risk([], [], n)[2].api_points for n in (0, 1, 3, 7, 11)]
    all_passed &= _check("api points: 0/1/3/7/11 APIs -> 0/5/10/15/17.9", pts == [0.0, 5.0, 10.0, 15.0, 17.9], str(pts))

    # 53 APIs alone (no files) stays MEDIUM, can never saturate to HIGH by itself
    r, sc, _ = risk([], [], 53)
    all_passed &= _check("53 APIs alone -> 29 / MEDIUM (log scale bounds the API term)", sc == 29 and r == "MEDIUM", f"{sc} {r}")

    # multiplier: 4 direct (24) + 7 APIs (15) = 39
    rb, sb, bb = risk([1, 1, 1, 1], [], 7, kind="breaking")
    rn, sn, bn = risk([1, 1, 1, 1], [], 7, kind="non_breaking")
    all_passed &= _check("breaking x1.0 -> 39 MEDIUM", (sb, rb, bb.multiplier) == (39, "MEDIUM", 1.0), f"{sb} {rb}")
    all_passed &= _check("non_breaking x0.75 -> 29 MEDIUM (39 * 0.75 = 29.25)", (sn, rn, bn.multiplier) == (29, "MEDIUM", 0.75), f"{sn} {rn}")

    # cycle bonus only when a *changed* file is circular, not merely a dependent
    _, _, b = risk([1], [], 0, changed=("m.py",), circular=("src/f0.py",))
    all_passed &= _check("circular *dependent* only -> no bonus", b.cycle_bonus == 0.0, str(b))
    _, sc, b = risk([1], [], 0, changed=("m.py",), circular=("m.py",))
    all_passed &= _check("circular *changed* file -> +15 bonus", b.cycle_bonus == 15.0 and sc == 21, f"{b} {sc}")

    # cap
    r, sc, _ = risk([1] * 30, [], 0)
    all_passed &= _check("score is capped at 100 -> HIGH", sc == 100 and r == "HIGH", f"{sc} {r}")

    # test-path heuristic
    cases = {
        "server/__tests__/unit/auth.service.test.ts": True,
        "client/src/App.spec.tsx": True,
        "pkg/test_core.py": True,
        "pkg/core_test.py": True,
        "src/test/java/com/x/A.java": True,
        "src/main/java/com/x/FooTest.java": True,
        "tests/conftest.py": True,
        "server/src/services/authService.ts": False,
        "src/main/java/com/x/Latest.java": False,
        "client/src/utils/attest.ts": False,
    }
    bad = {p: (_is_test_path(p), want) for p, want in cases.items() if _is_test_path(p) != want}
    all_passed &= _check("_is_test_path classifies 10 representative paths correctly", not bad, str(bad))
    return all_passed


if __name__ == "__main__":
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, FIXTURE_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-impact-service", owner="smoke", name="impact-service-fixture"
        )
        _clear_previous_runs(db, repository)

        r1 = run_low_risk(db, repository)
        r2 = run_medium_risk(db, repository)
        r3 = run_high_risk(db, repository)
        r5 = run_test_files_excluded(db, repository)
    finally:
        db.close()

    r4 = run_formula_terms_offline()

    print("\n=== Summary ===")
    print(f"low:                    {'PASS' if r1 else 'FAIL'}")
    print(f"medium:                 {'PASS' if r2 else 'FAIL'}")
    print(f"high:                   {'PASS' if r3 else 'FAIL'}")
    print(f"tests_excluded:         {'PASS' if r5 else 'FAIL'}")
    print(f"formula_terms_offline:  {'PASS' if r4 else 'FAIL'}")
    sys.exit(0 if (r1 and r2 and r3 and r4 and r5) else 1)
