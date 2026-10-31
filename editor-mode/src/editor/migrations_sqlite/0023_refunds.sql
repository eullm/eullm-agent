-- Alembic revision 0034: a payment row can be a refund. SQLite cannot
-- change a CHECK: the table is made again with the new one.
CREATE TABLE editor.payments_new (
    id integer PRIMARY KEY,
    tenant_id text REFERENCES tenants ON DELETE SET NULL,
    subscription_id integer REFERENCES subscriptions ON DELETE SET NULL,
    provider text NOT NULL,
    provider_payment_id text UNIQUE,
    kind text NOT NULL CHECK (kind IN ('first', 'recurring', 'manual', 'refund')),
    amount_cents integer NOT NULL,
    currency text NOT NULL DEFAULT 'EUR',
    status text NOT NULL,
    note text NOT NULL DEFAULT '',
    created_at text NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f000', 'now')),
    paid_at text
);
INSERT INTO editor.payments_new SELECT id, tenant_id, subscription_id, provider, provider_payment_id, kind, amount_cents,
    currency, status, note, created_at, paid_at FROM editor.payments;
DROP TABLE editor.payments;
ALTER TABLE editor.payments_new RENAME TO payments;
