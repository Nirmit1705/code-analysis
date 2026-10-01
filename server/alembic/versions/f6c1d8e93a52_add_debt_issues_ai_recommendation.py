"""add debt_issues ai_recommendation

Revision ID: f6c1d8e93a52
Revises: e5b9c2d41f67
Create Date: 2026-10-01 23:30:00.000000

Phase 10.4: `DebtIssue.ai_recommendation` — a nullable `Text` column, lazily
filled in and cached by `debt_insight_service.py::get_or_generate_recommendation`
the first time a finding is expanded on the Debt screen (not eagerly during the
analysis run). Additive, no backfill: every existing row stays NULL until viewed.
`DebtIssue` rows are recreated per analysis run, so a new run starts unset.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6c1d8e93a52'
down_revision: Union[str, Sequence[str], None] = 'e5b9c2d41f67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('debt_issues', sa.Column('ai_recommendation', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('debt_issues', 'ai_recommendation')
