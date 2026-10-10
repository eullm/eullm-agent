"""Editorial layer: profiles, opportunity scores, proposals with citations, briefings.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = [
    "editorial_profiles",
    "opportunity_scores",
    "proposals",
    "proposal_citations",
    "briefings",
    "recipients",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE editor.editorial_profiles (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            version integer NOT NULL,
            status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'retired')),
            body jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            approved_by text,
            approved_at timestamptz,
            UNIQUE (site_id, version),
            CHECK (status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX editorial_profiles_one_approved
            ON editor.editorial_profiles (site_id) WHERE status = 'approved';

        CREATE TABLE editor.opportunity_scores (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            topic_id bigint NOT NULL REFERENCES editor.topics ON DELETE CASCADE,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            profile_id bigint NOT NULL REFERENCES editor.editorial_profiles,
            computed_at timestamptz NOT NULL DEFAULT now(),
            formula_version text NOT NULL,
            score real NOT NULL CHECK (score BETWEEN 0 AND 100),
            components jsonb NOT NULL,
            missing text[] NOT NULL DEFAULT '{}'
        );
        CREATE INDEX opportunity_scores_site ON editor.opportunity_scores (site_id, computed_at DESC);

        CREATE TABLE editor.proposals (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            topic_id bigint NOT NULL REFERENCES editor.topics,
            profile_id bigint NOT NULL REFERENCES editor.editorial_profiles,
            opportunity real NOT NULL,
            title text NOT NULL,
            angle text NOT NULL DEFAULT '',
            why_now text NOT NULL DEFAULT '',
            format text NOT NULL DEFAULT 'article',
            generated_by text NOT NULL,
            status text NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'accepted', 'rejected', 'drafted')),
            decided_by text,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX proposals_site ON editor.proposals (site_id, created_at DESC);

        -- A proposal cites only items that exist: the foreign key enforces it.
        CREATE TABLE editor.proposal_citations (
            tenant_id text NOT NULL REFERENCES editor.tenants,
            proposal_id bigint NOT NULL REFERENCES editor.proposals ON DELETE CASCADE,
            item_id bigint NOT NULL REFERENCES editor.source_items,
            PRIMARY KEY (proposal_id, item_id)
        );

        CREATE TABLE editor.briefings (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            briefing_date date NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            body_md text NOT NULL,
            body_html text NOT NULL,
            proposal_ids bigint[] NOT NULL DEFAULT '{}',
            sent_email_at timestamptz,
            sent_telegram_at timestamptz,
            send_error text,
            UNIQUE (tenant_id, briefing_date)
        );

        CREATE TABLE editor.recipients (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            channel text NOT NULL CHECK (channel IN ('email', 'telegram')),
            address text NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            UNIQUE (tenant_id, channel, address)
        );
        """
    )
    for table in TABLES:
        op.execute(tenant_rls(table))
    # The scheduler needs the list of tenants without being any of them.
    # Only ids leave this function; editor.tenants stays under RLS for editor_app.
    op.execute(
        f"""
        ALTER TABLE editor.tenants NO FORCE ROW LEVEL SECURITY;
        CREATE FUNCTION editor.active_tenants() RETURNS SETOF text
            LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, editor
            AS $$ SELECT tenant_id FROM editor.tenants ORDER BY tenant_id $$;
        REVOKE ALL ON FUNCTION editor.active_tenants() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION editor.active_tenants() TO {APP_ROLE};
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE};
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS editor.active_tenants()")
    op.execute("ALTER TABLE editor.tenants FORCE ROW LEVEL SECURITY")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS editor.{table} CASCADE")
