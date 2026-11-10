"""VAT on what the gateway charges (Francesco 2026-11-10): Italian VAT at
22% on the list price, none for a business with a VAT number in another EU
country (reverse charge); the renewals at the gateway follow the customer."""

from sqlalchemy import update

from editor import billing
from editor import models as m

from test_billing import CUSTOMER
from test_extra_languages import G, Gateway, activate, admin, paying_owner  # noqa: F401 (fixture)


def test_who_pays_the_vat():
    assert billing.vat_rate(None) == 22
    assert billing.vat_rate({"country": "IT", "vat_number": "01234567890"}) == 22
    assert billing.vat_rate({"country": "DE", "vat_number": "123456789"}) == 0  # reverse charge
    assert billing.vat_rate({"country": "DE", "vat_number": ""}) == 22  # a private person
    assert billing.vat_rate({"country": "US", "vat_number": "12-3456789"}) == 22
    assert billing.with_vat(14900, None) == (18178, 3278)
    assert billing.with_vat(14900, {"country": "FR", "vat_number": "12345678901"}) == (14900, 0)
    assert billing.vat_in(18178, None) == 3278 and billing.vat_in(14900, {"country": "FR", "vat_number": "1"}) == 0


def test_the_renewals_follow_the_billing_data_and_old_ones_are_brought_to_the_vat(db, admin):
    fake = Gateway()
    c, tid, _ = paying_owner(db, fake)
    activate(c, fake, tid, plan="pro", interval="year")
    sub = billing.subscription(admin, tid)
    msub = sub["provider_subscription_id"]
    assert fake.subscriptions[msub]["amount"] == G(149000) and sub["renewal_cents"] == G(149000)
    page = c.get("/account").text
    assert "IVA al 22%" in page
    # The law wants consumers to see the price with VAT (Codice del Consumo art. 49): Pro, 149 € a month.
    assert "181,78 € IVA inclusa" in page and "+ IVA al mese" in page
    assert c.get("/api/public/plans").json()["vat_percent"] == 22
    # A German company with its VAT number: the renewals go on without VAT.
    de = CUSTOMER | {"country": "DE", "vat_number": "DE123456789", "tax_code": "", "province": "", "sdi_code": "",
                     "postal_code": "10115", "city": "Berlin"}
    r = c.post(f"/account/{tid}/customer", data=de, follow_redirects=False)
    assert r.status_code == 303
    assert fake.subscriptions[msub]["amount"] == 149000 and billing.subscription(admin, tid)["renewal_cents"] == 149000
    page = c.get("/account").text
    assert "inversione contabile" in page and "IVA inclusa" not in page
    # A subscription made before the VAT (no amount known): the hourly round fixes it, once.
    with admin.engine.begin() as conn:
        conn.execute(update(m.subscriptions).where(m.subscriptions.c.tenant_id == tid).values(renewal_cents=None))
    c.post(f"/account/{tid}/customer", data=CUSTOMER, follow_redirects=False)  # back to Italy
    with admin.engine.begin() as conn:
        conn.execute(update(m.subscriptions).where(m.subscriptions.c.tenant_id == tid).values(renewal_cents=None))
    fake.subscriptions[msub]["amount"] = 149000
    n = len(fake.updates)
    assert tid in billing.sync_renewals(admin, fake)
    assert fake.subscriptions[msub]["amount"] == G(149000) and len(fake.updates) == n + 1
    assert tid not in billing.sync_renewals(admin, fake) and len(fake.updates) == n + 1
