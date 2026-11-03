"""The Desk counts "news in the last 24 hours" several times, and the
reading of the archive has no index on the day an item was fetched: every
count read the whole archive. An index on (tenant_id, fetched_at) lets it
read only the day.

Revision ID: 0039
Revises: 0038
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX source_items_fetched ON editor.source_items (tenant_id, fetched_at DESC)")
    op.execute("ANALYZE editor.source_items")


def downgrade() -> None:
    op.execute("DROP INDEX editor.source_items_fetched")
