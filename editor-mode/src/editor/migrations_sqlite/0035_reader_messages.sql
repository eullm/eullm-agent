-- Alembic revision 0046: feedback from readers (sites.feedback, reader_messages).
ALTER TABLE editor.sites ADD COLUMN feedback boolean NOT NULL DEFAULT 0;
CREATE TABLE editor.reader_messages (
    id integer PRIMARY KEY AUTOINCREMENT,
    tenant_id text NOT NULL REFERENCES tenants,
    site_id integer NOT NULL REFERENCES sites ON DELETE CASCADE,
    url text NOT NULL DEFAULT '',
    title text NOT NULL DEFAULT '',
    kind text NOT NULL CHECK (kind IN ('error', 'comment', 'other')),
    message text NOT NULL CHECK (length(message) <= 2000),
    email text NOT NULL,
    language text,
    status text NOT NULL CHECK (status IN ('pending', 'new', 'read', 'closed')),
    token_sha256 text UNIQUE,
    address_hash text,
    created_at text NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f000', 'now')),
    confirmed_at text
);
CREATE INDEX editor.reader_messages_site ON reader_messages (tenant_id, site_id, status, created_at);
