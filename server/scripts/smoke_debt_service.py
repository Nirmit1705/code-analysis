"""Manual smoke test for Phase 10.2's `app/services/debt_service.py`.

Same `_check`/PASS-FAIL/exit-code template as the other smoke scripts; seeding
helpers imported from `smoke_storage` like `smoke_api_compare.py` does.

Four gating groups (real DB, no Celery/LLM/GitHub):

1. `run_keys_and_diff()` — pure: the issue key (a circular-dependency issue is
   keyed by its description, others by type+file+module), two findings sharing
   a key collapse to one, `(new, resolved)` set difference.
2. `run_trend_branch_rule()` — pure: the History screen's branch rule — the
   selected branch when it has >= 2 completed runs, else the branch with the
   most, ties to the newest — including the 9.6 regression (one stray run on a
   side branch must not blank or hijack the trend).
3. `run_categories_and_labels()` — pure: counts, all seven categories present
   and sorted, weights sum to the aggregate score, titles, display ids.
4. `run_seeded_service()` — `build_debt_read` against hand-seeded runs: main
   (commit A, commit B, a same-commit re-analysis), a side-branch run, a
   module-level issue (no file) and a cycle issue; checks the baseline skips
   the same-commit re-analysis, `score_delta`/`new`/`resolved` by hand, the
   trend stays on `main` despite a newer side-branch run, and the 500-issue
   cap (`truncated`) with counts still over *all* issues.

Run: `uv run python scripts/smoke_debt_service.py` (from `server/`).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.analysis.debt import DebtIssueType, DebtSeverity  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.branch import Branch  # noqa: E402
from app.models.debt_issue import DebtIssue  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.services import analysis_read_service, debt_service  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

WEIGHT = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 5.0}


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


# --- shared seeding (also imported by smoke_api_debt.py) ---------------------------------

IssueSpec = tuple[str, str, "str | None", "str | None", str, float]  # type, severity, file, module, description, metric


def seed_run(
    db: Session,
    repository: Repository,
    *,
    commit: str,
    branch: str = "main",
    completed_at: datetime,
    issues: list[IssueSpec],
) -> AnalysisRun:
    """A COMPLETED run with `debt_score` = the sum of its issues' weights
    (exactly what `analysis/debt.py::_aggregate_score` produces)."""
    run = AnalysisRun(
        repository_id=repository.id,
        branch=branch,
        commit_sha=commit * 40,
        status=AnalysisRunStatus.COMPLETED,
        completed_at=completed_at,
        debt_score=sum(WEIGHT[i[1]] for i in issues),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    file_id_by_path: dict[str, uuid.UUID] = {}
    for _t, _s, path, _m, _d, _v in issues:
        if path is not None and path not in file_id_by_path:
            f = File(
                repository_id=repository.id, analysis_run_id=run.id, path=path, language=Language.PYTHON,
                size_bytes=10, module=str(Path(path).parent).replace("\\", "/"), loc=10,
                last_analyzed_commit_sha=run.commit_sha,
            )
            db.add(f)
            db.flush()
            file_id_by_path[path] = f.id
    for issue_type, severity, path, module, description, metric in issues:
        db.add(DebtIssue(
            analysis_run_id=run.id,
            issue_type=DebtIssueType(issue_type),
            severity=DebtSeverity(severity),
            file_id=file_id_by_path.get(path) if path else None,
            module=module,
            description=description,
            metric_value=metric,
        ))
    db.commit()
    return run


def _select_branch(db: Session, repository: Repository, name: str | None) -> None:
    db.query(Branch).filter(Branch.repository_id == repository.id).delete()
    if name is not None:
        db.add(Branch(repository_id=repository.id, name=name, latest_commit_sha="0" * 40, is_selected=True))
    db.commit()


# --- group 1 -----------------------------------------------------------------------------


def run_keys_and_diff() -> bool:
    print("=== group 1: issue keys and the new/resolved set difference ===")
    k = debt_service.issue_key
    ok = _check("same type+file+module -> same key regardless of description / metric",
                k("high_complexity", "a.py", "m", "function 'f' ...") == k("high_complexity", "a.py", "m", "function 'g' ..."))
    ok &= _check("different file -> different key", k("large_file", "a.py", "m", "x") != k("large_file", "b.py", "m", "x"))
    ok &= _check("circular dependency keys on its description (its file list)",
                 k("circular_dependency", None, None, "across 2 file(s): a, b") != k("circular_dependency", None, None, "across 3 file(s): a, b, c"))
    ok &= _check("None file/module are normalised, not crashed on", k("large_module", None, "m", "x") == ("large_module", "", "m", ""))
    cur = {k("large_file", "a.py", "m", ""), k("large_file", "b.py", "m", ""), k("high_complexity", "c.py", "m", "")}
    prev = {k("large_file", "a.py", "m", ""), k("large_file", "gone.py", "m", "")}
    ok &= _check("diff -> (2 new, 1 resolved)", debt_service.diff_issue_keys(cur, prev) == (2, 1))
    ok &= _check("identical sets -> (0, 0); empty previous -> everything new",
                 debt_service.diff_issue_keys(cur, cur) == (0, 0) and debt_service.diff_issue_keys(cur, set()) == (3, 0))
    return ok


# --- group 2 -----------------------------------------------------------------------------


def run_trend_branch_rule() -> bool:
    print("\n=== group 2: trend branch rule (History parity) ===")
    pick = debt_service.pick_trend_branch
    ok = _check("selected branch with >= 2 completed runs wins even if another has more",
                pick(["x", "main", "x", "x", "main"], "main") == "main")
    ok &= _check("the 9.6 bug: a newest stray side-branch run must not hijack/blank the trend",
                 pick(["feature", "main", "main", "main"], "main") == "main")
    ok &= _check("selected branch with only 1 run -> falls back to the branch with the most",
                 pick(["main", "dev", "dev", "dev"], "main") == "dev")
    ok &= _check("no selected branch -> most runs", pick(["a", "b", "b"], None) == "b")
    ok &= _check("tie -> the branch with the newest run (first in a newest-first list)", pick(["b", "a", "a", "b"], None) == "b")
    ok &= _check("nothing completed -> None", pick([], "main") is None)
    ok &= _check("selected branch absent from the runs -> falls back", pick(["dev", "dev"], "main") == "dev")
    return ok


# --- group 3 -----------------------------------------------------------------------------


def run_categories_and_labels() -> bool:
    print("\n=== group 3: categories, severity counts, titles, display ids ===")
    pairs = [("high_complexity", "critical"), ("high_complexity", "medium"), ("large_file", "low"),
             ("circular_dependency", "high"), ("circular_dependency", "high")]
    cats = debt_service.build_categories(pairs)
    by_type = {c.issue_type: c for c in cats}
    ok = _check("all seven categories are always present", len(cats) == 7 and set(by_type) == {t.value for t in DebtIssueType})
    ok &= _check("counts / worst severity / weights by hand",
                 (by_type["high_complexity"].count, by_type["high_complexity"].worst_severity, by_type["high_complexity"].weighted_score) == (2, "critical", 7.0)
                 and (by_type["circular_dependency"].count, by_type["circular_dependency"].worst_severity, by_type["circular_dependency"].weighted_score) == (2, "high", 6.0)
                 and (by_type["large_file"].count, by_type["large_file"].worst_severity, by_type["large_file"].weighted_score) == (1, "low", 1.0),
                 str(cats))
    ok &= _check("an empty category has count 0, no worst severity, weight 0",
                 (by_type["outdated_dependency"].count, by_type["outdated_dependency"].worst_severity, by_type["outdated_dependency"].weighted_score) == (0, None, 0.0))
    ok &= _check("sorted by weighted_score desc", [c.issue_type for c in cats][:3] == ["high_complexity", "circular_dependency", "large_file"], str([c.issue_type for c in cats]))
    ok &= _check("category weights sum to the aggregate score (7+6+1 = 14)", sum(c.weighted_score for c in cats) == 14.0)
    sc = debt_service.count_severities(["high", "high", "low", "critical"])
    ok &= _check("severity counts", (sc.critical, sc.high, sc.medium, sc.low) == (1, 2, 0, 1))
    t = debt_service.build_title
    ok &= _check("title: label + file basename; label + module when no file; bare label when neither",
                 t("high_complexity", "server/app/x.py", "server/app") == "High complexity · x.py"
                 and t("large_module", None, "server/app") == "Large module · server/app"
                 and t("circular_dependency", None, None) == "Circular dependency")
    did = uuid.UUID("3fa9c1aa-0000-0000-0000-000000000000")
    ok &= _check("display id: type prefix + first 6 hex of the row id", debt_service.make_display_id("high_complexity", did) == "CX-3fa9c1")
    ok &= _check("every type has a label, prefix and why copy",
                 all(x.value in debt_service.CATEGORY_LABELS and x.value in debt_service.WHY for x in DebtIssueType))
    return ok


# --- group 4 -----------------------------------------------------------------------------


def run_seeded_service() -> bool:
    print("\n=== group 4: build_debt_read on seeded runs ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-debt-service@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-debt-service", owner="smoke-debt", name="service"
        )
        _clear_previous_runs(db, repository)
        _select_branch(db, repository, "main")
        t0 = datetime.now(timezone.utc) - timedelta(hours=6)

        # commit A (older): 3 findings, one of which will be resolved by B.
        seed_run(db, repository, commit="a", completed_at=t0, issues=[
            ("high_complexity", "high", "app/x.py", "app", "function 'f' (lines 1-9) has cyclomatic complexity 21", 21),
            ("large_file", "medium", "app/big.py", "app", "file has 400 non-blank lines of code (450 total)", 400),
            ("circular_dependency", "medium", None, None, "circular dependency across 2 file(s): app/p.py, app/q.py", 2),
        ])
        # commit B: x.py complexity stays (same key), big.py resolved, a new missing-docs + a new module-level coupling,
        # the cycle persists. Score = critical 5 + 3 x medium 2 = 11 (commit A = high 3 + 2 x medium 2 = 7).
        seed_run(db, repository, commit="b", completed_at=t0 + timedelta(hours=1), issues=[
            ("high_complexity", "critical", "app/x.py", "app", "function 'f' (lines 1-9) has cyclomatic complexity 33", 33),
            ("circular_dependency", "medium", None, None, "circular dependency across 2 file(s): app/p.py, app/q.py", 2),
            ("missing_documentation", "medium", None, "app", "3/4 symbols in module lack a docstring (75%)", 3),
            ("excessive_coupling", "medium", None, "app", "module has coupling score 12 (fan_in=5, fan_out=7)", 12),
        ])
        # a manual re-analysis of the SAME commit B (latest) — must be skipped as the baseline
        latest = seed_run(db, repository, commit="b", completed_at=t0 + timedelta(hours=2), issues=[
            ("high_complexity", "critical", "app/x.py", "app", "function 'f' (lines 1-9) has cyclomatic complexity 33", 33),
            ("circular_dependency", "medium", None, None, "circular dependency across 2 file(s): app/p.py, app/q.py", 2),
            ("missing_documentation", "medium", None, "app", "3/4 symbols in module lack a docstring (75%)", 3),
            ("excessive_coupling", "medium", None, "app", "module has coupling score 12 (fan_in=5, fan_out=7)", 12),
        ])
        # a newer stray run on a side branch (the 9.6 bug shape) — completed AFTER `latest` by time
        seed_run(db, repository, commit="c", branch="feature", completed_at=t0 + timedelta(hours=3), issues=[])

        # get_latest_completed_run now returns the stray feature run; the service is
        # exercised on `latest` (main) explicitly, as the API would for that repository's latest.
        read = debt_service.build_debt_read(db, repository, latest)
        ok = _check("branch/commit/score echoed from the run", read.branch == "main" and read.commit_sha == "b" * 40 and read.debt_score == 11.0, f"{read.debt_score}")
        ok &= _check("previous is commit A (the same-commit re-analysis of B is skipped)",
                     read.previous is not None and read.previous.commit_sha == "a" * 40 and read.previous_debt_score == 7.0, str(read.previous))
        ok &= _check("score_delta == 11 - 7 == +4.0", read.score_delta == 4.0, str(read.score_delta))
        # keys: current = {x.py complexity, cycle, missing_docs(app), coupling(app)}; previous = {x.py complexity, big.py, cycle}
        ok &= _check("new_since_previous == 2 (missing docs, coupling); resolved == 1 (big.py)",
                     (read.new_since_previous, read.resolved_since_previous) == (2, 1), f"{read.new_since_previous}/{read.resolved_since_previous}")
        ok &= _check("total_issues 4; severity counts critical 1 / medium 3",
                     read.total_issues == 4 and (read.severity_counts.critical, read.severity_counts.medium) == (1, 3))
        ok &= _check("category weights add up to debt_score", sum(c.weighted_score for c in read.categories) == read.debt_score)
        ok &= _check("issues sorted severity-desc (critical first)", read.issues[0].severity == "critical" and read.issues[0].file_path == "app/x.py")
        titles = {i.issue_type: i.title for i in read.issues}
        ok &= _check("titles: file basename / module / bare label",
                     titles["high_complexity"] == "High complexity · x.py" and titles["excessive_coupling"] == "Excessive coupling · app"
                     and titles["circular_dependency"] == "Circular dependency", str(titles))
        ok &= _check("trend follows main (selected, 3 completed) despite the newer feature-branch run",
                     read.trend_branch == "main" and [p.commit_sha[0] for p in read.trend] == ["a", "b", "b"], f"{read.trend_branch} {[p.commit_sha[0] for p in read.trend]}")
        ok &= _check("trend is oldest -> newest with scores", [p.debt_score for p in read.trend] == [7.0, 11.0, 11.0])

        # first-ever run on a branch -> no previous, everything None
        feature = db.query(AnalysisRun).filter(AnalysisRun.repository_id == repository.id, AnalysisRun.branch == "feature").one()
        fr = debt_service.build_debt_read(db, repository, feature)
        ok &= _check("a run with nothing earlier on its branch: previous/delta/new/resolved are all None, zero issues clean",
                     fr.previous is None and fr.score_delta is None and fr.new_since_previous is None and fr.resolved_since_previous is None
                     and fr.total_issues == 0 and fr.debt_score == 0.0 and fr.issues == [] and fr.truncated is False
                     and all(c.count == 0 for c in fr.categories) and len(fr.categories) == 7)

        # the 500-issue cap
        _clear_previous_runs(db, repository)
        many = seed_run(db, repository, commit="d", completed_at=t0, issues=[
            ("large_file", "low" if n % 2 else "high", f"app/f{n}.py", "app", f"file has {n} lines", float(n)) for n in range(510)
        ])
        big = debt_service.build_debt_read(db, repository, many)
        ok &= _check("510 issues -> 500 returned, truncated, but counts/total/score are over all 510",
                     len(big.issues) == 500 and big.truncated and big.total_issues == 510
                     and big.severity_counts.high + big.severity_counts.low == 510 and big.debt_score == 255 * 3 + 255 * 1, f"{len(big.issues)} {big.total_issues}")
        ok &= _check("the cap keeps the most severe first (no 'low' before the last 'high')",
                     all(i.severity == "high" for i in big.issues[:255]))
        return ok
    finally:
        db.close()


def main() -> int:
    gating = [run_keys_and_diff(), run_trend_branch_rule(), run_categories_and_labels(), run_seeded_service()]
    print("\n" + ("ALL GATING GROUPS PASS" if all(gating) else "SOME GATING GROUPS FAILED"))
    return 0 if all(gating) else 1


if __name__ == "__main__":
    sys.exit(main())
