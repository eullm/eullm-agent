"""Feedback from readers (Francesco 2026-11-11, in trial for every newsroom):
- sites.feedback: the site takes messages from the form under its articles;
- reader_messages: the messages, private to the newsroom, pending until the
  reader confirms by email (readers.py).

Revision ID: 0046
Revises: 0045
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.sites ADD COLUMN feedback boolean NOT NULL DEFAULT false;
        CREATE TABLE editor.reader_messages (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            url text NOT NULL DEFAULT '',
            title text NOT NULL DEFAULT '',
            kind text NOT NULL CHECK (kind IN ('error', 'comment', 'other')),
            message text NOT NULL CHECK (length(message) <= 2000),
            email text NOT NULL,
            language text,
            status text NOT NULL CHECK (status IN ('pending', 'new', 'read', 'closed')),
            token_sha256 text UNIQUE,
            address_hash text,
            created_at timestamptz NOT NULL DEFAULT now(),
            confirmed_at timestamptz
        );
        CREATE INDEX reader_messages_site ON editor.reader_messages (tenant_id, site_id, status, created_at);
        """
    )
    op.execute(tenant_rls("reader_messages"))
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE editor.reader_messages;
        ALTER TABLE editor.sites DROP COLUMN feedback;
        """
    )
