"""add repositories analysis_mode and ignore_patterns

Revision ID: 3c8f1a2d9e47
Revises: 7b2e9c4a1d63
Create Date: 2026-10-08 12:00:00.000000

Phase 10.11: two repository settings, both NOT NULL with a backfilling server
default.

- `analysis_mode` (`ON_PUSH`/`MANUAL`): whether a GitHub push to the selected
  branch queues a run. Every existing repository keeps today's behaviour, so
  the backfill is `'ON_PUSH'` — the member *name*, not the lowercase `.value`
  (decision #11 / Known Issue #15: `Enum(native_enum=False)` reads rows back
  by name, so `'on_push'` would make every existing row raise `LookupError`).
- `ignore_patterns` (JSONB list): the user's extra ignore patterns, applied on
  top of the built-in defaults. Backfilled to `[]` (no extra patterns).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3c8f1a2d9e47'
down_revision: Union[str, Sequence[str], None] = '7b2e9c4a1d63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'repositories',
        sa.Column(
            'analysis_mode',
            sa.Enum('ON_PUSH', 'MANUAL', name='analysismode', native_enum=False, length=16),
            server_default='ON_PUSH',
            nullable=False,
        ),
    )
    op.add_column(
        'repositories',
        sa.Column(
            'ignore_patterns',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('repositories', 'ignore_patterns')
    op.drop_column('repositories', 'analysis_mode')
