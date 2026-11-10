"""Extra languages (Francesco 2026-11-10): a site can have more languages
than its plan allows by buying them one at a time, 12,50 € a month or
125 € a year each (prices before VAT, changed from the console).
- addons: the add-ons on sale and their prices (only "language" for now);
- tenants.extra_languages: the languages bought, by site ({"<site id>": n});
- payments.kind 'addon': the first payment of an add-on (the renewals go
  with the plan's, whose amount grows by the add-on's price).
VAT on online payments (Francesco 2026-11-10, 22% except EU businesses
with a VAT number, reverse charge):
- payments.vat_cents: the VAT inside a payment by card;
- subscriptions.renewal_cents: what the gateway charges each renewal, VAT
  included (NULL until the hourly round brings it there).

Revision ID: 0045
Revises: 0044
"""

from alembic import op

from editor.migrations.rls import APP_ROLE

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE editor.addons (
            code text PRIMARY KEY CHECK (code ~ '^[a-z0-9-]{{1,40}}$'),
            name text NOT NULL,
            price_month_cents integer CHECK (price_month_cents >= 0),
            price_year_cents integer CHECK (price_year_cents >= 0),
            currency text NOT NULL DEFAULT 'EUR'
        );
        INSERT INTO editor.addons (code, name, price_month_cents, price_year_cents)
            VALUES ('language', 'Lingua extra', 1250, 12500);
        GRANT SELECT ON editor.addons TO {APP_ROLE};
        ALTER TABLE editor.tenants ADD COLUMN extra_languages jsonb;
        ALTER TABLE editor.payments DROP CONSTRAINT payments_kind_check;
        ALTER TABLE editor.payments ADD CONSTRAINT payments_kind_check
            CHECK (kind IN ('first', 'recurring', 'manual', 'refund', 'credits', 'addon'));
        ALTER TABLE editor.payments ADD COLUMN vat_cents integer;
        ALTER TABLE editor.subscriptions ADD COLUMN renewal_cents integer;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE editor.subscriptions DROP COLUMN renewal_cents;
        ALTER TABLE editor.payments DROP COLUMN vat_cents;
        DELETE FROM editor.payments WHERE kind = 'addon';
        ALTER TABLE editor.payments DROP CONSTRAINT payments_kind_check;
        ALTER TABLE editor.payments ADD CONSTRAINT payments_kind_check
            CHECK (kind IN ('first', 'recurring', 'manual', 'refund', 'credits'));
        ALTER TABLE editor.tenants DROP COLUMN extra_languages;
        DROP TABLE editor.addons;
        """
    )
