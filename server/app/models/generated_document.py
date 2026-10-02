"""GeneratedDocument model (Phase 10.8) — one row per (repository, document
type): the current generated version of one Documentation-screen section.

Documentation is generated **only on an explicit request** (docs/PHASE_10_PLAN.md
decision #7), never on view and never in the analysis pipeline, and a new
generation *replaces* the old one (`UNIQUE(repository_id, doc_type)`, upsert).
`analysis_run_id` records which run the content was built from; comparing it to
the repository's latest completed run is what makes "this document is stale"
computable without storing a flag that could go out of date.

`content` is `{"blocks": [...], "source": "<one line naming what it was built
from>"}`. A block is `{h?, p?, list?, code?, ai?}` — `ai: true` marks a block
whose `p` was written by the model rather than assembled from facts, so the
screen and the exports can say which text is which.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocType(str, enum.Enum):
    """The four sections that can be grounded in real facts. Stored by member
    *name* (`OVERVIEW`, …), like every `Enum(..., native_enum=False)` column
    here (Known Issue #15); the lowercase `.value` is what the API exposes."""

    OVERVIEW = "overview"
    ARCHITECTURE = "architecture"
    MODULES = "modules"
    API = "api"


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"
    __table_args__ = (
        UniqueConstraint("repository_id", "doc_type", name="uq_generated_documents_repository_id_doc_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # CASCADE: a run that is deleted (purge, disconnect) takes the documents
    # built from it with it — a document whose source run is gone could not say
    # which commit it describes.
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    doc_type: Mapped[DocType] = mapped_column(Enum(DocType, native_enum=False, length=16), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # True when at least one block's text was written by the model; False for a
    # facts-only document (including every one where the model call failed).
    ai_written: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
