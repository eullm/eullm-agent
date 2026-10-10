"""Domain-first Editor Mode: dynamic source registry, site history, profile
origin and periodic reviews.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = ["site_posts", "site_analyses", "profile_reviews", "feedback"]


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.sources
            ADD COLUMN site_id bigint REFERENCES editor.sites ON DELETE CASCADE,
            ADD COLUMN status text NOT NULL DEFAULT 'active'
                CHECK (status IN ('candidate', 'active', 'suspended', 'rejected')),
            ADD COLUMN origin text NOT NULL DEFAULT 'manual'
                CHECK (origin IN ('manual', 'site_outbound', 'site_feed', 'autodiscovery', 'api_query', 'model_suggestion')),
            ADD COLUMN evidence jsonb NOT NULL DEFAULT '{}',
            ADD COLUMN evaluation jsonb NOT NULL DEFAULT '{}',
            ADD COLUMN score real,
            ADD COLUMN evaluated_at timestamptz,
            ADD COLUMN status_reason text,
            ADD COLUMN status_changed_at timestamptz NOT NULL DEFAULT now(),
            ADD COLUMN consecutive_errors integer NOT NULL DEFAULT 0,
            ADD COLUMN last_item_at timestamptz;
        ALTER TABLE editor.sources DROP CONSTRAINT sources_tenant_id_kind_url_key;
        CREATE UNIQUE INDEX sources_unique ON editor.sources
            (tenant_id, coalesce(site_id, 0), kind, url, (config::text));
        UPDATE editor.sources SET status = CASE WHEN enabled THEN 'active' ELSE 'suspended' END;
        ALTER TABLE editor.sources DROP COLUMN enabled;

        -- What the analysed site itself has published: the history used to
        -- avoid proposing what it already covered.
        CREATE TABLE editor.site_posts (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            url text NOT NULL,
            url_hash text NOT NULL,
            title text NOT NULL,
            summary text NOT NULL DEFAULT '',
            categories text[] NOT NULL DEFAULT '{}',
            published_at timestamptz,
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            minhash bigint[] NOT NULL,
            UNIQUE (site_id, url_hash)
        );
        CREATE INDEX site_posts_published ON editor.site_posts (site_id, published_at DESC);

        -- Every analysis of a site, complete or not, with what it found.
        CREATE TABLE editor.site_analyses (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            started_at timestamptz NOT NULL DEFAULT now(),
            status text NOT NULL CHECK (status IN ('complete', 'partial', 'insufficient', 'failed')),
            snapshot jsonb NOT NULL,
            problems text[] NOT NULL DEFAULT '{}',
            pages_fetched integer NOT NULL DEFAULT 0
        );

        ALTER TABLE editor.editorial_profiles
            ADD COLUMN origin text NOT NULL DEFAULT 'manual'
                CHECK (origin IN ('analysis', 'reanalysis', 'manual')),
            ADD COLUMN analysis_id bigint REFERENCES editor.site_analyses,
            ADD COLUMN based_on integer,
            ADD COLUMN changes jsonb NOT NULL DEFAULT '[]';

        -- Periodic re-reading of the site against the approved profile.
        CREATE TABLE editor.profile_reviews (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            reviewed_at timestamptz NOT NULL DEFAULT now(),
            analysis_id bigint REFERENCES editor.site_analyses,
            drift jsonb NOT NULL,
            significant boolean NOT NULL,
            proposed_profile_id bigint REFERENCES editor.editorial_profiles
        );

        -- What people said about proposals, topics and sources.
        CREATE TABLE editor.feedback (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint REFERENCES editor.sites ON DELETE CASCADE,
            target text NOT NULL CHECK (target IN ('proposal', 'topic', 'source', 'draft')),
            target_id bigint NOT NULL,
            verdict text NOT NULL CHECK (verdict IN ('up', 'down', 'already_covered', 'off_topic')),
            note text,
            given_by text NOT NULL,
            given_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    for table in TABLES:
        op.execute(tenant_rls(table))
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS editor.{table} CASCADE")
    op.execute(
        """
        ALTER TABLE editor.editorial_profiles
            DROP COLUMN origin, DROP COLUMN analysis_id, DROP COLUMN based_on, DROP COLUMN changes;
        ALTER TABLE editor.sources ADD COLUMN enabled boolean NOT NULL DEFAULT true;
        UPDATE editor.sources SET enabled = (status = 'active');
        DROP INDEX editor.sources_unique;
        ALTER TABLE editor.sources
            DROP COLUMN site_id, DROP COLUMN status, DROP COLUMN origin, DROP COLUMN evidence,
            DROP COLUMN evaluation, DROP COLUMN score, DROP COLUMN evaluated_at,
            DROP COLUMN status_reason, DROP COLUMN status_changed_at,
            DROP COLUMN consecutive_errors, DROP COLUMN last_item_at,
            ADD CONSTRAINT sources_tenant_id_kind_url_key UNIQUE (tenant_id, kind, url);
        """
    )
