"""A payment row can be a refund: money given back from the console.

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.payments DROP CONSTRAINT payments_kind_check;
        ALTER TABLE editor.payments ADD CONSTRAINT payments_kind_check
            CHECK (kind IN ('first', 'recurring', 'manual', 'refund'));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM editor.payments WHERE kind = 'refund';
        ALTER TABLE editor.payments DROP CONSTRAINT payments_kind_check;
        ALTER TABLE editor.payments ADD CONSTRAINT payments_kind_check
            CHECK (kind IN ('first', 'recurring', 'manual'));
        """
    )
