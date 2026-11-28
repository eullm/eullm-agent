"""videos.scenes: the scenes of an idea (shot for the video model, text on
screen, narrator's words); videos.parts: the video model's job of each scene,
saved as soon as it is asked so a restart never pays a clip twice.

Revision ID: 0053
Revises: 0052
"""

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE editor.videos ADD COLUMN scenes jsonb")
    op.execute("ALTER TABLE editor.videos ADD COLUMN parts jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE editor.videos DROP COLUMN parts")
    op.execute("ALTER TABLE editor.videos DROP COLUMN scenes")
