-- Alembic revision 0037: credits (month's credits in the plan, price list,
-- packs, the newsroom's movements); a payment row can buy credits.
ALTER TABLE editor.plans ADD COLUMN credits_month integer CHECK (credits_month >= 0);
ALTER TABLE editor.tenants ADD COLUMN credits_month integer CHECK (credits_month >= 0);
ALTER TABLE editor.tenants ADD COLUMN credits_alert text;
UPDATE editor.plans SET credits_month = CASE code
    WHEN 'trial' THEN 30 WHEN 'solo' THEN 100 WHEN 'starter' THEN 250 WHEN 'pro' THEN 800
    WHEN 'business' THEN 2500 WHEN 'agency' THEN 8000 ELSE 0 END;
UPDATE editor.tenants SET credits_month = (SELECT p.credits_month FROM plans p WHERE p.code = tenants.plan);
CREATE TABLE editor.credit_prices (
    action text PRIMARY KEY CHECK (action IN ('article', 'translation', 'image')),
    credits integer NOT NULL CHECK (credits >= 0)
);
INSERT INTO editor.credit_prices (action, credits) VALUES ('article', 25), ('translation', 10), ('image', 3);
CREATE TABLE editor.credit_packs (
    code text PRIMARY KEY,
    name text NOT NULL,
    credits integer NOT NULL CHECK (credits > 0),
    price_cents integer NOT NULL CHECK (price_cents >= 0),
    currency text NOT NULL DEFAULT 'EUR',
    active boolean NOT NULL DEFAULT 1,
    sort integer NOT NULL DEFAULT 0
);
INSERT INTO editor.credit_packs (code, name, credits, price_cents, sort) VALUES
    ('small', '500 crediti', 500, 500, 0),
    ('medium', '2.000 crediti', 2000, 1800, 1),
    ('large', '6.000 crediti', 6000, 4800, 2);
CREATE TABLE editor.credit_ledger (
    id integer PRIMARY KEY AUTOINCREMENT,
    tenant_id text NOT NULL REFERENCES tenants,
    at text NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f000', 'now')),
    pool text NOT NULL CHECK (pool IN ('month', 'extra')),
    delta integer NOT NULL,
    reason text NOT NULL,
    ref text,
    note text NOT NULL DEFAULT '',
    by_user text
);
CREATE INDEX editor.credit_ledger_tenant_at ON credit_ledger (tenant_id, at);
CREATE INDEX editor.credit_ledger_ref ON credit_ledger (tenant_id, reason, ref);
CREATE TABLE editor.payments_new (
    id integer PRIMARY KEY,
    tenant_id text REFERENCES tenants ON DELETE SET NULL,
    subscription_id integer REFERENCES subscriptions ON DELETE SET NULL,
    provider text NOT NULL,
    provider_payment_id text UNIQUE,
    kind text NOT NULL CHECK (kind IN ('first', 'recurring', 'manual', 'refund', 'credits')),
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
