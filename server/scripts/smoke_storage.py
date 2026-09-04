"""Manual smoke test for Phase 4.4's `persist_analysis_run`
(`app/services/repo_service.py`) — the first script that actually touches
the real dev database.

Per the plan-mode discussion that shaped this test (see
`docs/BACKEND_IMPLEMENTATION_PLAN.md`'s Phase 4.4 section): **no new
algorithmic fixtures are invented here.** Every exact, hand-verifiable
expectation below reuses values already hand-verified by Phase 4.2's
`smoke_graph.py` (the `dependency_cycle/{cyclic,acyclic,self_loop}`
fixture trees) or Phase 4.3's `smoke_debt.py` (its `build_dirty_fixture`/
`build_clean_fixture`, extracted there specifically so this script could
import them). The real `server/app`+`client/src` dogfood run is kept as a
*secondary* integration check (no hand-derived expectations exist for its
real shape, so it's cross-checked against an independently recomputed
`analyze_dependency_graph`/`analyze_technical_debt` call instead) — this
mirrors `smoke_analysis.py`'s own small-fixture-primary / real-repo-
secondary structure, which this phase's verification had drifted from in
an earlier draft.

Five groups:

1. `run_dependency_cycle_persistence()` — `cyclic`/`acyclic`/`self_loop`
   run through the real pipeline (`analyze_ingested`) and persisted: exact
   expected `DependencyEdge.is_circular` flags (by source/target file
   path), exact expected `CIRCULAR_DEPENDENCY` `DebtIssue` count and
   `AnalysisRun.debt_score`, plus persisted-semantics assertions (status/
   timestamps, Repository fields, child-row `repository_id` ownership,
   File/Symbol row counts against the in-memory result).
2. `run_debt_fixture_persistence()` — `smoke_debt.py`'s dirty/clean
   fixtures, reassembled into a synthetic `AnalysisResult` (with
   `dependency_edges=[]`, since these fixtures' `CIRCULAR_DEPENDENCY`/
   `EXCESSIVE_COUPLING` inputs are hand-fabricated `GraphAnalysis` objects
   that don't correspond to any real edge list — `persist_analysis_run`
   always derives its own `GraphAnalysis` from real edges, so those two
   rules aren't exercised by this scenario; they're covered by group 1
   instead, which derives `GraphAnalysis` from real edges). Exercises the
   other five rules' *exact* persisted type/severity/metric_value/file_id,
   and `AnalysisRun.debt_score` for the resulting 5-issue subset (17.0, not
   `smoke_debt.py`'s own 24.0 — recomputed by hand for this deliberately
   narrower reuse, see that function's own comment).
3. `run_transaction_atomicity()` — forces a NOT-NULL violation partway
   through persistence (a `Symbol` with `name=None`) and asserts: the
   exception propagates out of `persist_analysis_run`, nothing at all
   commits (no new `AnalysisRun` row for the attempt), and no `FAILED`
   `AnalysisRun` row is ever written (that's Phase 5's job). Also checks
   the `repository_id`-not-found pre-flight `ValueError`.
4. `run_real_repo_dogfood()` (secondary) — this repo's own `server/app` +
   `client/src`, same ingestion `smoke_analysis.py` already builds;
   cross-checked against independently recomputed graph/debt analyses;
   also the only scenario with real API routes, so it's where
   `ApiEndpoint.handler_symbol_id` resolution and `auth_required == False`
   are actually checked.
5. `run_live_bonus()` (non-gating) — same `Nirmit1705/SubTrack` technique
   as `smoke_analysis.py::run_live_analyze_commit`, now also persisted;
   skips cleanly if no installation is found.

Run: `uv run python scripts/smoke_storage.py` (from `server/`).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.analysis import pipeline  # noqa: E402
from app.analysis.debt import DebtIssueType, DebtSeverity, analyze_technical_debt  # noqa: E402
from app.analysis.graph import analyze_dependency_graph  # noqa: E402
from app.analysis.ingestion import IngestedFile, IngestionResult  # noqa: E402
from app.analysis.metrics import FileMetrics  # noqa: E402
from app.analysis.pipeline import AnalysisResult, analyze_ingested  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.debt_issue import DebtIssue  # noqa: E402
from app.models.dependency import DependencyEdge as DependencyEdgeRow  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository, RepositoryAnalysisStatus  # noqa: E402
from app.models.symbol import Symbol as SymbolRow  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import github_service  # noqa: E402
from app.services.repo_service import persist_analysis_run  # noqa: E402
from smoke_debt import DebtFixture, build_clean_fixture, build_dirty_fixture, make_symbol  # noqa: E402

SERVER_ROOT = Path(__file__).resolve().parent.parent  # server/
REPO_ROOT = SERVER_ROOT.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "dependency_cycle"

DOGFOOD_USER_EMAIL = "smoke-storage@local.test"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


# --- seeding / cleanup helpers --------------------------------------------------


def _get_or_create_user(db: Session, email: str) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _get_or_create_repository(db: Session, user: User, *, github_repo_id: str, owner: str, name: str) -> Repository:
    repository = db.execute(
        select(Repository).where(
            Repository.connected_user_id == user.id, Repository.github_repo_id == github_repo_id
        )
    ).scalar_one_or_none()
    if repository is None:
        repository = Repository(
            owner=owner,
            name=name,
            full_name=f"{owner}/{name}",
            github_repo_id=github_repo_id,
            default_branch="main",
            connected_user_id=user.id,
            installation_id="0",
        )
        db.add(repository)
        db.commit()
        db.refresh(repository)
    return repository


def _clear_previous_runs(db: Session, repository: Repository) -> None:
    """Deletes any `AnalysisRun` rows left over from a previous run of this
    script for `repository` — cascades to File/Symbol/DependencyEdge/
    ApiEndpoint/DebtIssue at the DB level (`ondelete=CASCADE`), so reruns
    of this script don't accumulate stale data."""
    db.execute(delete(AnalysisRun).where(AnalysisRun.repository_id == repository.id))
    db.commit()


def _load_ingested_files(root: Path, prefix: str) -> list[IngestedFile]:
    return [
        IngestedFile(path=f"{prefix}/{p.relative_to(root).as_posix()}", absolute_path=p, size_bytes=p.stat().st_size)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]


def _walk_real_repo(base: Path, rel_prefix: str, extensions: tuple[str, ...]) -> list[IngestedFile]:
    """Same technique as `smoke_analysis.py::_walk_real` — duplicated
    rather than imported (it's a few lines of boilerplate, not an
    algorithm worth sharing) so this script's only cross-script import is
    `smoke_debt`'s public fixture builders."""
    files: list[IngestedFile] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in extensions:
            continue
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(base)).replace("\\", "/")
        files.append(IngestedFile(path=f"{rel_prefix}/{rel}", absolute_path=path, size_bytes=path.stat().st_size))
    return files


def _real_commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True
        )
        sha = result.stdout.strip()
        if sha:
            return sha[:40]
    except Exception:  # noqa: BLE001 — best-effort only, a fixed marker is a fine fallback
        pass
    return "dogfood0000000000000000000000000000000"[:40]


# --- group 1: dependency_cycle fixtures (Phase 4.2 reuse) -----------------------


def run_dependency_cycle_persistence(db: Session, repository: Repository) -> bool:
    print("=== group 1: dependency_cycle/{cyclic,acyclic,self_loop} (4.2) -> persisted is_circular + CIRCULAR_DEPENDENCY debt issues ===")
    all_passed = True

    # (label, expected is_circular (source_path, target_path) pairs, expected
    # CIRCULAR_DEPENDENCY debt-issue count, expected AnalysisRun.debt_score)
    # — all values already hand-verified by smoke_graph.py itself; the debt
    # counts/scores are newly derived here (smoke_graph.py never ran these
    # through debt.py), by hand against analysis/debt.py's own published
    # thresholds (CIRCULAR_DEPENDENCY_SIZE_THRESHOLDS medium floor = 1 file).
    scenarios = [
        (
            "cyclic",
            {("cyclic/module_a/a.py", "cyclic/module_b/b.py"), ("cyclic/module_b/b.py", "cyclic/module_a/a.py")},
            1,
            2.0,  # one MEDIUM CIRCULAR_DEPENDENCY issue (cycle size 2 -> MEDIUM tier), weight 2.0
        ),
        ("acyclic", set(), 0, 0.0),
        (
            "self_loop",
            {("self_loop/module_s/s.py", "self_loop/module_s/s.py")},
            1,
            2.0,  # cycle size 1 is still >= the MEDIUM floor (1) -> same MEDIUM weight
        ),
    ]

    for label, expected_circular_pairs, expected_circular_debt_count, expected_debt_score in scenarios:
        root = FIXTURES_DIR / label
        files = _load_ingested_files(root, label)
        ingestion_result = IngestionResult(root_dir=root, files=files, ignored_paths=[])
        commit_sha = label.ljust(40, "0")[:40]
        result = analyze_ingested(ingestion_result, commit_sha=commit_sha)

        _clear_previous_runs(db, repository)
        run = persist_analysis_run(db, repository_id=repository.id, branch="main", analysis_result=result)

        file_rows = db.execute(select(File).where(File.analysis_run_id == run.id)).scalars().all()
        symbol_rows = db.execute(select(SymbolRow).where(SymbolRow.analysis_run_id == run.id)).scalars().all()
        edge_rows = db.execute(select(DependencyEdgeRow).where(DependencyEdgeRow.analysis_run_id == run.id)).scalars().all()
        debt_rows = db.execute(select(DebtIssue).where(DebtIssue.analysis_run_id == run.id)).scalars().all()

        all_passed &= _check(
            f"{label}: File row count matches in-memory result", len(file_rows) == len(result.file_metrics),
            f"{len(file_rows)} vs {len(result.file_metrics)}",
        )
        all_passed &= _check(
            f"{label}: Symbol row count matches in-memory result", len(symbol_rows) == len(result.symbols),
            f"{len(symbol_rows)} vs {len(result.symbols)}",
        )
        all_passed &= _check(
            f"{label}: DependencyEdge row count matches in-memory result", len(edge_rows) == len(result.dependency_edges),
            f"{len(edge_rows)} vs {len(result.dependency_edges)}",
        )

        file_path_by_id = {f.id: f.path for f in file_rows}
        actual_circular_pairs = {
            (file_path_by_id.get(e.source_file_id), file_path_by_id.get(e.target_file_id))
            for e in edge_rows
            if e.is_circular
        }
        all_passed &= _check(
            f"{label}: exactly the expected edge(s) flagged is_circular", actual_circular_pairs == expected_circular_pairs,
            str(actual_circular_pairs),
        )

        circular_debt_issues = [d for d in debt_rows if d.issue_type is DebtIssueType.CIRCULAR_DEPENDENCY]
        all_passed &= _check(
            f"{label}: {expected_circular_debt_count} CIRCULAR_DEPENDENCY debt issue(s)",
            len(circular_debt_issues) == expected_circular_debt_count, str(circular_debt_issues),
        )
        all_passed &= _check(
            f"{label}: no other debt-issue types leaked in (these fixtures don't trip any other rule)",
            len(debt_rows) == expected_circular_debt_count, str(debt_rows),
        )
        all_passed &= _check(
            f"{label}: AnalysisRun.debt_score == {expected_debt_score}", abs(run.debt_score - expected_debt_score) < 1e-9,
            str(run.debt_score),
        )

        all_passed &= _check(f"{label}: AnalysisRun.status == COMPLETED", run.status is AnalysisRunStatus.COMPLETED)
        all_passed &= _check(f"{label}: AnalysisRun.completed_at is set", run.completed_at is not None)
        all_passed &= _check(
            f"{label}: Repository.last_analyzed_commit_sha updated", repository.last_analyzed_commit_sha == commit_sha
        )
        all_passed &= _check(
            f"{label}: Repository.analysis_status == COMPLETED", repository.analysis_status is RepositoryAnalysisStatus.COMPLETED
        )

        all_passed &= _check(
            f"{label}: every File row's repository_id matches", all(f.repository_id == repository.id for f in file_rows)
        )
        all_passed &= _check(
            f"{label}: every Symbol row's repository_id matches", all(s.repository_id == repository.id for s in symbol_rows)
        )
        all_passed &= _check(
            f"{label}: every DependencyEdge row's repository_id matches", all(e.repository_id == repository.id for e in edge_rows)
        )

    return all_passed


# --- group 2: smoke_debt.py's dirty/clean fixtures (Phase 4.3 reuse) ------------


def _synthetic_analysis_result(fixture: DebtFixture, *, commit_sha: str) -> AnalysisResult:
    """Wraps one of `smoke_debt.py`'s `DebtFixture`s into a full
    `AnalysisResult` `persist_analysis_run` can consume — `dependency_edges`
    is deliberately `[]` (see this module's own docstring on why the
    graph-dependent rules aren't exercised here).

    `smoke_debt.py`'s fixtures were built to test `analyze_technical_debt`
    in isolation, where `file_metrics`/`symbols`/`function_complexity`/
    `external_dependencies` have no cross-consistency requirement — e.g.
    `build_dirty_fixture()`'s complexity-rule symbol lives in a file
    (`app/complex_module/big_function.py`) that never appears in its own
    `file_metrics` list at all, since `debt.py`'s complexity rule doesn't
    need it to. A real pipeline run has no such gap (`analyze_ingested`
    always builds one `FileMetrics` per ingested file, and every `Symbol`
    only ever comes from an ingested file) — so `persist_analysis_run`
    assumes it too, and simply can't resolve a `File` row for a path
    `file_metrics` never mentioned (see its own `file_id_by_path.get(...)`
    fallback-to-`None` comments). This helper closes that gap for every
    fixture-only path this scenario needs resolved, by adding one
    synthetic `FileMetrics`/`IngestedFile` per file any symbol, function
    complexity entry, or external-dependency manifest references but
    `fixture.file_metrics` doesn't already cover."""
    file_metrics = list(fixture.file_metrics)
    known_paths = {fm.file_path for fm in file_metrics}

    def _ensure_file(path: str, module: str) -> None:
        if path not in known_paths:
            file_metrics.append(FileMetrics(file_path=path, module=module, language=None, total_lines=5, loc=5))
            known_paths.add(path)

    for symbol in fixture.symbols:
        _ensure_file(symbol.file_path, symbol.module)
    for fc in fixture.function_complexity:
        _ensure_file(fc.symbol.file_path, fc.symbol.module)
    for dep in fixture.external_dependencies:
        _ensure_file(dep.manifest_path, str(PurePosixPath(dep.manifest_path).parent))

    files = [IngestedFile(path=fm.file_path, absolute_path=Path(fm.file_path), size_bytes=100) for fm in file_metrics]
    return AnalysisResult(
        commit_sha=commit_sha,
        files=files,
        ignored_paths=[],
        language_counts={},
        symbols=fixture.symbols,
        api_routes=[],
        dependency_edges=[],
        external_dependencies=fixture.external_dependencies,
        file_metrics=file_metrics,
        function_complexity=fixture.function_complexity,
        module_metrics=fixture.module_metrics,
    )


def run_debt_fixture_persistence(db: Session, repository: Repository) -> bool:
    print("\n=== group 2: smoke_debt.py's dirty/clean fixtures (4.3) -> persisted DebtIssue rows ===")
    all_passed = True

    dirty_fixture = build_dirty_fixture()
    dirty_result = _synthetic_analysis_result(dirty_fixture, commit_sha="debtdirty0000000000000000000000000000"[:40])

    _clear_previous_runs(db, repository)
    dirty_run = persist_analysis_run(db, repository_id=repository.id, branch="main", analysis_result=dirty_result)

    dirty_issues = db.execute(select(DebtIssue).where(DebtIssue.analysis_run_id == dirty_run.id)).scalars().all()
    by_type = {issue.issue_type: issue for issue in dirty_issues}

    all_passed &= _check(
        "exactly 5 graph-independent debt issues persisted (CIRCULAR_DEPENDENCY/"
        "EXCESSIVE_COUPLING excluded here — see group 1 for those)",
        len(dirty_issues) == 5, str(dirty_issues),
    )

    # (issue_type, expected severity, expected metric_value, expected linked File.path or None for module-scoped)
    expectations = [
        (DebtIssueType.HIGH_COMPLEXITY, DebtSeverity.CRITICAL, 35.0, "app/complex_module/big_function.py"),
        (DebtIssueType.LARGE_FILE, DebtSeverity.MEDIUM, 350.0, "app/large_file_module/giant.py"),
        (DebtIssueType.LARGE_MODULE, DebtSeverity.HIGH, 4000.0, None),
        (DebtIssueType.MISSING_DOCUMENTATION, DebtSeverity.MEDIUM, 0.6, None),
        (DebtIssueType.OUTDATED_DEPENDENCY, DebtSeverity.CRITICAL, 5.0, "package.json"),
    ]
    for issue_type, expected_severity, expected_metric, expected_file_path in expectations:
        issue = by_type.get(issue_type)
        ok = issue is not None and issue.severity is expected_severity and abs(issue.metric_value - expected_metric) < 1e-9
        all_passed &= _check(
            f"{issue_type.value}: severity={expected_severity.value}, metric_value={expected_metric}", ok, str(issue)
        )
        if issue is None:
            continue
        if expected_file_path is None:
            all_passed &= _check(f"{issue_type.value} has no single file target (module-scoped)", issue.file_id is None)
        else:
            expected_file_id = db.execute(
                select(File.id).where(File.analysis_run_id == dirty_run.id, File.path == expected_file_path)
            ).scalar_one_or_none()
            all_passed &= _check(
                f"{issue_type.value} linked to the right File row ({expected_file_path})",
                issue.file_id == expected_file_id and expected_file_id is not None,
                f"{issue.file_id} vs {expected_file_id}",
            )

    # CRITICAL(5.0) + MEDIUM(2.0) + HIGH(3.0) + MEDIUM(2.0) + CRITICAL(5.0) — the
    # 5-issue subset actually persisted here, not smoke_debt.py's own 24.0
    # (which includes the 2 graph-dependent issues this scenario excludes).
    expected_dirty_score = 5.0 + 2.0 + 3.0 + 2.0 + 5.0
    all_passed &= _check(
        f"AnalysisRun.debt_score == {expected_dirty_score}", abs(dirty_run.debt_score - expected_dirty_score) < 1e-9,
        str(dirty_run.debt_score),
    )

    clean_fixture = build_clean_fixture()
    clean_result = _synthetic_analysis_result(clean_fixture, commit_sha="debtclean0000000000000000000000000000"[:40])
    _clear_previous_runs(db, repository)
    clean_run = persist_analysis_run(db, repository_id=repository.id, branch="main", analysis_result=clean_result)
    clean_issue_count = db.execute(
        select(func.count()).select_from(DebtIssue).where(DebtIssue.analysis_run_id == clean_run.id)
    ).scalar_one()
    all_passed &= _check("clean fixture persists zero DebtIssue rows", clean_issue_count == 0, str(clean_issue_count))
    all_passed &= _check("clean fixture: AnalysisRun.debt_score == 0.0", clean_run.debt_score == 0.0, str(clean_run.debt_score))

    return all_passed


# --- group 3: transaction atomicity ---------------------------------------------


def run_transaction_atomicity(db: Session, repository: Repository) -> bool:
    print("\n=== group 3: transaction atomicity — a forced failure must roll back completely ===")
    all_passed = True

    import uuid as uuid_module

    all_passed &= _check(
        "unknown repository_id raises ValueError before touching the DB",
        _raises_value_error(lambda: persist_analysis_run(
            db, repository_id=uuid_module.uuid4(), branch="main",
            analysis_result=_synthetic_analysis_result(build_clean_fixture(), commit_sha="x" * 40),
        )),
    )

    good_symbol = make_symbol("ok_fn", "broken/mod/file.py", "broken/mod", has_docstring=True)
    bad_symbol = make_symbol(None, "broken/mod/file.py", "broken/mod", has_docstring=True)  # violates Symbol.name NOT NULL
    analysis_result = AnalysisResult(
        commit_sha="atomicity000000000000000000000000000"[:40],
        files=[IngestedFile(path="broken/mod/file.py", absolute_path=Path("broken/mod/file.py"), size_bytes=10)],
        ignored_paths=[],
        language_counts={},
        symbols=[good_symbol, bad_symbol],
        api_routes=[],
        dependency_edges=[],
        external_dependencies=[],
        file_metrics=[
            FileMetrics(file_path="broken/mod/file.py", module="broken/mod", language=None, total_lines=5, loc=5)
        ],
        function_complexity=[],
        module_metrics=[],
    )

    _clear_previous_runs(db, repository)
    before_count = db.execute(
        select(func.count()).select_from(AnalysisRun).where(AnalysisRun.repository_id == repository.id)
    ).scalar_one()

    raised = _raises_value_error(
        lambda: persist_analysis_run(db, repository_id=repository.id, branch="main", analysis_result=analysis_result),
        any_exception=True,
    )
    all_passed &= _check("exception propagated out of persist_analysis_run", raised)

    after_count = db.execute(
        select(func.count()).select_from(AnalysisRun).where(AnalysisRun.repository_id == repository.id)
    ).scalar_one()
    all_passed &= _check(
        "no AnalysisRun row committed for the failed attempt (whole transaction rolled back)",
        after_count == before_count, f"before={before_count} after={after_count}",
    )

    failed_status_count = db.execute(
        select(func.count()).select_from(AnalysisRun).where(
            AnalysisRun.repository_id == repository.id, AnalysisRun.status == AnalysisRunStatus.FAILED
        )
    ).scalar_one()
    all_passed &= _check(
        "no FAILED AnalysisRun row exists (that status transition is Phase 5's job, not 4.4's)",
        failed_status_count == 0,
    )

    return all_passed


def _raises_value_error(fn, *, any_exception: bool = False) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True
    except Exception:
        return any_exception


# --- group 4: real dogfood run (secondary integration check) -------------------


def run_real_repo_dogfood(db: Session, repository: Repository) -> bool:
    print("\n=== group 4 (secondary): real dogfood run — this repo's own server/app + client/src ===")
    all_passed = True

    files = _walk_real_repo(REPO_ROOT / "server" / "app", "server/app", (".py",))
    files += _walk_real_repo(REPO_ROOT / "client" / "src", "client/src", (".ts", ".tsx"))
    for manifest_rel in ("server/pyproject.toml", "client/package.json", "client/tsconfig.json"):
        manifest_path = REPO_ROOT / manifest_rel
        if manifest_path.is_file():
            files.append(IngestedFile(path=manifest_rel, absolute_path=manifest_path, size_bytes=manifest_path.stat().st_size))

    ingestion_result = IngestionResult(root_dir=REPO_ROOT, files=files, ignored_paths=[])
    commit_sha = _real_commit_sha()
    result = analyze_ingested(ingestion_result, commit_sha=commit_sha)

    # Independently recomputed — no hand-derived expectations exist for this
    # repo's real shape, unlike groups 1/2 above, so cross-checking against
    # a second, separate call to the same pure functions is the right tool
    # here (it's still a real, meaningful check: it fails if
    # persist_analysis_run's own internal graph/debt calls ever diverge from
    # what a caller computing the same thing independently would get).
    graph_analysis = analyze_dependency_graph(result.dependency_edges)
    debt_analysis = analyze_technical_debt(
        file_metrics=result.file_metrics,
        module_metrics=result.module_metrics,
        function_complexity=result.function_complexity,
        symbols=result.symbols,
        graph_analysis=graph_analysis,
        external_dependencies=result.external_dependencies,
    )

    _clear_previous_runs(db, repository)
    run = persist_analysis_run(db, repository_id=repository.id, branch="main", analysis_result=result)

    all_passed &= _check("AnalysisRun.status == COMPLETED", run.status is AnalysisRunStatus.COMPLETED)
    all_passed &= _check("AnalysisRun.completed_at is set", run.completed_at is not None)
    all_passed &= _check(
        f"debt_score matches independently recomputed value ({debt_analysis.score})",
        abs(run.debt_score - debt_analysis.score) < 1e-9, str(run.debt_score),
    )

    file_rows = db.execute(select(File).where(File.analysis_run_id == run.id)).scalars().all()
    symbol_rows = db.execute(select(SymbolRow).where(SymbolRow.analysis_run_id == run.id)).scalars().all()
    api_rows = db.execute(select(ApiEndpoint).where(ApiEndpoint.analysis_run_id == run.id)).scalars().all()
    edge_rows = db.execute(select(DependencyEdgeRow).where(DependencyEdgeRow.analysis_run_id == run.id)).scalars().all()
    debt_rows = db.execute(select(DebtIssue).where(DebtIssue.analysis_run_id == run.id)).scalars().all()

    all_passed &= _check("File row count matches in-memory result", len(file_rows) == len(result.file_metrics), f"{len(file_rows)} vs {len(result.file_metrics)}")
    all_passed &= _check("Symbol row count matches in-memory result", len(symbol_rows) == len(result.symbols), f"{len(symbol_rows)} vs {len(result.symbols)}")
    all_passed &= _check("ApiEndpoint row count matches in-memory result", len(api_rows) == len(result.api_routes), f"{len(api_rows)} vs {len(result.api_routes)}")
    all_passed &= _check("DependencyEdge row count matches in-memory result", len(edge_rows) == len(result.dependency_edges), f"{len(edge_rows)} vs {len(result.dependency_edges)}")
    all_passed &= _check("DebtIssue row count matches independently recomputed debt analysis", len(debt_rows) == len(debt_analysis.issues), f"{len(debt_rows)} vs {len(debt_analysis.issues)}")

    circular_count = sum(1 for e in edge_rows if e.is_circular)
    all_passed &= _check(
        "circular-edge row count matches independently recomputed graph analysis",
        circular_count == len(graph_analysis.circular_edges), f"{circular_count} vs {len(graph_analysis.circular_edges)}",
    )

    all_passed &= _check("Repository.last_analyzed_commit_sha updated", repository.last_analyzed_commit_sha == commit_sha)
    all_passed &= _check("Repository.analysis_status == COMPLETED", repository.analysis_status is RepositoryAnalysisStatus.COMPLETED)

    all_passed &= _check("every ApiEndpoint.auth_required is False", all(a.auth_required is False for a in api_rows))
    all_passed &= _check("every File row's repository_id matches", all(f.repository_id == repository.id for f in file_rows))
    all_passed &= _check("every Symbol row's repository_id matches", all(s.repository_id == repository.id for s in symbol_rows))
    all_passed &= _check("every ApiEndpoint row's repository_id matches", all(a.repository_id == repository.id for a in api_rows))
    all_passed &= _check("every DependencyEdge row's repository_id matches", all(e.repository_id == repository.id for e in edge_rows))

    # A known real route (per smoke_analysis.py's own real-repo assertions):
    # its handler_symbol_id should resolve to the real 'register' Symbol row.
    register_route = next((a for a in api_rows if a.method == "POST" and a.path == "/api/v1/auth/register"), None)
    all_passed &= _check("real route POST /api/v1/auth/register persisted", register_route is not None)
    if register_route is not None:
        handler = db.get(SymbolRow, register_route.handler_symbol_id) if register_route.handler_symbol_id else None
        all_passed &= _check(
            "its handler_symbol_id resolves to the real 'register' Symbol row",
            handler is not None and handler.name == "register", str(handler),
        )

    return all_passed


# --- group 5: live bonus (non-gating) -------------------------------------------

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "SubTrack"


def run_live_bonus(db: Session, user: User) -> bool:
    print(f"\n=== group 5 (non-gating): live analyze_commit() + persist against {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
    try:
        installations = github_service.list_installations()
        installation = next(
            (i for i in installations if i["account"]["login"].lower() == LIVE_TEST_OWNER.lower()), None
        )
        if installation is None:
            print(f"  [SKIP] no installation found for account {LIVE_TEST_OWNER!r}")
            return True

        token_data = github_service.get_installation_access_token(str(installation["id"]))
        token = token_data["token"]
        branch_info = github_service.get_branch(token, LIVE_TEST_OWNER, LIVE_TEST_REPO, "main")
        commit_sha = branch_info["commit"]["sha"]

        result = pipeline.analyze_commit(
            installation_token=token, owner=LIVE_TEST_OWNER, repo=LIVE_TEST_REPO, commit_sha=commit_sha
        )

        repository = _get_or_create_repository(
            db, user,
            github_repo_id=f"smoke-storage-live-{LIVE_TEST_OWNER}-{LIVE_TEST_REPO}".lower(),
            owner=LIVE_TEST_OWNER, name=LIVE_TEST_REPO,
        )
        _clear_previous_runs(db, repository)
        run = persist_analysis_run(db, repository_id=repository.id, branch="main", analysis_result=result)

        file_count = db.execute(
            select(func.count()).select_from(File).where(File.analysis_run_id == run.id)
        ).scalar_one()
        print(f"  persisted: files={file_count} status={run.status.value} debt_score={run.debt_score}")
        return run.status is AnalysisRunStatus.COMPLETED and file_count == len(result.file_metrics)
    except Exception as exc:  # noqa: BLE001 — smoke script, report and continue
        print(f"  [ERROR] live bonus test failed: {exc!r}")
        return False


def main() -> int:
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        cycle_repo = _get_or_create_repository(
            db, user, github_repo_id="smoke-storage-cycle-fixtures", owner="smoke-storage", name="dependency-cycle-fixtures"
        )
        debt_repo = _get_or_create_repository(
            db, user, github_repo_id="smoke-storage-debt-fixtures", owner="smoke-storage", name="debt-fixtures"
        )
        atomicity_repo = _get_or_create_repository(
            db, user, github_repo_id="smoke-storage-atomicity", owner="smoke-storage", name="atomicity-fixture"
        )
        dogfood_repo = _get_or_create_repository(
            db, user, github_repo_id="smoke-storage-dogfood", owner="smoke-storage", name="dogfood"
        )

        r1 = run_dependency_cycle_persistence(db, cycle_repo)
        r2 = run_debt_fixture_persistence(db, debt_repo)
        r3 = run_transaction_atomicity(db, atomicity_repo)
        r4 = run_real_repo_dogfood(db, dogfood_repo)
        r5 = run_live_bonus(db, user)
    finally:
        db.close()

    print("\n=== Summary ===")
    print(f"1. dependency_cycle persistence: {'PASS' if r1 else 'FAIL'}")
    print(f"2. debt fixture persistence:     {'PASS' if r2 else 'FAIL'}")
    print(f"3. transaction atomicity:        {'PASS' if r3 else 'FAIL'}")
    print(f"4. real dogfood run:             {'PASS' if r4 else 'FAIL'}")
    print(f"5. live bonus:                   {'PASS' if r5 else 'FAIL (see above — may be a network/credentials issue)'}")
    # Matches smoke_analysis.py's own precedent: the live network/credentials
    # check doesn't gate the suite's exit code.
    return 0 if (r1 and r2 and r3 and r4) else 1


if __name__ == "__main__":
    sys.exit(main())
