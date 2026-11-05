"""Il Punto della Sera: plans say whether they include the director's
evening analysis of the day (evening.py), and each newsroom carries it
from its plan, like the automatic newsroom. Pro, Business and Agency
include it; newsrooms without a plan of the catalogue keep it.

Revision ID: 0042
Revises: 0041
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.plans ADD COLUMN evening boolean NOT NULL DEFAULT false;
        UPDATE editor.plans SET evening = true WHERE code IN ('pro', 'business', 'agency');
        ALTER TABLE editor.tenants ADD COLUMN evening boolean NOT NULL DEFAULT true;
        UPDATE editor.tenants t SET evening = p.evening FROM editor.plans p WHERE p.code = t.plan;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.tenants DROP COLUMN evening;
        ALTER TABLE editor.plans DROP COLUMN evening;
        """
    )
