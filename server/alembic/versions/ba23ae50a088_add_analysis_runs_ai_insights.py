"""add analysis_runs ai_insights

Revision ID: ba23ae50a088
Revises: 30173cdb9096
Create Date: 2026-09-21 15:54:32.310726

Phase 8.4: `AnalysisRun.ai_insights` — a plain JSONB list column (design
decision #7), not a new child table, generated once per run by
`insight_service.py::generate_insights` and cached. Additive, `NOT NULL
DEFAULT '[]'::jsonb` — every existing row gets the default, no backfill
needed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'ba23ae50a088'
down_revision: Union[str, Sequence[str], None] = '30173cdb9096'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'analysis_runs',
        sa.Column(
            'ai_insights',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('analysis_runs', 'ai_insights')
