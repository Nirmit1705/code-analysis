"""add generated_documents

Revision ID: a8d4e2f61b73
Revises: f6c1d8e93a52
Create Date: 2026-10-02 12:00:00.000000

Phase 10.8: one row per (repository, document type) holding the current
generated Documentation section. `UNIQUE(repository_id, doc_type)` makes
regeneration an upsert; `analysis_run_id` is what makes staleness computable.

New table, so no backfill and no server default on the enum column (the member
*name* would be the right one — Known Issue #15 — but nothing inserts without
setting it).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a8d4e2f61b73'
down_revision: Union[str, Sequence[str], None] = 'f6c1d8e93a52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'generated_documents',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('repository_id', sa.UUID(), nullable=False),
        sa.Column('analysis_run_id', sa.UUID(), nullable=False),
        sa.Column(
            'doc_type',
            sa.Enum('OVERVIEW', 'ARCHITECTURE', 'MODULES', 'API', name='doctype', native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('ai_written', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['analysis_run_id'], ['analysis_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('repository_id', 'doc_type', name='uq_generated_documents_repository_id_doc_type'),
    )
    op.create_index(op.f('ix_generated_documents_repository_id'), 'generated_documents', ['repository_id'], unique=False)
    op.create_index(op.f('ix_generated_documents_analysis_run_id'), 'generated_documents', ['analysis_run_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_generated_documents_analysis_run_id'), table_name='generated_documents')
    op.drop_index(op.f('ix_generated_documents_repository_id'), table_name='generated_documents')
    op.drop_table('generated_documents')
