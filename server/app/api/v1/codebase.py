"""Codebase Explorer endpoints (Phase 6.7): a flat file tree, live file
content read straight from GitHub at the analyzed commit, and a Phase-4-
data-only per-file insight (complexity/dependencies/usedBy/apis — no
`purpose`/`explanation`, Phase 8's job).

No shared prefix on `router` — matches `dashboard.py`'s/`architecture.py`'s
own per-file `APIRouter()` convention (composed under `/api/v1` by
`router.py`).
"""

from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.binary_detect import is_binary
from app.analysis.dependencies import DependencyEdgeType
from app.analysis.symbols import SymbolKind
from app.api.deps import get_current_user, get_db
from app.models.api_endpoint import ApiEndpoint
from app.models.dependency import DependencyEdge
from app.models.file import File
from app.models.repository import Repository
from app.models.symbol import Symbol
from app.models.user import User
from app.schemas.codebase import ComplexitySummary, FileContentRead, FileInsightRead, FileTreeEntry, FileTreeRead
from app.services import analysis_read_service, github_service, repo_service
from app.services.github_auth_service import GitHubAuthError, get_installation_token_for_user

router = APIRouter(tags=["codebase"])

# Duplicated from `analysis/debt.py`'s own `COMPLEXITY_THRESHOLDS` (10, 20,
# 30), matching this codebase's established "duplicate small private
# constants" convention (see `dashboard.py`'s identical duplication of
# `_SEVERITY_WEIGHT`). Adds a "Low" tier `debt.py`'s own `_tier` never
# needed — a `DebtIssue` is only ever created once a threshold is crossed,
# so `_tier` has no below-first-threshold case — but this endpoint labels
# every file, including ones with no debt issue at all.
_COMPLEXITY_THRESHOLDS = (10, 20, 30)

# Function-like symbol kinds for a file's `complexity.functions` count.
# Excludes CLASS — a class declaration isn't itself a function.
_FUNCTION_LIKE_KINDS = (SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.COMPONENT)


# Duplicated from `app/api/v1/github.py`'s/`analysis.py`'s/`dashboard.py`'s
# own module-private helpers of the same shape, rather than imported —
# matches this codebase's established convention of not reaching into
# another module's underscore-prefixed functions (see `analysis.py`'s own
# docstring).
def _installation_token_or_400(current_user: User) -> str:
    try:
        return get_installation_token_for_user(current_user)
    except GitHubAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


def _file_or_404(db: Session, repository_id: uuid.UUID, file_id: uuid.UUID) -> File:
    """Ownership-scoped via `File.repository_id` (denormalized, same as
    `Symbol`/`DependencyEdge`/`ApiEndpoint`) rather than restricted to only
    the latest completed run's files — a `file_id` handed back by an older
    `GET /files` tree call (from a since-superseded run) still resolves
    here, harmlessly, rather than 404ing just because a newer analysis has
    since completed. Real security scoping (does this user own this
    repository at all) is what matters, not run recency."""
    file = db.execute(
        select(File).where(File.id == file_id, File.repository_id == repository_id)
    ).scalar_one_or_none()
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return file


@router.get("/repos/{repository_id}/files", response_model=FileTreeRead)
def get_file_tree(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileTreeRead:
    """The latest completed run's files, flat and path-sorted — the
    frontend builds the nested tree client-side (Phase 6.8). Returns
    `analyzed=False` when the repository has never completed an analysis,
    matching `DashboardRead`/`ArchitectureRead`'s own convention."""
    repository = _repository_or_404(db, repository_id, current_user)

    current_run = analysis_read_service.get_latest_completed_run(db, repository.id)
    if current_run is None:
        return FileTreeRead(analyzed=False)

    files = db.execute(
        select(File).where(File.analysis_run_id == current_run.id).order_by(File.path)
    ).scalars().all()

    return FileTreeRead(
        analyzed=True,
        analysis_run_id=current_run.id,
        files=[
            FileTreeEntry(
                id=f.id,
                path=f.path,
                language=f.language.value if f.language is not None else None,
                size_bytes=f.size_bytes,
                loc=f.loc,
            )
            for f in files
        ],
    )


@router.get("/repos/{repository_id}/files/{file_id}", response_model=FileContentRead)
def get_file_content(
    repository_id: uuid.UUID,
    file_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileContentRead:
    """Live content, read straight from GitHub at `file.last_analyzed_commit_sha`
    (not from any local copy — nothing this app persists holds file bytes).
    `truncated=True` when GitHub's Contents API omits inline content
    (`encoding` isn't `"base64"` — real for files over ~1MB, per GitHub's
    own docs); `binary=True` when the decoded bytes aren't valid UTF-8.
    Both leave `content=None` rather than returning garbage."""
    repository = _repository_or_404(db, repository_id, current_user)
    file = _file_or_404(db, repository.id, file_id)
    token = _installation_token_or_400(current_user)

    try:
        data = github_service.get_file_contents(
            token, repository.owner, repository.name, file.path, ref=file.last_analyzed_commit_sha
        )
    except github_service.GitHubServiceError as exc:
        not_found = exc.status_code == status.HTTP_404_NOT_FOUND
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if not_found else status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub error: {exc}",
        ) from exc

    # `isinstance` defends against `path` unexpectedly resolving to a
    # directory listing (GitHub returns a JSON array, not an object) — not
    # a real code path for a genuine `File` row's path, but cheap to guard.
    # `encoding != "base64"` is the real, common case: GitHub omits inline
    # content for files roughly over 1MB (empty `content`/`encoding`
    # fields instead of an error) — both read the same to the caller.
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return FileContentRead(path=file.path, content=None, truncated=True, binary=False)

    raw = base64.b64decode(data.get("content") or "")
    if is_binary(raw):
        return FileContentRead(path=file.path, content=None, truncated=False, binary=True)

    return FileContentRead(path=file.path, content=raw.decode("utf-8"), truncated=False, binary=False)


@router.get("/repos/{repository_id}/files/{file_id}/insight", response_model=FileInsightRead)
def get_file_insight(
    repository_id: uuid.UUID,
    file_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileInsightRead:
    """Complexity/dependencies/usedBy/apis straight from Phase 4 rows —
    `purpose`/`explanation` are always `None` (Phase 8's job), same rule as
    `DashboardRead.ai_insights`/`ArchitectureRead`'s per-node `insight`."""
    repository = _repository_or_404(db, repository_id, current_user)
    file = _file_or_404(db, repository.id, file_id)

    functions = db.execute(
        select(func.count()).where(Symbol.file_id == file.id, Symbol.kind.in_(_FUNCTION_LIKE_KINDS))
    ).scalar_one()

    apis = sorted(
        {
            f"{a.method} {a.path}"
            for a in db.execute(select(ApiEndpoint).where(ApiEndpoint.file_id == file.id)).scalars().all()
        }
    )

    return FileInsightRead(
        path=file.path,
        purpose=None,
        complexity=ComplexitySummary(
            score=file.complexity_score,
            label=_complexity_label(file.complexity_score),
            loc=file.loc,
            functions=functions,
        ),
        dependencies=_build_dependencies(db, file),
        used_by=_build_used_by(db, file),
        apis=apis,
        explanation=None,
    )


def _complexity_label(score: int | None) -> str | None:
    if score is None:
        return None
    if score >= _COMPLEXITY_THRESHOLDS[2]:
        return "Critical"
    if score >= _COMPLEXITY_THRESHOLDS[1]:
        return "High"
    if score >= _COMPLEXITY_THRESHOLDS[0]:
        return "Medium"
    return "Low"


def _build_dependencies(db: Session, file: File) -> list[str]:
    """This file's outgoing edges (`source_file_id == file.id`) within its
    own run — an internal edge resolves to the target file's real path, an
    external edge to the real imported package name. Scoped by
    `file.analysis_run_id` (not just `file.repository_id`), since a
    `DependencyEdge` row is itself run-specific."""
    edges = db.execute(
        select(DependencyEdge).where(
            DependencyEdge.analysis_run_id == file.analysis_run_id, DependencyEdge.source_file_id == file.id
        )
    ).scalars().all()
    if not edges:
        return []

    target_file_ids = {e.target_file_id for e in edges if e.target_file_id is not None}
    path_by_id: dict[uuid.UUID, str] = {}
    if target_file_ids:
        path_by_id = dict(db.execute(select(File.id, File.path).where(File.id.in_(target_file_ids))).all())

    names: set[str] = set()
    for edge in edges:
        if edge.edge_type is DependencyEdgeType.INTERNAL and edge.target_file_id is not None:
            path = path_by_id.get(edge.target_file_id)
            if path is not None:
                names.add(path)
        elif edge.edge_type is DependencyEdgeType.EXTERNAL and edge.target_package_name:
            names.add(edge.target_package_name)
    return sorted(names)


def _build_used_by(db: Session, file: File) -> list[str]:
    """This file's incoming internal edges (`target_file_id == file.id`)
    within its own run — the real paths of files that import it. External
    edges never target a `File` row (see `DependencyEdge.target_file_id`'s
    own docstring), so this is internal-only by construction, no filter
    needed."""
    source_file_ids = db.execute(
        select(DependencyEdge.source_file_id).where(
            DependencyEdge.analysis_run_id == file.analysis_run_id,
            DependencyEdge.target_file_id == file.id,
            DependencyEdge.source_file_id.is_not(None),
        )
    ).scalars().all()
    if not source_file_ids:
        return []
    paths = db.execute(select(File.path).where(File.id.in_(source_file_ids))).scalars().all()
    return sorted(set(paths))
