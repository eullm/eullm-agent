"""Trend e piano looks up each topic's latest opportunity score; the table
was indexed by site and time only, so every topic scanned the scores of the
whole site. Indexed by topic as well.

Revision ID: 0040
Revises: 0039
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX opportunity_scores_topic ON editor.opportunity_scores (topic_id, site_id, computed_at DESC)")
    op.execute("ANALYZE editor.opportunity_scores")


def downgrade() -> None:
    op.execute("DROP INDEX editor.opportunity_scores_topic")
