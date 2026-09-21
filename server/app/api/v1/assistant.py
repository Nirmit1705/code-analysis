"""`app/api/v1/assistant.py` (Phase 8.3): a thin HTTP surface over 8.2's
`assistant_service.py` — no new patterns invented, matches every other
Phase 6+ screen endpoint's established conventions exactly.

`POST /repos/{id}/assistant/messages` is the one place in the assistant
flow where the usual "empty schema default for not-analyzed" convention
(Dashboard/Architecture/Codebase/Dependencies) doesn't fit: there's no
sensible empty `AssistantMessageResponse` to return for a question nobody
can ground yet, so a repository with no completed analysis run gets a real
`409`, not a fabricated answer or a `500`. `GET .../suggested-questions`
does have an obvious empty default (`[]`), so it keeps the usual
"not analyzed -> empty result" convention instead.

No shared prefix on `router` — matches `dashboard.py`'s/`dependencies.py`'s/
`codebase.py`'s own per-file `APIRouter()` convention (composed under
`/api/v1` by `router.py`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.repository import Repository
from app.models.user import User
from app.schemas.assistant import (
    AssistantMessageRequest,
    AssistantMessageResponse,
    ReferenceSchema,
    SuggestedQuestionsResponse,
)
from app.services import analysis_read_service, assistant_service, repo_service

router = APIRouter(tags=["assistant"])


# Duplicated from `app/api/v1/dependencies.py`'s/`dashboard.py`'s/
# `architecture.py`'s/`codebase.py`'s own module-private helper of the same
# shape, rather than imported — matches this codebase's established
# convention of not reaching into another module's underscore-prefixed
# functions.
def _repository_or_404(db: Session, repository_id: uuid.UUID, current_user: User) -> Repository:
    repository = repo_service.get_repository_for_user(db, repository_id, current_user.id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


@router.post("/repos/{repository_id}/assistant/messages", response_model=AssistantMessageResponse)
def post_assistant_message(
    repository_id: uuid.UUID,
    body: AssistantMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AssistantMessageResponse:
    repository = _repository_or_404(db, repository_id, current_user)

    if analysis_read_service.get_latest_completed_run(db, repository.id) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository has no completed analysis yet",
        )

    result = assistant_service.answer_question(db, repository_id=repository.id, question=body.question)
    return AssistantMessageResponse(
        answer=result.answer,
        references=[
            ReferenceSchema(
                file_path=r.file_path, symbol_name=r.symbol_name, line_start=r.line_start, line_end=r.line_end
            )
            for r in result.references
        ],
        follow_up_questions=result.follow_up_questions,
        insufficient_context=result.insufficient_context,
    )


@router.get("/repos/{repository_id}/assistant/suggested-questions", response_model=SuggestedQuestionsResponse)
def get_suggested_questions(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SuggestedQuestionsResponse:
    """`[]` (not a `409`) for a repository with no completed analysis — a
    suggestion list has an obvious, honest empty default, unlike an
    answer."""
    repository = _repository_or_404(db, repository_id, current_user)
    questions = assistant_service.list_suggested_questions(db, repository_id=repository.id)
    return SuggestedQuestionsResponse(questions=questions)
