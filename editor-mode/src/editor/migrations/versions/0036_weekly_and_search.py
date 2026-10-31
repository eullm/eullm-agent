"""The weekly report to the newsroom, and Google Search Console per site:
the property, its last check and the clicks of the site's pages and queries.

Revision ID: 0036
Revises: 0035
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.tenants ADD COLUMN weekly_report boolean NOT NULL DEFAULT true;
        ALTER TABLE editor.sites ADD COLUMN gsc_property text;
        ALTER TABLE editor.sites ADD COLUMN gsc_status text;
        ALTER TABLE editor.sites ADD COLUMN gsc_checked_at timestamptz;
        CREATE TABLE editor.weekly_reports (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            week_start date NOT NULL,
            body_md text NOT NULL,
            body_html text NOT NULL,
            sent_to jsonb,
            sent_at timestamptz,
            send_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, week_start)
        );
        CREATE TABLE editor.search_stats (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            day date NOT NULL,
            kind text NOT NULL CHECK (kind IN ('page', 'query')),
            key text NOT NULL,
            clicks integer NOT NULL DEFAULT 0,
            impressions integer NOT NULL DEFAULT 0,
            position real,
            UNIQUE (site_id, day, kind, key)
        );
        CREATE INDEX search_stats_site_day ON editor.search_stats (site_id, day);
        """
    )
    op.execute(tenant_rls("weekly_reports"))
    op.execute(tenant_rls("search_stats"))
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE editor.search_stats;
        DROP TABLE editor.weekly_reports;
        ALTER TABLE editor.sites DROP COLUMN gsc_checked_at;
        ALTER TABLE editor.sites DROP COLUMN gsc_status;
        ALTER TABLE editor.sites DROP COLUMN gsc_property;
        ALTER TABLE editor.tenants DROP COLUMN weekly_report;
        """
    )
