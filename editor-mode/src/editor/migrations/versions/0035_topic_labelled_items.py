"""How many news items a topic had when the model last named it: the name
is asked again only when the topic has really grown.

Revision ID: 0035
Revises: 0034
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE editor.topics ADD COLUMN labelled_items integer")


def downgrade() -> None:
    op.execute("ALTER TABLE editor.topics DROP COLUMN labelled_items")
