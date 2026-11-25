"""Videos for the social networks (Instagram Reels, TikTok, YouTube
Shorts): ideas written from an article or a trend of the moment, then, when
a person asks, a vertical clip with audio made by a video model through
OpenRouter (videos.py). The newsroom downloads it and posts it itself.
A video made is paid in credits (credit_prices 'video').

Revision ID: 0050
Revises: 0049
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE editor.videos (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            site_id bigint NOT NULL REFERENCES editor.sites ON DELETE CASCADE,
            draft_id bigint REFERENCES editor.drafts ON DELETE CASCADE,
            topic_id bigint,
            language text,
            title text NOT NULL,
            hook text NOT NULL DEFAULT '',
            scene text NOT NULL,
            voiceover text NOT NULL DEFAULT '',
            caption text NOT NULL DEFAULT '',
            hashtags jsonb NOT NULL DEFAULT '[]',
            status text NOT NULL DEFAULT 'idea'
                CHECK (status IN ('idea', 'queued', 'making', 'ready', 'failed')),
            model text,
            job_ref text,
            seconds integer,
            resolution text,
            cost double precision,
            credits integer,
            error text,
            mime text,
            content bytea,
            size_bytes integer,
            created_at timestamptz NOT NULL DEFAULT now(),
            made_at timestamptz,
            made_by text
        );
        CREATE INDEX videos_site ON editor.videos (tenant_id, site_id, created_at);
        CREATE INDEX videos_draft ON editor.videos (tenant_id, draft_id);

        ALTER TABLE editor.credit_prices DROP CONSTRAINT credit_prices_action_check;
        ALTER TABLE editor.credit_prices ADD CONSTRAINT credit_prices_action_check
            CHECK (action IN ('article', 'translation', 'image', 'video'));
        INSERT INTO editor.credit_prices (action, credits) VALUES ('video', 100) ON CONFLICT DO NOTHING;
        """
    )
    op.execute(tenant_rls("videos"))
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE editor.videos;
        DELETE FROM editor.credit_prices WHERE action = 'video';
        ALTER TABLE editor.credit_prices DROP CONSTRAINT credit_prices_action_check;
        ALTER TABLE editor.credit_prices ADD CONSTRAINT credit_prices_action_check
            CHECK (action IN ('article', 'translation', 'image'));
        """
    )
