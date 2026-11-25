"""videos.social: what Bluesky and YouTube said about the trend when the
video idea was proposed (social.py), shown on the idea.

Revision ID: 0052
Revises: 0051
"""

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE editor.videos ADD COLUMN social jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE editor.videos DROP COLUMN social")
