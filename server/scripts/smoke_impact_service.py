"""Manual smoke test for Phase 9.2's `app.services.impact_service`.

Needs a real DB (`compute_impact` queries `File`/`DependencyEdge`/
`ApiEndpoint` rows via `analysis_read_service.load_dependency_edges`, unlike
Phase 9.1's pure `analysis/change.py`) — hand-inserts `File`/`DependencyEdge`
/`ApiEndpoint` rows directly rather than running the real Tree-sitter
pipeline, since this sub-phase only needs precise control over the graph
shape, not real parsed source (same "insert directly, don't run the real
pipeline" precedent `smoke_rag_schema.py`/`smoke_file_insight.py` already
established for their own DB-only-shape fixtures).

Three seeded scenarios, one `AnalysisRun` each (isolates each scenario's own
graph — no cross-scenario adjacency bleed):

- `low`    — a plain 3-hop dependency chain (`d.py` <- `c.py` <- `b.py` <-
  `a.py`; the changed file is `d.py`). No API endpoints, no coupling/
  circular signal. Exercises direct (hops=1) vs. indirect (hops=2,3)
  classification and confirms a small, unremarkable change scores LOW.
- `medium` — two direct dependents + two indirect ones on a changed file,
  with one API endpoint on a direct dependent. No coupling/circular signal.
  Exercises affected-API resolution and lands the score in the MEDIUM band.
- `high`   — the changed file `m.py` is itself part of a real 2-file cycle
  (`m.py` <-> `n.py`), so its one direct dependent (`n.py`) is also a
  circular file; one indirect dependent (`o.py`); two API endpoints (on
  `n.py` and `o.py`). Exercises the circular-file risk bonus and lands the
  score in the HIGH band.

A fourth, DB-free group calls the private `_compute_risk` directly with a
synthetic high-*coupling* (not circular) input, since the three DB
scenarios above don't naturally produce a high per-module coupling score
(all their files share one top-level module, and `analysis/graph.py`'s own
coupling rule excludes same-module edges) — the only way to exercise that
half of the risk bonus without an artificially module-split DB fixture.

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
from app.analysis.graph import CouplingScore  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.dependency import DependencyEdge as DependencyEdgeRow  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.services.impact_service import _compute_risk, compute_impact  # noqa: E402
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
    result = compute_impact(db, head_analysis_run_id=run.id, changed_file_paths={"d.py"})

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
        "score == 14 (1 direct * 8 + 2 indirect * 3)",
        result.score == 14,
        str(result.score),
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
    result = compute_impact(db, head_analysis_run_id=run.id, changed_file_paths={"y.py"})

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
        "score == 32 (2 direct * 8 + 2 indirect * 3 + 1 api * 10)",
        result.score == 32,
        str(result.score),
    )
    all_passed &= _check("risk == MEDIUM", result.risk == "MEDIUM", result.risk)
    return all_passed


def run_high_risk(db: Session, repository: Repository) -> bool:
    print("\n=== high: changed file is itself in a real cycle, plus 2 APIs ===")
    all_passed = True
    run = _seed_run(
        db,
        repository,
        commit_sha="3333333333333333333333333333333333333c",
        file_paths=["m.py", "n.py", "o.py"],
        # m.py <-> n.py is a real 2-file cycle; o.py depends on n.py one hop further out.
        edges=[("m.py", "n.py"), ("n.py", "m.py"), ("o.py", "n.py")],
        apis=[("GET", "/n", "n.py"), ("POST", "/o", "o.py")],
    )
    result = compute_impact(db, head_analysis_run_id=run.id, changed_file_paths={"m.py"})

    all_passed &= _check(
        "direct == [n.py @ hops=1]",
        [(f.path, f.hops) for f in result.direct] == [("n.py", 1)],
        str(result.direct),
    )
    all_passed &= _check(
        "indirect == [o.py @ hops=2]",
        [(f.path, f.hops) for f in result.indirect] == [("o.py", 2)],
        str(result.indirect),
    )
    all_passed &= _check(
        "affected_apis == ['GET /n', 'POST /o']",
        result.affected_apis == ["GET /n", "POST /o"],
        str(result.affected_apis),
    )
    all_passed &= _check(
        "score == 56 (1 direct * 8 + 1 indirect * 3 + 2 apis * 10 + 25 circular bonus)",
        result.score == 56,
        str(result.score),
    )
    all_passed &= _check("risk == HIGH", result.risk == "HIGH", result.risk)
    return all_passed


def run_high_coupling_bonus_offline() -> bool:
    print("\n=== offline: _compute_risk's high-*coupling* bonus (not circular) ===")
    from app.services.impact_service import ImpactedFile

    direct = [ImpactedFile(path="svc/a.py", hops=1, why="imports `svc/changed.py`")]
    indirect: list[ImpactedFile] = []
    affected_apis: list[str] = []
    coupling = [CouplingScore(module="svc", fan_in=5, fan_out=8, score=13)]  # >= COUPLING_SCORE_THRESHOLDS[0]==10
    circular_files: frozenset[str] = frozenset()

    risk, score = _compute_risk(direct, indirect, affected_apis, coupling, circular_files)
    return _check(
        "1 direct file (8) + high-coupling bonus (25) == 33 -> MEDIUM",
        score == 33 and risk == "MEDIUM",
        f"score={score} risk={risk}",
    )


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
    finally:
        db.close()

    r4 = run_high_coupling_bonus_offline()

    print("\n=== Summary ===")
    print(f"low:                    {'PASS' if r1 else 'FAIL'}")
    print(f"medium:                 {'PASS' if r2 else 'FAIL'}")
    print(f"high:                   {'PASS' if r3 else 'FAIL'}")
    print(f"high_coupling_offline:  {'PASS' if r4 else 'FAIL'}")
    sys.exit(0 if (r1 and r2 and r3 and r4) else 1)
