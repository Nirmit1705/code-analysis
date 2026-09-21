"""`POST /repos/{id}/assistant/messages` / `GET /repos/{id}/assistant/
suggested-questions` request/response schemas (Phase 8.3).

Field names snake_case throughout, matching every other schema in this
codebase (`schemas/dependencies.py`'s own docstring already corrects the
record on this point). `ReferenceSchema` mirrors `assistant_service
.Reference` one-for-one; `AssistantMessageResponse` mirrors
`assistant_service.AssistantAnswer` one-for-one — plain Pydantic models,
no business logic here at all.
"""

from __future__ import annotations

from pydantic import BaseModel


class AssistantMessageRequest(BaseModel):
    question: str


class ReferenceSchema(BaseModel):
    file_path: str
    symbol_name: str | None
    line_start: int
    line_end: int


class AssistantMessageResponse(BaseModel):
    answer: str
    references: list[ReferenceSchema]
    follow_up_questions: list[str]
    insufficient_context: bool


class SuggestedQuestionsResponse(BaseModel):
    questions: list[str]
