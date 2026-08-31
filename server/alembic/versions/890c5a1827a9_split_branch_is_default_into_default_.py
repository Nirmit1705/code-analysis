"""split branch is_default into default fact and is_selected

`Branch.is_default` was originally modeled as "GitHub's actual default
branch" (2.1) but got repurposed during 2.4 to mean "the branch currently
selected to analyze" instead, conflating two distinct facts. This migration
restores the unambiguous split:

- `is_default` goes back to meaning GitHub's actual default branch (backfilled
  by joining back to `repositories.default_branch`, since existing rows may
  hold the repurposed 2.4 meaning).
- `is_selected` (new column) takes over "currently selected to analyze",
  seeded from whatever `is_default` held before the backfill above, so any
  selection already made by a user is preserved across the migration.

Revision ID: 890c5a1827a9
Revises: f2ddd5ed0c99
Create Date: 2026-08-31 21:12:14.005037

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '890c5a1827a9'
down_revision: Union[str, Sequence[str], None] = 'f2ddd5ed0c99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('branches', sa.Column('is_selected', sa.Boolean(), server_default='false', nullable=False))

    # Preserve any existing selection (old `is_default` semantics) before
    # `is_default` itself is recomputed to mean GitHub's actual default branch.
    op.execute("UPDATE branches SET is_selected = is_default")
    op.execute(
        """
        UPDATE branches
        SET is_default = (branches.name = repositories.default_branch)
        FROM repositories
        WHERE branches.repository_id = repositories.id
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Restore the pre-split semantics: `is_default` meant "selected to
    # analyze", so a branch that was selected (and, failing that, one that
    # happens to be GitHub's actual default) becomes `is_default` again.
    op.execute("UPDATE branches SET is_default = true WHERE is_selected = true")
    op.drop_column('branches', 'is_selected')
