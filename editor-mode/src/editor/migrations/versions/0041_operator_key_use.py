"""The console's page of API keys says when each key was last used: a key
nobody uses can go, the one the public site uses must not.

Revision ID: 0041
Revises: 0040
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE editor.operator_tokens ADD COLUMN last_used_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE editor.operator_tokens DROP COLUMN last_used_at")
