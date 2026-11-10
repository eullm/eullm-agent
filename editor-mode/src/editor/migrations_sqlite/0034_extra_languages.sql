-- Alembic revision 0045: extra languages bought one at a time (addons,
-- tenants.extra_languages by site, payments.kind 'addon').
CREATE TABLE editor.addons (
    code text PRIMARY KEY,
    name text NOT NULL,
    price_month_cents integer CHECK (price_month_cents >= 0),
    price_year_cents integer CHECK (price_year_cents >= 0),
    currency text NOT NULL DEFAULT 'EUR'
);
INSERT INTO editor.addons (code, name, price_month_cents, price_year_cents) VALUES ('language', 'Lingua extra', 1250, 12500);
ALTER TABLE editor.tenants ADD COLUMN extra_languages text;
CREATE TABLE editor.payments_new (
    id integer PRIMARY KEY,
    tenant_id text REFERENCES tenants ON DELETE SET NULL,
    subscription_id integer REFERENCES subscriptions ON DELETE SET NULL,
    provider text NOT NULL,
    provider_payment_id text UNIQUE,
    kind text NOT NULL CHECK (kind IN ('first', 'recurring', 'manual', 'refund', 'credits', 'addon')),
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
