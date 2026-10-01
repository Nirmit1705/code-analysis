"""add api_endpoints handler_name handler_line_start

Revision ID: d7a3f1c85e20
Revises: c41e7a9d2b58
Create Date: 2026-10-01 12:00:00.000000

Phase 10.1: `ApiEndpoint.handler_name`/`handler_line_start` — two nullable
columns recording what `ApiRoute` already carried but `persist_analysis_run`
used to drop (the raw handler reference text and the route-defining
construct's first line). Needed by the API Reference screen now and by the
tracer/docs later. Additive, no backfill: rows from older runs stay NULL and
the read path falls back to the `handler_symbol_id` `Symbol` row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7a3f1c85e20'
down_revision: Union[str, Sequence[str], None] = 'c41e7a9d2b58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('api_endpoints', sa.Column('handler_name', sa.String(length=255), nullable=True))
    op.add_column('api_endpoints', sa.Column('handler_line_start', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('api_endpoints', 'handler_line_start')
    op.drop_column('api_endpoints', 'handler_name')
