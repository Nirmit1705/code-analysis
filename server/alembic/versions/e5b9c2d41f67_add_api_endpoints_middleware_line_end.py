"""add api_endpoints middleware handler_line_end

Revision ID: e5b9c2d41f67
Revises: d7a3f1c85e20
Create Date: 2026-10-01 14:00:00.000000

Phase 10.1 follow-up (API Reference redesign): `ApiEndpoint.handler_line_end`
(end of the route-defining construct, so the screen can show the real
declaration/handler code) and `ApiEndpoint.middleware` (JSONB list of the
middleware names passed on the route itself; NULL = this framework's extractor
doesn't record it, `[]` = recorded, none). Additive, no backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e5b9c2d41f67'
down_revision: Union[str, Sequence[str], None] = 'd7a3f1c85e20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('api_endpoints', sa.Column('handler_line_end', sa.Integer(), nullable=True))
    op.add_column('api_endpoints', sa.Column('middleware', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('api_endpoints', 'middleware')
    op.drop_column('api_endpoints', 'handler_line_end')
