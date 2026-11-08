"""Credit prices raised (Francesco 2026-11-08): an article beyond the plan
cost 25 credits, about 0,25 €, less than an article of any plan and close
to its real cost; now 100 (about 1 €). A cover redrawn: 3 to 5. Prices the
console already changed stay as they are.

Revision ID: 0043
Revises: 0042
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE editor.credit_prices SET credits = 100 WHERE action = 'article' AND credits = 25;
        UPDATE editor.credit_prices SET credits = 5 WHERE action = 'image' AND credits = 3;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE editor.credit_prices SET credits = 25 WHERE action = 'article' AND credits = 100;
        UPDATE editor.credit_prices SET credits = 3 WHERE action = 'image' AND credits = 5;
        """
    )
