"""add users.github_user_id

Revision ID: a8d3e6f21b94
Revises: 9e4b7c2a5f18
Create Date: 2026-10-11 12:00:00.000000

"Continue with GitHub" sign-in (Known Issue #4): the person's own GitHub user
id, proven by GitHub's OAuth flow. Separate from `users.github_id`, which holds
the *installation account's* id (an org, possibly). Nullable, unique, no
backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8d3e6f21b94'
down_revision: Union[str, Sequence[str], None] = '9e4b7c2a5f18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('github_user_id', sa.String(length=64), nullable=True))
    op.create_unique_constraint('uq_users_github_user_id', 'users', ['github_user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_users_github_user_id', 'users', type_='unique')
    op.drop_column('users', 'github_user_id')
