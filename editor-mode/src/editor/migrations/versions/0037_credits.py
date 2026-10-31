"""Credits: what goes beyond the plan (articles over the month's quota,
translations over it, cover regenerations after the free ones) is paid in
credits. The plan gives credits every month; packs bought with Mollie or
by bank transfer add credits that do not expire.

- plans.credits_month, tenants.credits_month: the month's credits;
- tenants.credits_alert: the last alert sent ("YYYY-MM:80" / "YYYY-MM:100");
- credit_prices: what each action costs, changed from the console;
- credit_packs: the packs on sale, changed from the console;
- credit_ledger: every movement of the newsroom (pool 'month' for the
  month's credits, 'extra' for packs and credits given by hand).

Revision ID: 0037
Revises: 0036
"""

from alembic import op

from editor.migrations.rls import APP_ROLE, tenant_rls

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE editor.plans ADD COLUMN credits_month integer CHECK (credits_month >= 0);
        ALTER TABLE editor.tenants ADD COLUMN credits_month integer CHECK (credits_month >= 0);
        ALTER TABLE editor.tenants ADD COLUMN credits_alert text;
        UPDATE editor.plans SET credits_month = CASE code
            WHEN 'trial' THEN 30 WHEN 'solo' THEN 100 WHEN 'starter' THEN 250 WHEN 'pro' THEN 800
            WHEN 'business' THEN 2500 WHEN 'agency' THEN 8000 ELSE 0 END;
        UPDATE editor.tenants t SET credits_month = p.credits_month FROM editor.plans p WHERE p.code = t.plan;

        CREATE TABLE editor.credit_prices (
            action text PRIMARY KEY CHECK (action IN ('article', 'translation', 'image')),
            credits integer NOT NULL CHECK (credits >= 0)
        );
        INSERT INTO editor.credit_prices (action, credits) VALUES ('article', 25), ('translation', 10), ('image', 3);
        CREATE TABLE editor.credit_packs (
            code text PRIMARY KEY CHECK (code ~ '^[a-z0-9-]{{1,40}}$'),
            name text NOT NULL,
            credits integer NOT NULL CHECK (credits > 0),
            price_cents integer NOT NULL CHECK (price_cents >= 0),
            currency text NOT NULL DEFAULT 'EUR',
            active boolean NOT NULL DEFAULT true,
            sort integer NOT NULL DEFAULT 0
        );
        INSERT INTO editor.credit_packs (code, name, credits, price_cents, sort) VALUES
            ('small', '500 crediti', 500, 500, 0),
            ('medium', '2.000 crediti', 2000, 1800, 1),
            ('large', '6.000 crediti', 6000, 4800, 2);
        GRANT SELECT ON editor.credit_prices, editor.credit_packs TO {APP_ROLE};

        CREATE TABLE editor.credit_ledger (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES editor.tenants,
            at timestamptz NOT NULL DEFAULT now(),
            pool text NOT NULL CHECK (pool IN ('month', 'extra')),
            delta integer NOT NULL,
            reason text NOT NULL,
            ref text,
            note text NOT NULL DEFAULT '',
            by_user text
        );
        CREATE INDEX credit_ledger_tenant_at ON editor.credit_ledger (tenant_id, at);
        CREATE INDEX credit_ledger_ref ON editor.credit_ledger (tenant_id, reason, ref);
        ALTER TABLE editor.payments DROP CONSTRAINT payments_kind_check;
        ALTER TABLE editor.payments ADD CONSTRAINT payments_kind_check
            CHECK (kind IN ('first', 'recurring', 'manual', 'refund', 'credits'));
        """
    )
    op.execute(tenant_rls("credit_ledger"))
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA editor TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM editor.payments WHERE kind = 'credits';
        ALTER TABLE editor.payments DROP CONSTRAINT payments_kind_check;
        ALTER TABLE editor.payments ADD CONSTRAINT payments_kind_check
            CHECK (kind IN ('first', 'recurring', 'manual', 'refund'));
        DROP TABLE editor.credit_ledger;
        DROP TABLE editor.credit_packs;
        DROP TABLE editor.credit_prices;
        ALTER TABLE editor.tenants DROP COLUMN credits_alert;
        ALTER TABLE editor.tenants DROP COLUMN credits_month;
        ALTER TABLE editor.plans DROP COLUMN credits_month;
        """
    )
