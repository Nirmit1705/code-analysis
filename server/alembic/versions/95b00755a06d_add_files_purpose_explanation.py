"""add files purpose explanation

Revision ID: 95b00755a06d
Revises: ba23ae50a088
Create Date: 2026-09-22 11:54:25.804884

Phase 8.7: `File.purpose`/`File.explanation` — two nullable `Text` columns,
lazily filled in and cached by `file_insight_service.py::get_or_generate_file_insight`
on first `GET .../insight` view (design decision #2), not eagerly during
indexing. Additive, no backfill — every existing row simply stays NULL until
its file is next viewed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95b00755a06d'
down_revision: Union[str, Sequence[str], None] = 'ba23ae50a088'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('files', sa.Column('purpose', sa.Text(), nullable=True))
    op.add_column('files', sa.Column('explanation', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('files', 'explanation')
    op.drop_column('files', 'purpose')
