"""add analysis_runs.database_provider

Revision ID: 284f462c9a5a
Revises: b3bba9aa365f
Create Date: 2026-09-13 16:48:51.865351

Architecture screen follow-up (docs/PHASE_6_PLAN.md 6.5/6.6): the real
detected database engine (Prisma's own raw `datasource` provider value,
e.g. "postgresql"), from `analysis/datasource.py::detect_database_provider`
— lets the Architecture graph label a real "Database" node with the
actual engine instead of a generic "uses Prisma" guess. Nullable, no
default — most runs have no Prisma schema at all, and existing rows are
left NULL rather than backfilled (a real "not detected" outcome, not a
gap to paper over).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '284f462c9a5a'
down_revision: Union[str, Sequence[str], None] = 'b3bba9aa365f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('analysis_runs', sa.Column('database_provider', sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('analysis_runs', 'database_provider')
