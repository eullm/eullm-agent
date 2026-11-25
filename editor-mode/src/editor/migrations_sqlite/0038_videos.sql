-- Alembic revision 0050: videos for the social networks (videos.py), paid in credits ('video').
CREATE TABLE editor.videos (
    id integer PRIMARY KEY AUTOINCREMENT,
    tenant_id text NOT NULL REFERENCES tenants,
    site_id integer NOT NULL REFERENCES sites ON DELETE CASCADE,
    draft_id integer REFERENCES drafts ON DELETE CASCADE,
    topic_id integer,
    language text,
    title text NOT NULL,
    hook text NOT NULL DEFAULT '',
    scene text NOT NULL,
    voiceover text NOT NULL DEFAULT '',
    caption text NOT NULL DEFAULT '',
    hashtags text NOT NULL DEFAULT '[]',
    status text NOT NULL DEFAULT 'idea' CHECK (status IN ('idea', 'queued', 'making', 'ready', 'failed')),
    model text,
    job_ref text,
    seconds integer,
    resolution text,
    cost real,
    credits integer,
    error text,
    mime text,
    content blob,
    size_bytes integer,
    created_at text NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f000', 'now')),
    made_at text,
    made_by text
);
CREATE INDEX editor.videos_site ON videos (tenant_id, site_id, created_at);
CREATE INDEX editor.videos_draft ON videos (tenant_id, draft_id);
CREATE TABLE editor.credit_prices_new (
    action text PRIMARY KEY CHECK (action IN ('article', 'translation', 'image', 'video')),
    credits integer NOT NULL CHECK (credits >= 0)
);
INSERT INTO editor.credit_prices_new SELECT action, credits FROM editor.credit_prices;
DROP TABLE editor.credit_prices;
ALTER TABLE editor.credit_prices_new RENAME TO credit_prices;
INSERT OR IGNORE INTO editor.credit_prices (action, credits) VALUES ('video', 100);
