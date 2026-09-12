"""add repositories.private

Revision ID: b3bba9aa365f
Revises: eb9da5f426f9
Create Date: 2026-09-12 00:00:00.000000

Phase 6.1: `GitHubRepoSummary.private` was already fetched from GitHub at
repo-connect time but never forwarded past the frontend's own state — this
adds the column so `POST /repos` can actually persist it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3bba9aa365f'
down_revision: Union[str, Sequence[str], None] = 'eb9da5f426f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'repositories',
        sa.Column('private', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('repositories', 'private')
