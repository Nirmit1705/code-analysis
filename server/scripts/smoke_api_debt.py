"""Manual smoke test for Phase 10.2's REST surface (`app/api/v1/debt.py`):
`GET /repos/{id}/debt`.

Uses `fastapi.testclient.TestClient(app)` against hand-seeded runs (shared
`seed_run` from `smoke_debt_service.py`). The service's own business logic
(branch rule, key diff, caps) is covered by `smoke_debt_service.py`; this
script's job is the HTTP layer: routing, ownership scoping, the response
envelope and field mapping, and a live cross-check against the real database.

Four groups (the last non-gating, real DB, no Celery/LLM/GitHub):

1. `run_not_analyzed()` — no completed run -> `{analyzed: false}` with empty lists.
2. `run_ownership_404()` — a second user's token -> 404; no token -> 401/403.
3. `run_happy_path()` — every field of `DebtRead` over HTTP on a seeded
   two-commit history: ids, branch, scores, delta, severity counts, categories
   (7, weights add to the score), issue fields (display_id, title, file_path,
   module, metric, description, why), new/resolved, `previous`, `trend`.
4. `run_live_bonus()` — `Nirmit1705/HireSight`'s latest run: severity counts
   and per-type counts equal an independent raw-SQL `GROUP BY`, and
   `debt_score` equals both the stored column and the sum of severity weights.

Run: `uv run python scripts/smoke_api_debt.py` (from `server/`).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import analysis_read_service  # noqa: E402
from smoke_debt_service import WEIGHT, _select_branch, seed_run  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def run_not_analyzed() -> bool:
    print("=== group 1: repository with no completed run -> analyzed: false ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-debt-none@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-debt-none", owner="smoke-api", name="debt-none"
        )
        _clear_previous_runs(db, repository)
        resp = TestClient(app).get(f"/api/v1/repos/{repository.id}/debt", headers=_auth(create_access_token(subject=user.email)))
        body = resp.json()
        ok = _check("GET -> 200", resp.status_code == 200, resp.text)
        ok &= _check("analyzed == false, everything else empty", body.get("analyzed") is False and body["issues"] == [] and body["categories"] == []
                     and body["trend"] == [] and body["previous"] is None and body["total_issues"] == 0, str(body)[:300])
        return ok
    finally:
        db.close()


def run_ownership_404() -> bool:
    print("\n=== group 2: a second user's token -> 404 ===")
    db = SessionLocal()
    try:
        owner = _get_or_create_user(db, "smoke-api-debt-owner@local.test")
        other = _get_or_create_user(db, "smoke-api-debt-other@local.test")
        repository = _get_or_create_repository(
            db, owner, github_repo_id="smoke-api-debt-owner", owner="smoke-api", name="debt-owner"
        )
        _clear_previous_runs(db, repository)
        client = TestClient(app)
        resp = client.get(f"/api/v1/repos/{repository.id}/debt", headers=_auth(create_access_token(subject=other.email)))
        ok = _check("other user's GET -> 404", resp.status_code == 404, resp.text)
        resp = client.get(f"/api/v1/repos/{repository.id}/debt")
        ok &= _check("no token -> 401/403", resp.status_code in (401, 403), str(resp.status_code))
        return ok
    finally:
        db.close()


def run_happy_path() -> bool:
    print("\n=== group 3: every DebtRead field over HTTP ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-api-debt-happy@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-api-debt-happy", owner="smoke-api", name="debt-happy"
        )
        _clear_previous_runs(db, repository)
        _select_branch(db, repository, "main")
        t0 = datetime.now(timezone.utc) - timedelta(hours=4)

        seed_run(db, repository, commit="a", completed_at=t0, issues=[
            ("large_file", "medium", "app/big.py", "app", "file has 400 non-blank lines of code (450 total)", 400),
            ("outdated_dependency", "low", "package.json", ".", "react@16.0.0 is 2 major version(s) behind 18", 2),
        ])
        head = seed_run(db, repository, commit="b", completed_at=t0 + timedelta(hours=1), issues=[
            ("high_complexity", "critical", "app/x.py", "app", "function 'f' (lines 1-9) has cyclomatic complexity 33", 33),
            ("circular_dependency", "high", None, None, "circular dependency across 2 file(s): app/p.py, app/q.py", 2),
            ("excessive_coupling", "medium", None, "app", "module has coupling score 12 (fan_in=5, fan_out=7)", 12),
            ("outdated_dependency", "low", "package.json", ".", "react@16.0.0 is 2 major version(s) behind 18", 2),
        ])
        # head score = 5 + 3 + 2 + 1 = 11 ; previous = 2 + 1 = 3 ; delta = +8
        resp = TestClient(app).get(f"/api/v1/repos/{repository.id}/debt", headers=_auth(create_access_token(subject=user.email)))
        ok = _check("GET -> 200", resp.status_code == 200, resp.text)
        if not ok:
            return False
        b = resp.json()
        ok &= _check("envelope: analyzed + run id + branch + commit", b["analyzed"] is True and b["analysis_run_id"] == str(head.id) and b["branch"] == "main" and b["commit_sha"] == "b" * 40)
        ok &= _check("debt_score 11, previous_debt_score 3, score_delta +8", (b["debt_score"], b["previous_debt_score"], b["score_delta"]) == (11.0, 3.0, 8.0), str((b["debt_score"], b["previous_debt_score"], b["score_delta"])))
        ok &= _check("previous run is commit a", b["previous"] is not None and b["previous"]["commit_sha"] == "a" * 40)
        sc = b["severity_counts"]
        ok &= _check("severity_counts critical1 high1 medium1 low1 (total 4)", (sc["critical"], sc["high"], sc["medium"], sc["low"], b["total_issues"]) == (1, 1, 1, 1, 4), str(sc))
        cats = {c["issue_type"]: c for c in b["categories"]}
        ok &= _check("7 categories; weights add up to debt_score", len(cats) == 7 and sum(c["weighted_score"] for c in b["categories"]) == b["debt_score"])
        ok &= _check("category fields", cats["high_complexity"]["label"] == "High complexity" and cats["high_complexity"]["worst_severity"] == "critical"
                     and cats["high_complexity"]["weighted_score"] == 5.0 and cats["large_file"]["count"] == 0 and cats["large_file"]["worst_severity"] is None, str(cats["high_complexity"]))
        ok &= _check("categories ordered by weight desc", [c["issue_type"] for c in b["categories"]][:3] == ["high_complexity", "circular_dependency", "excessive_coupling"], str([c["issue_type"] for c in b["categories"]]))
        issues = b["issues"]
        ok &= _check("issues sorted critical -> high -> medium -> low", [i["severity"] for i in issues] == ["critical", "high", "medium", "low"], str([i["severity"] for i in issues]))
        first = issues[0]
        ok &= _check(
            "issue fields: display_id prefix, category, title, file_path, module, metric, description, why",
            first["display_id"].startswith("CX-") and len(first["display_id"]) == 9 and first["category"] == "High complexity"
            and first["title"] == "High complexity · x.py" and first["file_path"] == "app/x.py" and first["module"] == "app"
            and first["metric_value"] == 33.0 and "cyclomatic complexity 33" in first["description"] and len(first["why"]) > 40, str(first),
        )
        ok &= _check("module-level issue: file_path null, title uses the module", issues[2]["file_path"] is None and issues[2]["title"] == "Excessive coupling · app")
        ok &= _check("cycle issue: neither file nor module, bare-label title", issues[1]["file_path"] is None and issues[1]["module"] is None and issues[1]["title"] == "Circular dependency")
        ok &= _check("new_since_previous 3 (complexity, cycle, coupling), resolved_since_previous 1 (big.py)", (b["new_since_previous"], b["resolved_since_previous"]) == (3, 1), str((b["new_since_previous"], b["resolved_since_previous"])))
        ok &= _check("not truncated", b["truncated"] is False)
        ok &= _check("trend: branch main, two points oldest -> newest", b["trend_branch"] == "main" and [p["commit_sha"][0] for p in b["trend"]] == ["a", "b"] and [p["debt_score"] for p in b["trend"]] == [3.0, 11.0], str(b["trend"]))
        return ok
    finally:
        db.close()


def run_live_bonus() -> bool:
    print("\n=== group 4 (non-gating): HireSight — counts vs an independent raw-SQL GROUP BY ===")
    db = SessionLocal()
    try:
        # Several users can each hold a HireSight copy; use the one with the newest completed run.
        candidates = db.execute(
            select(Repository).where(Repository.owner == LIVE_TEST_OWNER, Repository.name == LIVE_TEST_REPO)
        ).scalars().all()
        best = None
        for r in candidates:
            run = analysis_read_service.get_latest_completed_run(db, r.id)
            if run is not None and (best is None or (run.completed_at or run.created_at) > (best[1].completed_at or best[1].created_at)):
                best = (r, run)
        if best is None:
            print("  [SKIP] no HireSight repository with a completed run in this database")
            return True
        repository, run = best
        user = db.get(User, repository.connected_user_id)
        resp = TestClient(app).get(f"/api/v1/repos/{repository.id}/debt", headers=_auth(create_access_token(subject=user.email)))
        b = resp.json()
        sql_sev = {r[0]: r[1] for r in db.execute(text(
            "select severity, count(*) from debt_issues where analysis_run_id=:r group by severity"), {"r": run.id}).all()}
        sql_type = {r[0]: r[1] for r in db.execute(text(
            "select issue_type, count(*) from debt_issues where analysis_run_id=:r group by issue_type"), {"r": run.id}).all()}
        sc = b["severity_counts"]
        # Enum columns store the member NAME (Known Issue #15): 'CRITICAL', 'HIGH_COMPLEXITY', ...
        sev_ok = all(sc[k] == sql_sev.get(k.upper(), 0) for k in ("critical", "high", "medium", "low"))
        type_ok = all(c["count"] == sql_type.get(c["issue_type"].upper(), 0) for c in b["categories"])
        weighted = sum(WEIGHT[k] * sc[k] for k in WEIGHT)
        print(f"  info: score={b['debt_score']} total={b['total_issues']} sev={sc} delta={b['score_delta']} "
              f"new/resolved={b['new_since_previous']}/{b['resolved_since_previous']} trend={b['trend_branch']}({len(b['trend'])})")
        ok = _check("GET -> 200", resp.status_code == 200, resp.text[:200])
        ok &= _check("severity counts == raw SQL GROUP BY severity", sev_ok, f"{sc} vs {sql_sev}")
        ok &= _check("per-type counts == raw SQL GROUP BY issue_type", type_ok, f"{[ (c['issue_type'], c['count']) for c in b['categories']]} vs {sql_type}")
        ok &= _check("total_issues == COUNT(*)", b["total_issues"] == sum(sql_sev.values()))
        ok &= _check("debt_score == stored column == sum of severity weights", b["debt_score"] == run.debt_score == weighted, f"{b['debt_score']} {run.debt_score} {weighted}")
        ok &= _check("category weights add up to debt_score", sum(c["weighted_score"] for c in b["categories"]) == b["debt_score"])
        return ok
    finally:
        db.close()


def main() -> int:
    gating = [run_not_analyzed(), run_ownership_404(), run_happy_path()]
    bonus = run_live_bonus()
    print("\n" + ("ALL GATING GROUPS PASS" if all(gating) else "SOME GATING GROUPS FAILED") + ("" if bonus else " (live bonus failed)"))
    return 0 if all(gating) else 1


if __name__ == "__main__":
    sys.exit(main())
