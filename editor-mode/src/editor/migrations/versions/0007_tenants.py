"""Commercial multi-tenancy: plan, limits and settings per tenant, usage
summary. Tenants can read their plan but not change it.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

from editor.migrations.rls import APP_ROLE

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE editor.tenants
            ADD COLUMN plan text NOT NULL DEFAULT 'standard',
            ADD COLUMN max_sites integer,
            ADD COLUMN max_active_sources integer,
            ADD COLUMN max_items_per_day integer,
            ADD COLUMN max_llm_cost_month double precision,
            ADD COLUMN max_drafts_month integer,
            ADD COLUMN timezone text NOT NULL DEFAULT 'Europe/Rome',
            ADD COLUMN briefing_hour integer NOT NULL DEFAULT 8 CHECK (briefing_hour BETWEEN 0 AND 23),
            ADD COLUMN language text NOT NULL DEFAULT 'it',
            ADD COLUMN core_token_env text,
            ADD COLUMN active boolean NOT NULL DEFAULT true;

        -- The application may create its own tenant row (name only) and read
        -- it; plan, limits and settings change only through the owner role.
        REVOKE INSERT, UPDATE, DELETE ON editor.tenants FROM {APP_ROLE};
        GRANT INSERT (tenant_id, name) ON editor.tenants TO {APP_ROLE};

        CREATE OR REPLACE FUNCTION editor.active_tenants() RETURNS SETOF text
            LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, editor
            AS $$ SELECT tenant_id FROM editor.tenants WHERE active ORDER BY tenant_id $$;

        -- Usage of the current tenant (RLS applies: SECURITY INVOKER).
        CREATE VIEW editor.tenant_usage WITH (security_invoker = true) AS
        SELECT t.tenant_id,
               (SELECT count(*) FROM editor.sites) AS sites,
               (SELECT count(*) FROM editor.sources WHERE status = 'active') AS active_sources,
               (SELECT count(*) FROM editor.source_items
                 WHERE fetched_at >= date_trunc('day', now() AT TIME ZONE t.timezone) AT TIME ZONE t.timezone) AS items_today,
               (SELECT coalesce(sum(cost), 0) FROM editor.llm_usage
                 WHERE at >= date_trunc('month', now() AT TIME ZONE t.timezone) AT TIME ZONE t.timezone) AS llm_cost_month,
               (SELECT coalesce(sum(input_tokens), 0) + coalesce(sum(output_tokens), 0) FROM editor.llm_usage
                 WHERE at >= date_trunc('month', now() AT TIME ZONE t.timezone) AT TIME ZONE t.timezone) AS llm_tokens_month,
               (SELECT count(*) FROM editor.drafts
                 WHERE created_at >= date_trunc('month', now() AT TIME ZONE t.timezone) AT TIME ZONE t.timezone) AS drafts_month
        FROM editor.tenants t;
        GRANT SELECT ON editor.tenant_usage TO {APP_ROLE};
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DROP VIEW IF EXISTS editor.tenant_usage;
        CREATE OR REPLACE FUNCTION editor.active_tenants() RETURNS SETOF text
            LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, editor
            AS $$ SELECT tenant_id FROM editor.tenants ORDER BY tenant_id $$;
        GRANT SELECT, INSERT, UPDATE, DELETE ON editor.tenants TO {APP_ROLE};
        ALTER TABLE editor.tenants
            DROP COLUMN plan, DROP COLUMN max_sites, DROP COLUMN max_active_sources,
            DROP COLUMN max_items_per_day, DROP COLUMN max_llm_cost_month, DROP COLUMN max_drafts_month,
            DROP COLUMN timezone, DROP COLUMN briefing_hour, DROP COLUMN language,
            DROP COLUMN core_token_env, DROP COLUMN active;
        """
    )
