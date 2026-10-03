"""add onboarding_progress

Revision ID: 7b2e9c4a1d63
Revises: f6c1d8e93a52
Create Date: 2026-10-03 12:00:00.000000

Phase 10.10: `onboarding_progress` — which onboarding steps a user has ticked
for a repository. One row per completed step (unticking deletes the row);
`UNIQUE(user_id, repository_id, step_id)`, where `step_id` is the Architecture
component id so a tick survives re-analysis. Both FKs cascade: deleting the user
or the repository removes their progress. New table, no backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7b2e9c4a1d63'
down_revision: Union[str, Sequence[str], None] = 'f6c1d8e93a52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'onboarding_progress',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('step_id', sa.String(length=1024), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'repository_id', 'step_id', name='uq_onboarding_progress_user_repository_step'),
    )
    op.create_index(op.f('ix_onboarding_progress_user_id'), 'onboarding_progress', ['user_id'], unique=False)
    op.create_index(op.f('ix_onboarding_progress_repository_id'), 'onboarding_progress', ['repository_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_onboarding_progress_repository_id'), table_name='onboarding_progress')
    op.drop_index(op.f('ix_onboarding_progress_user_id'), table_name='onboarding_progress')
    op.drop_table('onboarding_progress')
