"""add analysis_runs trigger_source

Revision ID: c41e7a9d2b58
Revises: 95b00755a06d
Create Date: 2026-09-29 10:00:00.000000

Phase 9.5: `AnalysisRun.trigger_source` (`MANUAL`/`WEBHOOK`), NOT NULL. Every
pre-existing row was created by the manual endpoint (no webhook path existed
before this phase), so the backfill is `'MANUAL'`.

The server default is the enum member's *name* (uppercase), not its lowercase
`.value` as `docs/PHASE_9_PLAN.md` design decision #9 originally sketched:
SQLAlchemy's `Enum(..., native_enum=False)` reads columns back by member name
(Known Issue #15), so a lowercase `'manual'` backfill would make every
pre-existing row raise `LookupError` on its next ORM SELECT.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c41e7a9d2b58'
down_revision: Union[str, Sequence[str], None] = '95b00755a06d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'analysis_runs',
        sa.Column(
            'trigger_source',
            sa.Enum('MANUAL', 'WEBHOOK', name='analysisruntriggersource', native_enum=False, length=16),
            server_default='MANUAL',
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('analysis_runs', 'trigger_source')
