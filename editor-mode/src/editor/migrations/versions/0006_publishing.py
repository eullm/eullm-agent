"""Publishing behind approval: targets (CMS, channels, webhooks) and
publications, each waiting for a person before anything leaves.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = ["publish_targets", "publications"]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE editor.publish_targets (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            kind text NOT NULL CHECK (kind IN ('wordpress', 'webhook', 'telegram_channel')),
            name text NOT NULL,
            -- Never secrets: credentials are read from the environment
            -- variable named in secret_env.
            config jsonb NOT NULL DEFAULT '{}',
            secret_env text,
            enabled boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE editor.publications (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            draft_id bigint NOT NULL REFERENCES editor.drafts ON DELETE CASCADE,
            target_id bigint NOT NULL REFERENCES editor.publish_targets ON DELETE CASCADE,
            mode text NOT NULL DEFAULT 'draft' CHECK (mode IN ('draft', 'publish')),
            payload jsonb NOT NULL,
            status text NOT NULL DEFAULT 'pending_approval'
                CHECK (status IN ('pending_approval', 'approved', 'rejected', 'published', 'failed')),
            requested_by text NOT NULL,
            requested_at timestamptz NOT NULL DEFAULT now(),
            decided_by text,
            decided_at timestamptz,
            decision_note text,
            published_at timestamptz,
            external_id text,
            external_url text,
            error text,
            attempts integer NOT NULL DEFAULT 0,
            -- Nothing goes out without a person's decision.
            CHECK (status IN ('pending_approval') OR decided_by IS NOT NULL)
        );
        CREATE UNIQUE INDEX publications_once ON editor.publications (draft_id, target_id, mode)
            WHERE status IN ('pending_approval', 'approved', 'published');
        """
    )
    for table in TABLES:
        op.execute(tenant_rls(table))
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS editor.{table} CASCADE")
