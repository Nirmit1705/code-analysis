"""Repository/branch persistence backing the `app/api/v1/github.py` repo and
branch endpoints (Phase 2.4). `persist_analysis_run` and the rest of the
analysis-storage logic land here in Phase 4.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.branch import Branch
from app.models.repository import Repository
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
            connected_user_id=user.id,
            installation_id=installation_id,
        )
    else:
        repository.owner = owner
        repository.name = name
        repository.full_name = full_name
        repository.default_branch = default_branch
        repository.installation_id = installation_id

    db.add(repository)
    db.commit()
    db.refresh(repository)
    return repository


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
