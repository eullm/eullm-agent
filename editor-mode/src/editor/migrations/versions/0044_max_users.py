"""Accesses included in a plan (Francesco 2026-11-08): how many people a
newsroom can have, the director included, open invites counted. Empty is
no limit. Starting values: Solo 1, Starter 2, Pro 5, Business 10, Agency
and the others without limit; newsrooms take their plan's.

Revision ID: 0044
Revises: 0043
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.plans ADD COLUMN max_users integer;
        UPDATE editor.plans SET max_users = CASE code WHEN 'solo' THEN 1 WHEN 'starter' THEN 2
            WHEN 'pro' THEN 5 WHEN 'business' THEN 10 END;
        ALTER TABLE editor.tenants ADD COLUMN max_users integer;
        UPDATE editor.tenants t SET max_users = p.max_users FROM editor.plans p WHERE p.code = t.plan;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.tenants DROP COLUMN max_users;
        ALTER TABLE editor.plans DROP COLUMN max_users;
        """
    )
