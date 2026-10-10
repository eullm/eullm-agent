"""Collection and trends: tenants, sites, sources, items, topics, scores.

Revision ID: 0001
Revises:
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = [
    "tenants",
    "sites",
    "sources",
    "source_items",
    "observations",
    "topics",
    "topic_items",
    "trend_scores",
    "llm_usage",
]


def upgrade() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA editor TO {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA editor GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE};

        CREATE TABLE editor.tenants (
            tenant_id text PRIMARY KEY,
            name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE editor.sites (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            domain text NOT NULL,
            name text NOT NULL,
            language text NOT NULL DEFAULT 'it',
            UNIQUE (tenant_id, domain)
        );

        CREATE TABLE editor.sources (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            kind text NOT NULL CHECK (kind IN ('rss', 'hackernews', 'github', 'huggingface', 'arxiv')),
            name text NOT NULL,
            url text NOT NULL,
            config jsonb NOT NULL DEFAULT '{{}}',
            weight real NOT NULL DEFAULT 1.0,
            enabled boolean NOT NULL DEFAULT true,
            sites text[] NOT NULL DEFAULT '{{}}',
            etag text,
            last_modified text,
            last_fetched_at timestamptz,
            last_error text,
            UNIQUE (tenant_id, kind, url)
        );

        CREATE TABLE editor.source_items (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            source_id bigint NOT NULL REFERENCES editor.sources ON DELETE CASCADE,
            external_id text,
            url text NOT NULL,
            canonical_url text NOT NULL,
            url_hash text NOT NULL,
            content_hash text NOT NULL,
            title text NOT NULL,
            summary text NOT NULL DEFAULT '',
            author text,
            published_at timestamptz,
            fetched_at timestamptz NOT NULL DEFAULT now(),
            metrics jsonb NOT NULL DEFAULT '{{}}',
            minhash bigint[] NOT NULL,
            duplicate_of bigint REFERENCES editor.source_items,
            UNIQUE (tenant_id, url_hash)
        );
        CREATE INDEX source_items_published ON editor.source_items (tenant_id, published_at DESC);
        CREATE INDEX source_items_content ON editor.source_items (tenant_id, content_hash);

        CREATE TABLE editor.observations (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            item_id bigint NOT NULL REFERENCES editor.source_items ON DELETE CASCADE,
            observed_at timestamptz NOT NULL DEFAULT now(),
            metrics jsonb NOT NULL
        );
        CREATE INDEX observations_item ON editor.observations (item_id, observed_at);

        CREATE TABLE editor.topics (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            label text NOT NULL,
            keywords text[] NOT NULL DEFAULT '{{}}',
            summary text NOT NULL DEFAULT '',
            labelled_by text NOT NULL DEFAULT 'keywords',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE editor.topic_items (
            tenant_id text NOT NULL REFERENCES editor.tenants,
            topic_id bigint NOT NULL REFERENCES editor.topics ON DELETE CASCADE,
            item_id bigint NOT NULL REFERENCES editor.source_items ON DELETE CASCADE,
            PRIMARY KEY (topic_id, item_id)
        );
        CREATE UNIQUE INDEX topic_items_one_topic ON editor.topic_items (item_id);

        CREATE TABLE editor.trend_scores (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            topic_id bigint NOT NULL REFERENCES editor.topics ON DELETE CASCADE,
            computed_at timestamptz NOT NULL DEFAULT now(),
            formula_version text NOT NULL,
            score real NOT NULL CHECK (score BETWEEN 0 AND 100),
            components jsonb NOT NULL,
            missing text[] NOT NULL DEFAULT '{{}}'
        );
        CREATE INDEX trend_scores_topic ON editor.trend_scores (topic_id, computed_at DESC);

        CREATE TABLE editor.llm_usage (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            purpose text NOT NULL,
            model text NOT NULL,
            input_tokens integer,
            output_tokens integer,
            cost double precision,
            at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    for table in TABLES:
        op.execute(tenant_rls(table))
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS editor.{table} CASCADE")
