"""A published article stays in the director's hands: updated on the site
after a change, or withdrawn from it.

- publications.updated_at / updated_by: the last time the article on the
  site was replaced with the current text of its draft;
- publications.withdrawn_at / withdrawn_by: taken off the site. The row
  stays 'published' (what was sent, and where), with the date it ended.

Revision ID: 0038
Revises: 0037
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.publications ADD COLUMN updated_at timestamptz;
        ALTER TABLE editor.publications ADD COLUMN updated_by text;
        ALTER TABLE editor.publications ADD COLUMN withdrawn_at timestamptz;
        ALTER TABLE editor.publications ADD COLUMN withdrawn_by text;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.publications DROP COLUMN withdrawn_by;
        ALTER TABLE editor.publications DROP COLUMN withdrawn_at;
        ALTER TABLE editor.publications DROP COLUMN updated_by;
        ALTER TABLE editor.publications DROP COLUMN updated_at;
        """
    )
