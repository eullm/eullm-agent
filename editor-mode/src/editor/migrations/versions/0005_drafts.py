"""Article drafts in which every claim cites stored sources.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = ["drafts", "draft_claims", "draft_claim_sources"]


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.source_items
            ADD COLUMN content text,
            ADD COLUMN content_fetched_at timestamptz;

        CREATE TABLE editor.drafts (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            proposal_id bigint NOT NULL REFERENCES editor.proposals ON DELETE CASCADE,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            version integer NOT NULL,
            title text NOT NULL,
            subtitle text NOT NULL DEFAULT '',
            body_md text NOT NULL,
            language text,
            status text NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'needs_review', 'approved', 'rejected')),
            flags jsonb NOT NULL DEFAULT '[]',
            generated_by text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            decided_by text,
            decided_at timestamptz,
            UNIQUE (proposal_id, version),
            CHECK (status NOT IN ('approved', 'rejected') OR decided_by IS NOT NULL)
        );

        CREATE TABLE editor.draft_claims (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            draft_id bigint NOT NULL REFERENCES editor.drafts ON DELETE CASCADE,
            ordinal integer NOT NULL,
            section text,
            text text NOT NULL,
            UNIQUE (draft_id, ordinal)
        );

        -- Every claim has at least one source, and every source is a stored item.
        CREATE TABLE editor.draft_claim_sources (
            tenant_id text NOT NULL REFERENCES editor.tenants,
            claim_id bigint NOT NULL REFERENCES editor.draft_claims ON DELETE CASCADE,
            item_id bigint NOT NULL REFERENCES editor.source_items,
            PRIMARY KEY (claim_id, item_id)
        );
        """
    )
    for table in TABLES:
        op.execute(tenant_rls(table))
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS editor.{table} CASCADE")
    op.execute("ALTER TABLE editor.source_items DROP COLUMN content, DROP COLUMN content_fetched_at")
