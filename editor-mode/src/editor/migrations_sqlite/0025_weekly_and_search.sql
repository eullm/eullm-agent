-- Alembic revision 0036: the weekly report, Google Search Console per site.
ALTER TABLE editor.tenants ADD COLUMN weekly_report boolean NOT NULL DEFAULT 1;
ALTER TABLE editor.sites ADD COLUMN gsc_property text;
ALTER TABLE editor.sites ADD COLUMN gsc_status text;
ALTER TABLE editor.sites ADD COLUMN gsc_checked_at text;
CREATE TABLE editor.weekly_reports (
    id integer PRIMARY KEY AUTOINCREMENT,
    tenant_id text NOT NULL REFERENCES tenants,
    week_start text NOT NULL,
    body_md text NOT NULL,
    body_html text NOT NULL,
    sent_to text,
    sent_at text,
    send_error text,
    created_at text NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f000', 'now')),
    UNIQUE (tenant_id, week_start)
);
CREATE TABLE editor.search_stats (
    id integer PRIMARY KEY AUTOINCREMENT,
    tenant_id text NOT NULL REFERENCES tenants,
    site_id integer NOT NULL REFERENCES sites ON DELETE CASCADE,
    day text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('page', 'query')),
    key text NOT NULL,
    clicks integer NOT NULL DEFAULT 0,
    impressions integer NOT NULL DEFAULT 0,
    position real,
    UNIQUE (site_id, day, kind, key)
);
CREATE INDEX editor.search_stats_site_day ON search_stats (site_id, day);
