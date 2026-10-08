"""Repository/branch persistence backing the `app/api/v1/github.py` repo and
branch endpoints (Phase 2.4). `create_queued_analysis_run` /
`persist_analysis_run` / `finalize_analysis_run_completed` /
`mark_analysis_run_failed` (Phase 4.4, split for async use in Phase 5.1,
`persist_analysis_run`/`finalize_analysis_run_completed` split further in
Phase 7.1) live here too — together they're the place a Phase 3
`AnalysisResult` (pure, in-memory) becomes real `AnalysisRun`/`File`/
`Symbol`/`DependencyEdge`/`ApiEndpoint`/`DebtIssue` rows, across the separate
transactions Phase 5's "API queues now, Celery finalizes later" lifecycle
needs: `create_queued_analysis_run` inserts the `QUEUED` row and returns
immediately; `persist_analysis_run` persists a run's facts (File/Symbol/etc.)
in one transaction but does *not* mark the run done; `mark_analysis_run_failed`
finalizes an already-existing row to `FAILED`; `finalize_analysis_run_completed`
finalizes one to `COMPLETED` — kept as its own explicit step (rather than
folded into the tail of `persist_analysis_run`, as it was before Phase 7.1)
because Phase 7's Celery task now needs to run RAG indexing *between* facts
being persisted and the run actually counting as done, and the DB must never
transiently read `COMPLETED` before that indexing stage has even started.
"""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.analysis.debt import analyze_technical_debt
from app.analysis.dependencies import DependencyEdge, DependencyEdgeType
from app.analysis.graph import analyze_dependency_graph
from app.analysis.pipeline import AnalysisResult
from app.analysis.symbols import Symbol
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus, AnalysisRunTriggerSource
from app.models.api_endpoint import ApiEndpoint
from app.models.branch import Branch
from app.models.debt_issue import DebtIssue
from app.models.dependency import DependencyEdge as DependencyEdgeRow
from app.models.file import File
from app.models.repository import AnalysisMode, Repository, RepositoryAnalysisStatus
from app.models.symbol import Symbol as SymbolRow
from app.models.user import User


def get_repository_for_user(db: Session, repository_id: uuid.UUID, user_id: uuid.UUID) -> Repository | None:
    """Scoped lookup — a repository only "exists" for the user who
    connected it, so callers get a clean 404 rather than leaking other
    users' repository ids."""
    return db.execute(
        select(Repository).where(
            Repository.id == repository_id, Repository.connected_user_id == user_id
        )
    ).scalar_one_or_none()


def upsert_repository(
    db: Session,
    *,
    user: User,
    installation_id: str,
    github_repo_id: str,
    owner: str,
    name: str,
    full_name: str,
    default_branch: str,
    private: bool,
) -> Repository:
    """Creates the Repository row for a user's selected GitHub repo, or
    refreshes it if they'd already connected this same repo before
    (matches the `(connected_user_id, github_repo_id)` unique constraint
    from Phase 2.1)."""
    repository = db.execute(
        select(Repository).where(
            Repository.connected_user_id == user.id, Repository.github_repo_id == github_repo_id
        )
    ).scalar_one_or_none()

    if repository is None:
        repository = Repository(
            owner=owner,
            name=name,
            full_name=full_name,
            github_repo_id=github_repo_id,
            default_branch=default_branch,
            private=private,
            connected_user_id=user.id,
            installation_id=installation_id,
        )
    else:
        repository.owner = owner
        repository.name = name
        repository.full_name = full_name
        repository.default_branch = default_branch
        repository.private = private
        repository.installation_id = installation_id

    db.add(repository)
    db.commit()
    db.refresh(repository)
    return repository


def list_repositories_for_user(db: Session, user_id: uuid.UUID) -> list[Repository]:
    """Every `Repository` the given user has connected, newest first.
    Backs `GET /repos` (Phase 6.1) — the frontend's only way to resolve
    "which repositories exist for this user" without already knowing an id,
    needed for the new active-repository plumbing every `/app/*` screen
    from Phase 6.3 onward depends on."""
    return list(
        db.execute(
            select(Repository)
            .where(Repository.connected_user_id == user_id)
            .order_by(Repository.created_at.desc())
        ).scalars()
    )


def get_last_completed_analysis_times(
    db: Session, repository_ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """Batch lookup of each repository's most recent `COMPLETED`
    `AnalysisRun.completed_at`, for `GET /repos`'s list view (Phase 6.1).
    Deliberately not a stored column — `Repository.analysis_status` already
    tracks the *outcome* of the most recent run, but not *when* the most
    recent completed one actually finished, and the two can diverge (e.g. the
    latest run FAILED but an earlier one COMPLETED). One grouped query over
    all requested ids, reusing the existing `(repository_id, status)` index,
    rather than a round trip per repository."""
    if not repository_ids:
        return {}
    rows = db.execute(
        select(AnalysisRun.repository_id, func.max(AnalysisRun.completed_at))
        .where(
            AnalysisRun.repository_id.in_(repository_ids),
            AnalysisRun.status == AnalysisRunStatus.COMPLETED,
        )
        .group_by(AnalysisRun.repository_id)
    ).all()
    return {repository_id: completed_at for repository_id, completed_at in rows}


def upsert_branches(db: Session, repository: Repository, github_branches: list[dict]) -> list[Branch]:
    """Syncs `Branch` rows from live GitHub branch listings (name + head
    commit sha). `is_default` is a fact about GitHub's actual default branch
    (`Repository.default_branch`) and is recomputed for every row, new or
    existing, on every sync. `is_selected` — the "currently selected branch
    to analyze" flag — is never touched on existing rows; it's owned
    exclusively by `select_branch` below. A brand-new branch row is seeded
    as selected only if its name matches `Repository.default_branch` *and*
    no branch for this repository is selected yet, so a freshly connected
    repo has a sensible initial selection before the user picks one
    explicitly, without ever silently overriding an existing choice.
    """
    existing_by_name = {
        branch.name: branch
        for branch in db.execute(
            select(Branch).where(Branch.repository_id == repository.id)
        ).scalars()
    }
    has_selection = any(branch.is_selected for branch in existing_by_name.values())

    result: list[Branch] = []
    for gh_branch in github_branches:
        name = gh_branch["name"]
        sha = gh_branch["commit"]["sha"]
        is_default_now = name == repository.default_branch
        branch = existing_by_name.get(name)
        if branch is None:
            select_it = is_default_now and not has_selection
            branch = Branch(
                repository_id=repository.id,
                name=name,
                latest_commit_sha=sha,
                is_default=is_default_now,
                is_selected=select_it,
            )
            if select_it:
                has_selection = True
            db.add(branch)
        else:
            if branch.latest_commit_sha != sha:
                branch.latest_commit_sha = sha
                db.add(branch)
            if branch.is_default != is_default_now:
                branch.is_default = is_default_now
                db.add(branch)
        result.append(branch)

    db.commit()
    for branch in result:
        db.refresh(branch)
    return result


def select_branch(db: Session, repository: Repository, branch_name: str, commit_sha: str) -> Branch:
    """Marks `branch_name` as the selected branch to analyze for
    `repository` (unsetting any previous selection), upserting a `Branch`
    row for it with the freshly-fetched head commit sha. Never touches
    `is_default` on existing rows — that's GitHub's actual default branch,
    unrelated to what the user has selected to analyze."""
    existing = db.execute(
        select(Branch).where(Branch.repository_id == repository.id)
    ).scalars().all()

    target: Branch | None = None
    for branch in existing:
        if branch.name == branch_name:
            branch.latest_commit_sha = commit_sha
            branch.is_selected = True
            target = branch
        elif branch.is_selected:
            branch.is_selected = False
        db.add(branch)

    if target is None:
        target = Branch(
            repository_id=repository.id,
            name=branch_name,
            latest_commit_sha=commit_sha,
            is_default=(branch_name == repository.default_branch),
            is_selected=True,
        )
        db.add(target)

    db.commit()
    db.refresh(target)
    return target


def list_repositories_by_github_repo_id(db: Session, github_repo_id: str) -> list[Repository]:
    """Every `Repository` row (across *all* users) for one GitHub repo id.
    Unscoped by user, unlike every other by-github-id lookup here — Phase
    9.5's webhook payload carries no Substrate user at all, only GitHub's own
    numeric repo id. `github_repo_id` is only unique per `(connected_user_id,
    github_repo_id)`, so if several Substrate users connected the same GitHub
    repo this returns all of them and the webhook fans out to each (Known
    Issue #28, resolved). Oldest first, so ordering is deterministic."""
    return list(
        db.execute(
            select(Repository)
            .where(Repository.github_repo_id == github_repo_id)
            .order_by(Repository.created_at.asc(), Repository.id.asc())
        ).scalars()
    )


def lock_analysis_run_slot(db: Session, *, repository_id: uuid.UUID, branch: str, commit_sha: str) -> None:
    """Takes a transaction-scoped Postgres advisory lock keyed on
    `(repository_id, branch, commit_sha)`. Serializes concurrent webhook
    deliveries for the same commit so `has_analysis_run_for_commit` +
    `create_queued_analysis_run` can't both pass the check (a redelivery
    racing the original). Released automatically when the session's
    transaction ends — `create_queued_analysis_run`'s own commit, or a
    `db.rollback()` on the skip path — so callers never unlock explicitly."""
    key = f"{repository_id}:{branch}:{commit_sha}"
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


def has_analysis_run_for_commit(db: Session, *, repository_id: uuid.UUID, branch: str, commit_sha: str) -> bool:
    """True if this repository already has a non-`FAILED` run (queued,
    in-flight or completed) for exactly this branch + commit. A `FAILED` run
    doesn't count, so a redelivery after a failed enqueue/analysis retries.
    Used by the webhook to make redelivered/duplicate `push` events
    idempotent (Known Issue #28) — deliberately *not* used by the manual
    endpoint, where re-analyzing the same commit is an explicit user action."""
    return (
        db.execute(
            select(AnalysisRun.id)
            .where(
                AnalysisRun.repository_id == repository_id,
                AnalysisRun.branch == branch,
                AnalysisRun.commit_sha == commit_sha,
                AnalysisRun.status != AnalysisRunStatus.FAILED,
            )
            .limit(1)
        ).first()
        is not None
    )


def create_queued_analysis_run(
    db: Session,
    *,
    repository_id: uuid.UUID,
    branch: str,
    commit_sha: str,
    trigger_source: AnalysisRunTriggerSource,
) -> AnalysisRun:
    """Phase 5's async-lifecycle entry point: a lightweight, insert-only
    counterpart to `persist_analysis_run` below. Called synchronously from
    the API layer (Phase 5.4's `POST /repos/{id}/analysis-runs`) so the
    caller gets a real `AnalysisRun.id` back immediately — well before the
    actual analysis pipeline has even started running in Celery. Creates
    exactly one `AnalysisRun` row with `status=QUEUED` (`started_at`
    defaults to insert time via the column's own `server_default`, same as
    `created_at` — see Known Issue #12 on "queued" vs. "started") and flips
    `Repository.analysis_status` to `QUEUED` too.

    Nothing else happens here — no `File`/`Symbol`/... rows, no pipeline
    execution. Once a background worker has actually produced an
    `AnalysisResult` for this row, it hands the row back to
    `persist_analysis_run` (success) or `mark_analysis_run_failed` (the
    pipeline raised) below.

    Raises `ValueError` if `repository_id` doesn't resolve to a real
    `Repository` row — same contract `persist_analysis_run` used to have
    itself before this split; it now lives here, since this is the function
    that actually creates a row from a bare `repository_id` rather than
    finalizing one that already exists. This is an internal service
    function, not an API endpoint; callers are expected to have already
    validated the repository exists (e.g. via `get_repository_for_user`).
    """
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise ValueError(f"no Repository with id={repository_id}")

    analysis_run = AnalysisRun(
        id=uuid.uuid4(),
        repository_id=repository_id,
        branch=branch,
        commit_sha=commit_sha,
        status=AnalysisRunStatus.QUEUED,
        trigger_source=trigger_source,
    )
    repository.analysis_status = RepositoryAnalysisStatus.QUEUED

    db.add(analysis_run)
    db.add(repository)
    db.commit()
    db.refresh(analysis_run)
    return analysis_run


def mark_analysis_run_failed(
    db: Session, *, analysis_run: AnalysisRun, error_message: str
) -> AnalysisRun:
    """The first code path that ever writes `AnalysisRunStatus.FAILED`.
    Phase 5's Celery task calls this when the pipeline (or
    `persist_analysis_run` itself) raises partway through a `QUEUED` run, so
    the row reflects the failure instead of being left stuck at `QUEUED`/an
    intermediate stage forever. Mirrors `persist_analysis_run`'s own
    `Repository` bookkeeping on the success path: `Repository.analysis_status`
    moves to `FAILED` too.

    `completed_at` is set here as well, not left `None` — there's no
    separate `failed_at` column, and a failed run has, same as a completed
    one, stopped being actively processed; leaving it null on failure would
    make "how long did this run take" / "when did it stop" unanswerable for
    every failed run.

    One transaction, same rollback-and-reraise discipline as
    `persist_analysis_run` below — a failure while merely recording a
    failure should never itself leave a half-updated row.

    `analysis_run` is expected to already be attached to `db` (the normal
    case: a caller loads it via `db.get(AnalysisRun, id)` in the same
    session, then calls this on failure) — this function doesn't merge a
    detached instance from another session.
    """
    repository = db.get(Repository, analysis_run.repository_id)
    if repository is None:
        raise ValueError(f"no Repository with id={analysis_run.repository_id}")

    try:
        analysis_run.status = AnalysisRunStatus.FAILED
        analysis_run.error_message = error_message
        analysis_run.completed_at = datetime.now(timezone.utc)
        repository.analysis_status = RepositoryAnalysisStatus.FAILED

        db.add(analysis_run)
        db.add(repository)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(analysis_run)
    return analysis_run


def persist_analysis_run(
    db: Session, *, analysis_run: AnalysisRun, analysis_result: AnalysisResult
) -> tuple[AnalysisRun, dict[str, uuid.UUID], dict[Symbol, uuid.UUID]]:
    """Persists one Phase 3 `AnalysisResult` (`analysis/pipeline.py`) onto an
    *existing* `AnalysisRun` row — its `File`/`Symbol`/`DependencyEdge`/
    `ApiEndpoint`/`DebtIssue` children. Each `AnalysisRun` gets its own
    complete row snapshot, not diffed against the previous run (simplest
    correct model for history/comparison later). Runs Phase 4.2's
    `analyze_dependency_graph` (for `DependencyEdge.is_circular`) and Phase
    4.3's `analyze_technical_debt` (for the `DebtIssue` rows plus the run's
    aggregate `debt_score`) as part of the same call — this is the one place
    those two pure modules' output actually reaches the database.

    **Does not mark the run `COMPLETED`** (Phase 7.1 change — see the module
    docstring for why) — call `finalize_analysis_run_completed` once the
    caller is done with the run entirely (Phase 7's Celery task runs RAG
    indexing in between). Returns `(analysis_run, file_id_by_path,
    symbol_id_by_symbol)`: the same two lookup maps built below, so a caller
    that also needs to resolve `File`/`Symbol` ids (Phase 7's
    `index_analysis_run`, resolving `RagChunk` FKs) doesn't need a second DB
    query to rebuild them.

    As of Phase 5.1, this function no longer creates the `AnalysisRun` row
    itself — it takes one already created by `create_queued_analysis_run`
    above (`status=QUEUED` at that point), so the API layer can hand back a
    real id before the pipeline has even started running. `analysis_run` is
    expected to already be attached to `db` (see `mark_analysis_run_failed`'s
    docstring above for the same assumption).

    **One transaction end to end.** Every child row's `id` is generated
    explicitly here (`uuid.uuid4()`) rather than left to the column's own
    `default=` (which SQLAlchemy only applies at flush time) — that's what
    lets a child row's FK column reference the right id immediately, in
    Python, before anything is sent to the DB. Nothing in this table family
    uses `relationship()` (plain FK columns + explicit `select()`, matching
    this module's existing style above), so FK resolution is done by hand
    via two lookup maps: `file_id_by_path` (`FileMetrics.file_path` ->
    generated `File.id`) and `symbol_id_by_symbol` (the frozen, hashable
    `Symbol` dataclass instance -> generated `Symbol` row id), the latter
    resolving `ApiRoute.handler_symbol` -> `ApiEndpoint.handler_symbol_id`.
    `db.commit()` is called exactly once, at the very end; any exception
    before that triggers `db.rollback()` and re-raises unchanged — nothing
    partial is ever left committed. Note that this no longer means "nothing
    at all is left in the DB": the `QUEUED` `AnalysisRun` row itself was
    already committed by `create_queued_analysis_run` in an earlier,
    separate transaction, so a failure here leaves that row behind exactly
    as it was (still `QUEUED`) with none of its children — it's the
    caller's job to then call `mark_analysis_run_failed` on it.

    Raises `ValueError` if `analysis_run.repository_id` doesn't resolve to a
    real `Repository` row — this is an internal service function, not an
    API endpoint; callers are expected to have already validated the
    repository exists (e.g. via `get_repository_for_user`).
    """
    repository = db.get(Repository, analysis_run.repository_id)
    if repository is None:
        raise ValueError(f"no Repository with id={analysis_run.repository_id}")

    repository_id = analysis_run.repository_id
    analysis_run_id = analysis_run.id

    try:
        # The pipeline's own resolved commit_sha is the source of truth for
        # what was actually analyzed — kept in sync here in case it ever
        # diverges from the commit_sha create_queued_analysis_run recorded
        # at queue time (expected to already match in every real call path).
        analysis_run.commit_sha = analysis_result.commit_sha

        # --- Files -----------------------------------------------------------
        size_by_path = {f.path: f.size_bytes for f in analysis_result.files}
        complexities_by_file: dict[str, list[int]] = defaultdict(list)
        for fc in analysis_result.function_complexity:
            complexities_by_file[fc.symbol.file_path].append(fc.complexity)

        file_rows: list[File] = []
        file_id_by_path: dict[str, uuid.UUID] = {}
        for fm in analysis_result.file_metrics:
            file_id = uuid.uuid4()
            file_id_by_path[fm.file_path] = file_id
            complexities = complexities_by_file.get(fm.file_path)
            # Average, rounded — a descriptive per-file metric (feeds Phase
            # 6's fileInsight.complexity), not a debt signal in its own
            # right; analysis/debt.py already flags high per-function
            # complexity separately (Known Issue #9's aggregation choice).
            complexity_score = round(sum(complexities) / len(complexities)) if complexities else None
            file_rows.append(
                File(
                    id=file_id,
                    repository_id=repository_id,
                    analysis_run_id=analysis_run_id,
                    path=fm.file_path,
                    language=fm.language,
                    size_bytes=size_by_path.get(fm.file_path, 0),
                    module=fm.module,
                    loc=fm.loc,
                    complexity_score=complexity_score,
                    last_analyzed_commit_sha=analysis_result.commit_sha,
                )
            )

        # --- Symbols ------------------------------------------------------
        symbol_rows: list[SymbolRow] = []
        symbol_id_by_symbol: dict[Symbol, uuid.UUID] = {}
        for symbol in analysis_result.symbols:
            file_id = file_id_by_path.get(symbol.file_path)
            if file_id is None:
                # Shouldn't happen — every symbol's file_path comes from the
                # same file set file_metrics was built from — but never fail
                # the whole run over one unresolved thing, matching the rest
                # of Phase 3's stance.
                continue
            symbol_id = uuid.uuid4()
            symbol_id_by_symbol[symbol] = symbol_id
            symbol_rows.append(
                SymbolRow(
                    id=symbol_id,
                    repository_id=repository_id,
                    file_id=file_id,
                    analysis_run_id=analysis_run_id,
                    name=symbol.name,
                    kind=symbol.kind,
                    line_start=symbol.line_start,
                    line_end=symbol.line_end,
                    module=symbol.module,
                    language=symbol.language,
                    has_docstring=symbol.has_docstring,
                )
            )

        # --- API endpoints -------------------------------------------------
        api_endpoint_rows: list[ApiEndpoint] = []
        for route in analysis_result.api_routes:
            file_id = file_id_by_path.get(route.file_path)
            if file_id is None:
                continue
            handler_symbol_id = (
                symbol_id_by_symbol.get(route.handler_symbol) if route.handler_symbol is not None else None
            )
            api_endpoint_rows.append(
                ApiEndpoint(
                    id=uuid.uuid4(),
                    repository_id=repository_id,
                    analysis_run_id=analysis_run_id,
                    method=route.method,
                    path=route.path,
                    handler_symbol_id=handler_symbol_id,
                    # Phase 10.1: `route.handler_name` always carries the
                    # raw reference text, even for an unresolved handler;
                    # truncated to the column width rather than failing the
                    # whole run over one absurdly long inline expression.
                    handler_name=route.handler_name[:255] if route.handler_name else None,
                    handler_line_start=route.line_start,
                    handler_line_end=route.line_end,
                    middleware=list(route.middleware) if route.middleware is not None else None,
                    file_id=file_id,
                    module=route.module,
                    framework=route.framework,
                    # No decorator/middleware/dependency signal exists
                    # upstream yet (Phase 3.6's ApiRoute doesn't capture
                    # any) — an honest placeholder rather than a fabricated
                    # guess; revisit only if Phase 3.6 is ever extended to
                    # capture that.
                    auth_required=False,
                )
            )

        # --- Dependency edges + circular-dependency flagging (4.2) --------
        graph_analysis = analyze_dependency_graph(analysis_result.dependency_edges)
        circular_edge_set: set[DependencyEdge] = set(graph_analysis.circular_edges)

        dependency_edge_rows: list[DependencyEdgeRow] = []
        for edge in analysis_result.dependency_edges:
            dependency_edge_rows.append(
                DependencyEdgeRow(
                    id=uuid.uuid4(),
                    repository_id=repository_id,
                    analysis_run_id=analysis_run_id,
                    source_file_id=file_id_by_path.get(edge.source_file),
                    target_file_id=(
                        file_id_by_path.get(edge.target_file)
                        if edge.edge_type is DependencyEdgeType.INTERNAL
                        else None
                    ),
                    target_package_name=(
                        edge.target_package if edge.edge_type is DependencyEdgeType.EXTERNAL else None
                    ),
                    edge_type=edge.edge_type,
                    is_circular=edge in circular_edge_set,
                )
            )

        # --- Debt issues (4.3) ---------------------------------------------
        debt_analysis = analyze_technical_debt(
            file_metrics=analysis_result.file_metrics,
            module_metrics=analysis_result.module_metrics,
            function_complexity=analysis_result.function_complexity,
            symbols=analysis_result.symbols,
            graph_analysis=graph_analysis,
            external_dependencies=analysis_result.external_dependencies,
        )
        debt_issue_rows: list[DebtIssue] = [
            DebtIssue(
                id=uuid.uuid4(),
                analysis_run_id=analysis_run_id,
                issue_type=issue.issue_type,
                severity=issue.severity,
                file_id=file_id_by_path.get(issue.file_path),
                module=issue.module,
                description=issue.description,
                metric_value=issue.metric_value,
            )
            for issue in debt_analysis.issues
        ]

        # --- Finalize the run + repository ---------------------------------
        analysis_run.file_count = len(file_rows)
        analysis_run.module_count = len({fm.module for fm in analysis_result.file_metrics})
        analysis_run.api_count = len(api_endpoint_rows)
        analysis_run.dependency_count = len(dependency_edge_rows)
        analysis_run.debt_score = debt_analysis.score
        analysis_run.database_provider = (
            analysis_result.database_provider.engine if analysis_result.database_provider else None
        )

        repository.last_analyzed_commit_sha = analysis_result.commit_sha

        # Explicit staged `db.flush()`es, not just `db.add_all()` followed by
        # one `db.commit()` at the end: without any `relationship()` on these
        # models (this table family's own established convention — see the
        # module docstring above), SQLAlchemy's unit-of-work has no signal
        # connecting e.g. `File` and `DependencyEdge`, so it doesn't reliably
        # order their INSERT statements by FK dependency on its own — a
        # `File` row can otherwise get flushed *after* a `DependencyEdge` row
        # that already references its id, tripping a live FK violation. Each
        # `flush()` here forces the previous stage's rows to actually exist
        # in the DB (still inside this same uncommitted transaction) before
        # the next stage, which references them, is even added. Still just
        # one `db.commit()` — a flush is not a commit, so the "one
        # transaction end to end" contract (and the try/except rollback
        # below) is unaffected.
        db.add(analysis_run)
        db.flush()

        db.add_all(file_rows)
        db.flush()

        db.add_all(symbol_rows)
        db.flush()

        db.add_all(api_endpoint_rows)
        db.add_all(dependency_edge_rows)
        db.add_all(debt_issue_rows)
        db.add(repository)

        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(analysis_run)
    return analysis_run, file_id_by_path, symbol_id_by_symbol


def finalize_analysis_run_completed(db: Session, *, analysis_run: AnalysisRun, repository: Repository) -> AnalysisRun:
    """Marks an `AnalysisRun` (and its `Repository`) `COMPLETED` — the tail
    end that used to live inside `persist_analysis_run` itself, split out in
    Phase 7.1 so a Celery task can run RAG indexing *between* persisting a
    run's facts and the run actually counting as done, without the DB ever
    transiently reading `COMPLETED` while indexing is still in flight (which
    would make Phase 6.2's frontend REST-poll fallback stop polling early and
    silently skip showing the real `INDEXING` stage).

    Expects both `analysis_run` and `repository` to already be attached to
    `db` (same assumption `mark_analysis_run_failed` makes below). Call this
    exactly once per run, after indexing has been attempted (successfully or
    not — see `app/workers/tasks/analysis_tasks.py`'s non-fatal indexing
    wrapper) — never before, and never as a substitute for it.
    """
    analysis_run.status = AnalysisRunStatus.COMPLETED
    analysis_run.completed_at = datetime.now(timezone.utc)
    repository.analysis_status = RepositoryAnalysisStatus.COMPLETED

    db.add(analysis_run)
    db.add(repository)
    db.commit()
    db.refresh(analysis_run)
    return analysis_run


# --- Phase 10.11: repository settings, disconnect, purge -------------------------

# A non-terminal run counts as "in flight" (blocking disconnect/purge with a
# 409) only if it was created within this window. Older non-terminal rows are
# treated as abandoned: a run whose enqueue silently failed, or whose worker
# died, stays `QUEUED`/mid-stage forever (there is no heartbeat column), and
# without a cutoff such a row would block disconnect/purge permanently. Two
# hours is well beyond a normal analysis (HireSight: minutes).
IN_FLIGHT_WINDOW = timedelta(hours=2)

_TERMINAL_RUN_STATUSES = (AnalysisRunStatus.COMPLETED, AnalysisRunStatus.FAILED)


class RunInFlightError(Exception):
    """Raised by `delete_repository`/`purge_repository_runs` while a run is
    live for the repository (the API turns it into a 409)."""

    def __init__(self, analysis_run: AnalysisRun):
        self.analysis_run = analysis_run
        super().__init__(f"analysis run {analysis_run.id} is {analysis_run.status.value}")


def get_in_flight_run(db: Session, repository_id: uuid.UUID, *, now: datetime | None = None) -> AnalysisRun | None:
    """The newest non-terminal run created within `IN_FLIGHT_WINDOW`, or `None`."""
    cutoff = (now or datetime.now(timezone.utc)) - IN_FLIGHT_WINDOW
    return db.execute(
        select(AnalysisRun)
        .where(
            AnalysisRun.repository_id == repository_id,
            AnalysisRun.status.notin_(_TERMINAL_RUN_STATUSES),
            AnalysisRun.created_at >= cutoff,
        )
        .order_by(AnalysisRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def count_analysis_runs(db: Session, repository_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count()).select_from(AnalysisRun).where(AnalysisRun.repository_id == repository_id)
    ).scalar_one()


def get_selected_branch(db: Session, repository_id: uuid.UUID) -> Branch | None:
    return db.execute(
        select(Branch).where(Branch.repository_id == repository_id, Branch.is_selected == True)  # noqa: E712
    ).scalar_one_or_none()


def _lock_repository_row(db: Session, repository_id: uuid.UUID) -> None:
    """`SELECT ... FOR UPDATE` on the repository row, so two disconnect/purge
    requests for one repository serialize (the in-flight check and the delete
    then happen under the same lock). Released by the caller's commit/rollback."""
    db.execute(select(Repository.id).where(Repository.id == repository_id).with_for_update())


def update_repository_settings(
    db: Session,
    repository: Repository,
    *,
    analysis_mode: AnalysisMode | None = None,
    ignore_patterns: list[str] | None = None,
) -> Repository:
    """Sets whichever of the two settings is given (`None` = leave unchanged).
    `ignore_patterns` must already be normalised (the PATCH schema does it).
    Takes effect from the next run: nothing already analyzed is touched."""
    if analysis_mode is not None:
        repository.analysis_mode = analysis_mode
    if ignore_patterns is not None:
        repository.ignore_patterns = list(ignore_patterns)
    db.add(repository)
    db.commit()
    db.refresh(repository)
    return repository


def delete_repository(db: Session, repository: Repository) -> None:
    """Disconnects a repository: deletes the `Repository` row, and Postgres
    cascades (`ON DELETE CASCADE` on every child FK) remove its branches,
    runs, files, symbols, edges, endpoints, debt findings, RAG chunks and
    onboarding progress. Does *not* uninstall the GitHub App (that is the
    user's GitHub-side action and may cover other repositories). Raises
    `RunInFlightError` (nothing deleted) while a run is live."""
    try:
        _lock_repository_row(db, repository.id)
        in_flight = get_in_flight_run(db, repository.id)
        if in_flight is not None:
            raise RunInFlightError(in_flight)
        db.execute(delete(Repository).where(Repository.id == repository.id))
        db.commit()
    except Exception:
        db.rollback()
        raise


def purge_repository_runs(
    db: Session, repository: Repository, *, branch: str, commit_sha: str
) -> tuple[AnalysisRun, int]:
    """Deletes every `AnalysisRun` of the repository (cascading to all of its
    facts and RAG chunks — History, trends and baselines are gone), resets
    `last_analyzed_commit_sha`, and inserts one fresh `MANUAL` `QUEUED` run
    for `branch`@`commit_sha` — all in one transaction (committed by
    `create_queued_analysis_run`), so a failure leaves the old runs intact.
    Branches, settings and onboarding ticks (keyed by component id, designed
    to survive re-analysis) are kept. Raises `RunInFlightError` while a run is
    live. Returns `(new_run, deleted_run_count)`; the caller enqueues the task."""
    try:
        _lock_repository_row(db, repository.id)
        in_flight = get_in_flight_run(db, repository.id)
        if in_flight is not None:
            raise RunInFlightError(in_flight)
        deleted = db.execute(delete(AnalysisRun).where(AnalysisRun.repository_id == repository.id)).rowcount
        repository.last_analyzed_commit_sha = None
        repository.analysis_status = RepositoryAnalysisStatus.NOT_ANALYZED
        db.add(repository)
        analysis_run = create_queued_analysis_run(
            db,
            repository_id=repository.id,
            branch=branch,
            commit_sha=commit_sha,
            trigger_source=AnalysisRunTriggerSource.MANUAL,
        )
    except Exception:
        db.rollback()
        raise
    return analysis_run, deleted
