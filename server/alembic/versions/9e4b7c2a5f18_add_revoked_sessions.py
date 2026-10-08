"""add revoked_sessions

Revision ID: 9e4b7c2a5f18
Revises: 3c8f1a2d9e47
Create Date: 2026-10-08 18:00:00.000000

Server-side logout: one row per signed-out session (`sid` claim of the access
and refresh tokens). Every token check refuses a revoked `sid`. Rows are only
needed until every token of the session has expired (`expires_at`). Cascades
with the user. New table, no backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9e4b7c2a5f18'
down_revision: Union[str, Sequence[str], None] = '3c8f1a2d9e47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'revoked_sessions',
        sa.Column('sid', sa.String(length=64), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('sid'),
    )
    op.create_index(op.f('ix_revoked_sessions_user_id'), 'revoked_sessions', ['user_id'], unique=False)
    op.create_index(op.f('ix_revoked_sessions_expires_at'), 'revoked_sessions', ['expires_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_revoked_sessions_expires_at'), table_name='revoked_sessions')
    op.drop_index(op.f('ix_revoked_sessions_user_id'), table_name='revoked_sessions')
    op.drop_table('revoked_sessions')
