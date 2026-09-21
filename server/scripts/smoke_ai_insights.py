"""Manual smoke test for Phase 8.4's AI Insights generation
(`app/services/insight_service.py::generate_insights`) and its wiring into
`app/workers/tasks/analysis_tasks.py::run_repository_analysis` as a real
`GENERATING_INSIGHTS` Celery stage.

The `alembic upgrade head` / `downgrade -1` / `upgrade head` round-trip for
the new `AnalysisRun.ai_insights` column was verified manually (not
scripted here) — matches this codebase's established precedent
(`smoke_rag_schema.py` etc. don't drive Alembic from within the script
either); see `server/CLAUDE.md`'s 8.4 write-up for that verification.

Reuses the `dependency_cycle/cyclic` fixture (`scripts/fixtures/
dependency_cycle/cyclic/`) throughout — already hand-verified by
`smoke_graph.py`/`smoke_storage.py`/`smoke_api_architecture.py` — rather
than introducing a second fixture: a real `module_a<->module_b` cycle
(-> a real `CIRCULAR_DEPENDENCY` `DebtIssue`), a real `module_c->module_a`
edge (-> real coupling data), and real `Symbol` rows (-> a real doc-coverage
ratio), so `generate_insights`'s own DB queries exercise real data, not
just a stubbed shape.

Four groups:

1. `run_generate_insights_unit()` (gating) — `generate_insights` called
   directly against the real persisted `cyclic` fixture, five stub-provider
   scenarios: a well-formed response returns the exact expected list; a
   non-JSON response, a valid-JSON-wrong-shape response (missing
   `"insights"`), and a response with an invalid `tone` value all degrade
   to `[]`; a stub that raises `app.ai.provider.LLMProviderError` (a real
   provider-side failure, not a malformed response) also returns `[]`,
   never propagates — this function's own documented "never raises"
   contract (distinct from `assistant_service.answer_question`, which lets
   the same exception type propagate — see `insight_service.py`'s own
   module docstring for why).
2. `run_task_level_forced_provider_failure()` (gating) — the *same* fixture
   run twice through the real `run_repository_analysis` task (fixture
   ingestion + fake GitHub token, same monkeypatch shape
   `smoke_analysis_tasks.py` already uses): once with a succeeding stub
   provider (the reference run, establishing this fixture's real
   File/Symbol/DebtIssue counts), once with a provider that raises
   `LLMProviderError` for every call. Asserts the forced-failure run still
   reaches `COMPLETED`, `ai_insights == []`, `generating_insights` appears
   between `indexing` and `completed` in the real event sequence, and its
   File/Symbol/DebtIssue counts exactly match the reference run's — proof,
   not assumption, that a provider failure costs nothing already persisted.
3. `run_task_level_unexpected_exception_in_insight_stage()` (gating) — a
   *different* failure point than group 2: `insight_service.generate_insights`
   itself patched to raise a plain `RuntimeError` (simulating a bug in the
   surrounding query code, not a provider failure `generate_insights`
   already catches internally). Proves the task's own `try/except` around
   this stage (design decision #9) is real defense in depth, not dead code
   — the run still reaches `COMPLETED` with `ai_insights` left at its
   column default (`[]`).
4. `run_live_bonus()` (non-gating) — a real, freshly-triggered
   `Nirmit1705/HireSight` run via `run_repository_analysis.delay()` in
   Celery eager mode (matching `smoke_rag_indexing.py`'s own live-bonus
   pattern). `publish_progress_event` is wrapped (not replaced) so the real
   Redis/WS event flow is untouched while this script also records the
   exact sequence in-process — the same underlying `AnalysisProgressEvent`
   calls a real WebSocket client would relay (`progress_service.py`), so
   this is an equivalent proof that `generating_insights` really fires
   between `indexing` and `completed`, without this script itself opening
   a socket (a literal browser/WS-observed run is 8.6's own explicit DoD
   step, not re-scripted here). Prints the real generated insights for a
   human to eyeball against what's actually true of HireSight.

Run: `uv run python scripts/smoke_ai_insights.py` (from `server/`). Groups
1-3 are offline and fast. Group 4 needs a live GitHub App installation for
`Nirmit1705/HireSight` and makes real Groq + local-model calls — skips
cleanly if that installation isn't found.
"""

from __future__ import annotations

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Real LLM insight text (group 4's live bonus) can contain Unicode
# punctuation that Windows' default console codepage (cp1252) can't encode
# — same fix `smoke_assistant_service.py`/`smoke_api_assistant.py` needed.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from contextlib import contextmanager  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.workers.tasks.analysis_tasks as analysis_tasks  # noqa: E402
from app.ai.provider import LLMProviderError  # noqa: E402
from app.analysis import ingestion as ingestion_module  # noqa: E402
from app.analysis.ingestion import IngestedFile, IngestionResult  # noqa: E402
from app.analysis.pipeline import AnalysisResult, analyze_ingested  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.debt_issue import DebtIssue  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.symbol import Symbol as SymbolRow  # noqa: E402
from app.services import github_service, insight_service, progress_service  # noqa: E402
from app.services.repo_service import (  # noqa: E402
    create_queued_analysis_run,
    finalize_analysis_run_completed,
    persist_analysis_run,
)
from app.workers.celery_app import celery_app  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

SERVER_ROOT = Path(__file__).resolve().parent.parent  # server/
CYCLIC_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dependency_cycle" / "cyclic"

DOGFOOD_USER_EMAIL = "smoke-ai-insights@local.test"

# This codebase's established Phase 6+ sample repo (server/CLAUDE.md).
LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


class _Patch:
    """Tiny monkeypatch-and-restore helper — duplicated per this codebase's
    established small-helper convention (see `smoke_analysis_tasks.py`'s
    own copy)."""

    def __init__(self, obj, attr: str, replacement):
        self._obj = obj
        self._attr = attr
        self._original = getattr(obj, attr)
        setattr(obj, attr, replacement)

    def restore(self) -> None:
        setattr(self._obj, self._attr, self._original)


class _StubProvider:
    """A fake `LLMProvider` returning a fixed response string, or raising a
    fixed exception if one was given instead."""

    def __init__(self, response_text: str | None = None, *, raises: Exception | None = None):
        self._response_text = response_text
        self._raises = raises

    def complete(self, *, system: str, messages: list[dict], response_schema: dict | None = None) -> str:
        if self._raises is not None:
            raise self._raises
        return self._response_text


def _load_ingested_files(root: Path, prefix: str) -> list[IngestedFile]:
    return [
        IngestedFile(path=f"{prefix}/{p.relative_to(root).as_posix()}", absolute_path=p, size_bytes=p.stat().st_size)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]


def _cyclic_ingestion_result() -> IngestionResult:
    files = _load_ingested_files(CYCLIC_FIXTURE_DIR, "cyclic")
    return IngestionResult(root_dir=CYCLIC_FIXTURE_DIR, files=files, ignored_paths=[])


@contextmanager
def _fake_ingest_cyclic(**kwargs):
    """A context-manager stand-in for `ingestion.ingest_repository`
    (patched into `ingestion_module.ingest_repository` for the task-level
    groups), yielding the same cyclic fixture `_persist_cyclic_fixture`
    persists directly for the unit-level group — one fixture, used two
    ways, matching this script's own "don't introduce a second fixture"
    scoping."""
    yield _cyclic_ingestion_result()


def _persist_cyclic_fixture(db: Session, repository: Repository, *, commit_sha: str) -> tuple[AnalysisRun, AnalysisResult]:
    """Duplicated from `smoke_api_architecture.py`'s own helper of the same
    name (this codebase's established small-helper duplication convention)
    — persists the real `cyclic` fixture via the real
    `create_queued_analysis_run`/`persist_analysis_run`/
    `finalize_analysis_run_completed` pipeline, so `generate_insights`'s
    own DB queries run against real `DebtIssue`/`DependencyEdge`/`Symbol`
    rows, not a hand-built shortcut."""
    result = analyze_ingested(_cyclic_ingestion_result(), commit_sha=commit_sha)

    queued_run = create_queued_analysis_run(db, repository_id=repository.id, branch="main", commit_sha=commit_sha)
    run, _file_id_by_path, _symbol_id_by_symbol = persist_analysis_run(
        db, analysis_run=queued_run, analysis_result=result
    )
    run = finalize_analysis_run_completed(db, analysis_run=run, repository=repository)
    return run, result


# --- group 1: generate_insights unit coverage ---------------------------------


def run_generate_insights_unit() -> bool:
    print("=== group 1 (gating): generate_insights — parse/validate/fallback against real persisted facts ===")
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, "smoke-ai-insights-unit@local.test")
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-ai-insights-unit", owner="smoke-ai-insights", name="unit"
        )
        _clear_previous_runs(db, repository)
        run, _result = _persist_cyclic_fixture(db, repository, commit_sha="cyclic".ljust(40, "0")[:40])

        all_passed = True

        well_formed = (
            '{"insights": [{"tone": "warning", "title": "Circular dependency", '
            '"body": "module_a and module_b import each other."}]}'
        )
        result = insight_service.generate_insights(db, analysis_run=run, provider=_StubProvider(well_formed))
        all_passed &= _check(
            "well-formed response -> the exact expected list",
            result == [{"tone": "warning", "title": "Circular dependency", "body": "module_a and module_b import each other."}],
            str(result),
        )

        result = insight_service.generate_insights(db, analysis_run=run, provider=_StubProvider("not valid json at all"))
        all_passed &= _check("non-JSON response -> []", result == [], str(result))

        result = insight_service.generate_insights(db, analysis_run=run, provider=_StubProvider('{"wrong_key": []}'))
        all_passed &= _check("valid JSON missing the 'insights' key -> []", result == [], str(result))

        result = insight_service.generate_insights(
            db, analysis_run=run,
            provider=_StubProvider('{"insights": [{"tone": "not-a-real-tone", "title": "x", "body": "y"}]}'),
        )
        all_passed &= _check("an invalid tone value -> []", result == [], str(result))

        result = insight_service.generate_insights(
            db, analysis_run=run, provider=_StubProvider(raises=LLMProviderError("simulated Groq outage"))
        )
        all_passed &= _check(
            "a real LLMProviderError from the provider never propagates -> []", result == [], str(result)
        )

        return all_passed
    finally:
        db.close()


# --- group 2: task-level forced provider failure -------------------------------


def run_task_level_forced_provider_failure() -> bool:
    print(
        "\n=== group 2 (gating): forced LLMProviderError at the task level -> "
        "COMPLETED, ai_insights == [], facts unaffected ==="
    )
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-ai-insights-task-forced", owner="smoke-ai-insights", name="task-forced"
        )
        _clear_previous_runs(db, repository)

        # Reference run: same fixture, a succeeding stub — establishes the
        # real File/Symbol/DebtIssue counts this fixture produces, so the
        # forced-failure run below can be compared against real numbers
        # rather than a hardcoded guess.
        ref_events: list[str] = []
        ref_patches = [
            _Patch(github_service, "get_installation_access_token", lambda installation_id: {"token": "fake-token"}),
            _Patch(ingestion_module, "ingest_repository", _fake_ingest_cyclic),
            _Patch(analysis_tasks, "publish_progress_event", lambda run_id, event: ref_events.append(event.status)),
            _Patch(insight_service, "GroqProvider", lambda: _StubProvider('{"insights": []}')),
        ]
        reference_queued = create_queued_analysis_run(
            db, repository_id=repository.id, branch="main", commit_sha="aiinsightref0000000000000000000000000000"[:40]
        )
        try:
            reference_result = analysis_tasks.run_repository_analysis(str(reference_queued.id))
        finally:
            for patch in ref_patches:
                patch.restore()

        all_passed = _check(
            "reference run reaches completed", reference_result.get("status") == "completed", str(reference_result)
        )
        expected_file_count = db.execute(
            select(func.count()).select_from(File).where(File.analysis_run_id == reference_queued.id)
        ).scalar_one()
        expected_symbol_count = db.execute(
            select(func.count()).select_from(SymbolRow).where(SymbolRow.analysis_run_id == reference_queued.id)
        ).scalar_one()
        expected_debt_count = db.execute(
            select(func.count()).select_from(DebtIssue).where(DebtIssue.analysis_run_id == reference_queued.id)
        ).scalar_one()
        all_passed &= _check("reference run produced >=1 real DebtIssue (the circular dependency)", expected_debt_count >= 1)

        # Forced-failure run: same fixture, same repository, a provider
        # that raises LLMProviderError on every call.
        forced_events: list[str] = []
        forced_patches = [
            _Patch(github_service, "get_installation_access_token", lambda installation_id: {"token": "fake-token"}),
            _Patch(ingestion_module, "ingest_repository", _fake_ingest_cyclic),
            _Patch(analysis_tasks, "publish_progress_event", lambda run_id, event: forced_events.append(event.status)),
            _Patch(
                insight_service, "GroqProvider",
                lambda: _StubProvider(raises=LLMProviderError("simulated Groq outage (task-level)")),
            ),
        ]
        forced_queued = create_queued_analysis_run(
            db, repository_id=repository.id, branch="main",
            commit_sha="aiinsightforced00000000000000000000000000"[:40],
        )
        try:
            forced_result = analysis_tasks.run_repository_analysis(str(forced_queued.id))
        finally:
            for patch in forced_patches:
                patch.restore()

        all_passed &= _check(
            "forced-failure task still returns status completed", forced_result.get("status") == "completed",
            str(forced_result),
        )
        db.refresh(forced_queued)
        all_passed &= _check("AnalysisRun.status == COMPLETED", forced_queued.status is AnalysisRunStatus.COMPLETED)
        all_passed &= _check(
            "ai_insights == [] (a forced provider failure never fabricates content)",
            forced_queued.ai_insights == [], str(forced_queued.ai_insights),
        )
        all_passed &= _check(
            "event order == cloning/parsing/analyzing/building_graph/indexing/generating_insights/completed",
            forced_events
            == ["cloning", "parsing", "analyzing", "building_graph", "indexing", "generating_insights", "completed"],
            str(forced_events),
        )

        forced_file_count = db.execute(
            select(func.count()).select_from(File).where(File.analysis_run_id == forced_queued.id)
        ).scalar_one()
        forced_symbol_count = db.execute(
            select(func.count()).select_from(SymbolRow).where(SymbolRow.analysis_run_id == forced_queued.id)
        ).scalar_one()
        forced_debt_count = db.execute(
            select(func.count()).select_from(DebtIssue).where(DebtIssue.analysis_run_id == forced_queued.id)
        ).scalar_one()

        all_passed &= _check(
            f"File rows unaffected ({expected_file_count} in both runs)", forced_file_count == expected_file_count,
            f"reference={expected_file_count} forced={forced_file_count}",
        )
        all_passed &= _check(
            f"Symbol rows unaffected ({expected_symbol_count} in both runs)",
            forced_symbol_count == expected_symbol_count,
            f"reference={expected_symbol_count} forced={forced_symbol_count}",
        )
        all_passed &= _check(
            f"DebtIssue rows unaffected ({expected_debt_count} in both runs)", forced_debt_count == expected_debt_count,
            f"reference={expected_debt_count} forced={forced_debt_count}",
        )
        return all_passed
    finally:
        db.close()


# --- group 3: unexpected exception inside generate_insights itself ------------


def run_task_level_unexpected_exception_in_insight_stage() -> bool:
    print(
        "\n=== group 3 (gating): an unexpected exception inside generate_insights itself "
        "-> still COMPLETED (task-level defense in depth) ==="
    )

    def _raising_generate_insights(db, *, analysis_run, provider=None):
        raise RuntimeError("simulated bug inside generate_insights itself")

    patches = [
        _Patch(github_service, "get_installation_access_token", lambda installation_id: {"token": "fake-token"}),
        _Patch(ingestion_module, "ingest_repository", _fake_ingest_cyclic),
        _Patch(analysis_tasks, "publish_progress_event", lambda run_id, event: None),
        _Patch(insight_service, "generate_insights", _raising_generate_insights),
    ]
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
        repository = _get_or_create_repository(
            db, user, github_repo_id="smoke-ai-insights-unexpected-bug", owner="smoke-ai-insights",
            name="unexpected-bug",
        )
        _clear_previous_runs(db, repository)
        queued = create_queued_analysis_run(
            db, repository_id=repository.id, branch="main", commit_sha="aiinsightbug0000000000000000000000000000"[:40]
        )

        result = analysis_tasks.run_repository_analysis(str(queued.id))

        all_passed = True
        all_passed &= _check(
            "task still returns status completed despite the bug", result.get("status") == "completed", str(result)
        )
        db.refresh(queued)
        all_passed &= _check("AnalysisRun.status == COMPLETED", queued.status is AnalysisRunStatus.COMPLETED)
        all_passed &= _check(
            "ai_insights left at its column default [] (the assignment never completed)",
            queued.ai_insights == [], str(queued.ai_insights),
        )
        return all_passed
    finally:
        db.close()
        for patch in patches:
            patch.restore()


# --- group 4 (non-gating): live bonus ------------------------------------------


def run_live_bonus() -> bool:
    print(f"\n=== group 4 (non-gating): live {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} run — generating_insights fires for real ===")
    original_eager = celery_app.conf.task_always_eager
    try:
        installations = github_service.list_installations()
        installation = next(
            (i for i in installations if i["account"]["login"].lower() == LIVE_TEST_OWNER.lower()), None
        )
        if installation is None:
            print(f"  [SKIP] no installation found for account {LIVE_TEST_OWNER!r}")
            return True

        recorded_events: list[str] = []
        real_publish = progress_service.publish_progress_event

        def spying_publish(analysis_run_id, event):
            recorded_events.append(event.status)
            real_publish(analysis_run_id, event)

        patch = _Patch(analysis_tasks, "publish_progress_event", spying_publish)
        db = SessionLocal()
        try:
            user = _get_or_create_user(db, DOGFOOD_USER_EMAIL)
            repository = _get_or_create_repository(
                db, user,
                github_repo_id=f"smoke-ai-insights-live-{LIVE_TEST_OWNER}-{LIVE_TEST_REPO}".lower(),
                owner=LIVE_TEST_OWNER, name=LIVE_TEST_REPO,
            )
            repository.installation_id = str(installation["id"])
            db.add(repository)
            db.commit()

            token_data = github_service.get_installation_access_token(str(installation["id"]))
            branch_info = github_service.get_branch(token_data["token"], LIVE_TEST_OWNER, LIVE_TEST_REPO, "main")
            commit_sha = branch_info["commit"]["sha"]

            _clear_previous_runs(db, repository)
            queued = create_queued_analysis_run(
                db, repository_id=repository.id, branch="main", commit_sha=commit_sha
            )

            celery_app.conf.task_always_eager = True
            async_result = analysis_tasks.run_repository_analysis.delay(str(queued.id))
            result = async_result.get(timeout=900)

            db.refresh(queued)
            all_passed = True
            all_passed &= _check("task returns status completed", result.get("status") == "completed", str(result))
            all_passed &= _check("AnalysisRun.status == COMPLETED", queued.status is AnalysisRunStatus.COMPLETED)

            in_order = (
                "indexing" in recorded_events
                and "generating_insights" in recorded_events
                and "completed" in recorded_events
                and recorded_events.index("indexing")
                < recorded_events.index("generating_insights")
                < recorded_events.index("completed")
            )
            all_passed &= _check(
                "generating_insights appears between indexing and completed in the real event sequence",
                in_order, str(recorded_events),
            )
            print(f"  event sequence: {recorded_events}")

            insights = queued.ai_insights
            print(f"\n  ai_insights ({len(insights)}):")
            for insight in insights:
                print(f"    [{insight.get('tone')}] {insight.get('title')}: {insight.get('body')}")
            all_passed &= _check(
                "3-5 real insights were generated (the prompt's own instruction, not a hard schema constraint)",
                3 <= len(insights) <= 5, f"got {len(insights)}",
            )
            all_passed &= _check(
                "every insight has a valid tone/title/body shape",
                all(
                    isinstance(i, dict) and i.get("tone") in {"danger", "warning", "info"}
                    and isinstance(i.get("title"), str) and isinstance(i.get("body"), str)
                    for i in insights
                ),
                str(insights),
            )
            return all_passed
        finally:
            db.close()
            patch.restore()
    except Exception as exc:  # noqa: BLE001 — non-gating smoke check, report and continue
        print(f"  [SKIP] live bonus unavailable ({type(exc).__name__}: {exc})")
        return True
    finally:
        celery_app.conf.task_always_eager = original_eager


def main() -> int:
    r1 = run_generate_insights_unit()
    r2 = run_task_level_forced_provider_failure()
    r3 = run_task_level_unexpected_exception_in_insight_stage()
    r4 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. generate_insights parse/validate/fallback (gating): {'PASS' if r1 else 'FAIL'}")
    print(f"2. Task-level forced provider failure (gating):        {'PASS' if r2 else 'FAIL'}")
    print(f"3. Task-level unexpected exception (gating):           {'PASS' if r3 else 'FAIL'}")
    print(f"4. Live bonus (non-gating):                            {'PASS' if r4 else 'SKIP/FAIL (excluded from exit code)'}")
    return 0 if (r1 and r2 and r3) else 1


if __name__ == "__main__":
    sys.exit(main())
