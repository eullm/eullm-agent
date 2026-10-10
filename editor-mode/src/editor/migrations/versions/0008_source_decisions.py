"""Remember who decided a source's status: a person's decision is never
undone by the automatic maintenance.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- NULL when the status was set automatically.
        ALTER TABLE editor.sources ADD COLUMN status_set_by text;
        UPDATE editor.sources SET status_set_by = substr(status_reason, 8)
            WHERE status_reason LIKE 'set by %';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE editor.sources DROP COLUMN status_set_by")
