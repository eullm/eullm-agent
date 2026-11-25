"""Videos as a service of their own (Francesco, 2026-11-25: "un servizio
scollato venduto a parte che si può integrare con gli articoli e vice
versa"):

- sites.kind: 'web' (a site with articles) or 'social' (a channel with no
  blog: Instagram, TikTok, YouTube Shorts), sites.handle its account;
- plans/tenants.max_videos_month: videos included every month (NULL: no
  limit, the local installation); the plans already sold include none, so
  their videos are paid in credits;
- (IF NOT EXISTS: test_migrations builds rows of an older schema with
  today's code, which reads these columns);
- videos.included: made within the month's videos of the plan; videos.hype,
  videos.gain: the Hype of the trend and the points it gained in the last
  hours when the idea was proposed; videos.auto: proposed by the morning
  round.

Revision ID: 0051
Revises: 0050
"""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.sites ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'web' CHECK (kind IN ('web', 'social'));
        ALTER TABLE editor.sites ADD COLUMN IF NOT EXISTS handle text;
        ALTER TABLE editor.plans ADD COLUMN max_videos_month integer CHECK (max_videos_month >= 0);
        ALTER TABLE editor.tenants ADD COLUMN IF NOT EXISTS max_videos_month integer CHECK (max_videos_month >= 0);
        UPDATE editor.plans SET max_videos_month = 0;
        UPDATE editor.tenants SET max_videos_month = 0 WHERE plan IS NOT NULL AND plan <> '';
        ALTER TABLE editor.videos ADD COLUMN included boolean NOT NULL DEFAULT false;
        ALTER TABLE editor.videos ADD COLUMN hype real;
        ALTER TABLE editor.videos ADD COLUMN gain real;
        ALTER TABLE editor.videos ADD COLUMN auto boolean NOT NULL DEFAULT false;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.videos DROP COLUMN auto;
        ALTER TABLE editor.videos DROP COLUMN gain;
        ALTER TABLE editor.videos DROP COLUMN hype;
        ALTER TABLE editor.videos DROP COLUMN included;
        ALTER TABLE editor.tenants DROP COLUMN max_videos_month;
        ALTER TABLE editor.plans DROP COLUMN max_videos_month;
        ALTER TABLE editor.sites DROP COLUMN handle;
        ALTER TABLE editor.sites DROP COLUMN kind;
        """
    )
