"""A language more for a site, bought one at a time (Francesco 2026-11-10:
12,50 € a month or 125 € a year, before VAT): what is left of the paid
period now, then with the plan's renewals at the gateway."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from editor import accounts, billing, repo, translate as tr
from editor.db import Database

from test_billing import FakeGateway, client, key, signup, unique



def G(net: int) -> int:
    """What an Italian customer pays: the price and 22% VAT."""
    return round(net * 1.22)

class Gateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.updates = []

    def one_off_payment(self, customer_id, amount_cents, currency, description, redirect_url, webhook_url, metadata):
        self.n += 1
        pid = f"tr_{self.run}{self.n}"
        self.payments[pid] = {"id": pid, "status": "open", "amount_cents": amount_cents, "currency": currency,
                              "metadata": metadata, "mandate_id": None, "subscription_id": None,
                              "description": description}
        self.last = {"redirect": redirect_url, "webhook": webhook_url}
        return pid, f"https://pay.example/{pid}"

    def update_subscription(self, customer_id, subscription_id, amount_cents, currency):
        self.subscriptions[subscription_id]["amount"] = amount_cents
        self.updates.append(amount_cents)


@pytest.fixture
def admin(db):
    a = Database.from_url(db.admin_url)
    yield a
    a.engine.dispose()


def paying_owner(db, fake, plan="pro", interval="year"):
    """A newsroom on a paid plan, renewed by card at the gateway, with a site."""
    c, sent = client(db, gateway=fake)
    owner = f"{unique('amm')}@rossi.it"
    tid = signup(c, key(db), unique("web"), owner).json()["tenant"]["tenant_id"]
    invite = re.search(r"https?://\S+/invite/\S+", sent[0][2]).group(0)
    c.post(f"/invite/{invite.rsplit('/', 1)[1]}", data={"password": "password del capo", "again": "password del capo"})
    with db.tenant(tid) as s:
        site_id = repo.add_site(s, tid, f"{unique('sito')}.example")
    return c, tid, site_id


def activate(c, fake, tid, plan="pro", interval="year"):
    c.post(f"/account/{tid}/checkout", data={"plan": plan, "interval": interval}, follow_redirects=False)
    pid = f"tr_{fake.run}{fake.n}"
    fake.payments[pid]["status"] = "paid"
    c.post("/api/billing/mollie/webhook", data={"id": pid})


def test_a_language_more_is_bought_for_a_site_and_renews_with_the_plan(db, admin):
    fake = Gateway()
    c, tid, site_id = paying_owner(db, fake)
    # On trial the language cannot be bought: the plan comes first.
    offer = billing.language_offer(admin, tid)
    assert not offer["ok"] and "piano" in offer["why"]
    activate(c, fake, tid)
    sub = billing.subscription(admin, tid)
    msub = sub["provider_subscription_id"]
    assert fake.subscriptions[msub]["amount"] == G(149000)
    with db.tenant(tid) as s:
        assert tr.allowed(s, site_id) == 2  # Pro: two languages per site, its own included
    with pytest.raises(tr.LanguageError):
        tr.set_languages(db, tid, site_id, ["en", "de"])
    page = c.get("/site?tab=publish")
    assert "Lingue extra" in page.text and "12,50 €" in page.text and "125 €" in page.text
    assert f'action="/account/{tid}/language"' in page.text
    # A year just started: today almost the whole year's price, then with the plan.
    r = c.post(f"/account/{tid}/language", data={"site_id": site_id, "back": "site"}, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    assert r.status_code == 303 and G(12400) <= fake.payments[pid]["amount_cents"] <= G(12500)
    assert fake.last["redirect"] == f"https://editor.example.com/account/{tid}/language-paid?back=site"
    c.post("/api/billing/mollie/webhook", data={"id": pid})  # not paid yet: nothing
    assert billing.extra_languages(admin, tid) == {}
    fake.payments[pid]["status"] = "paid"
    c.post("/api/billing/mollie/webhook", data={"id": pid})
    c.post("/api/billing/mollie/webhook", data={"id": pid})  # the same notice twice: one language
    assert billing.extra_languages(admin, tid) == {site_id: 1}
    assert fake.subscriptions[msub]["amount"] == G(149000 + 12500)
    with db.tenant(tid) as s:
        assert tr.allowed(s, site_id) == 3 and tr.allowed(s) == 2
    assert tr.set_languages(db, tid, site_id, ["en", "de"]) == ["en", "de"]
    # Removing it waits until the site has a language less.
    r = c.post(f"/account/{tid}/language/remove", data={"site_id": site_id, "back": "site"}, follow_redirects=False)
    assert r.headers["location"] == "/site?tab=publish#h-languages"
    assert billing.extra_languages(admin, tid) == {site_id: 1}
    tr.set_languages(db, tid, site_id, ["en"])
    c.post(f"/account/{tid}/language/remove", data={"site_id": site_id, "back": "site"})
    assert billing.extra_languages(admin, tid) == {}
    assert fake.subscriptions[msub]["amount"] == G(149000)


def test_a_plan_paid_again_keeps_its_extra_languages_in_the_amount(db, admin):
    fake = Gateway()
    c, tid, site_id = paying_owner(db, fake)
    activate(c, fake, tid, "pro", "month")
    billing.set_extra_languages(admin, tid, site_id, 2, fake)
    msub = billing.subscription(admin, tid)["provider_subscription_id"]
    assert fake.subscriptions[msub]["amount"] == G(14900 + 2 * 1250)
    # A change of plan: the first payment and the renewals carry the languages.
    c.post(f"/account/{tid}/checkout", data={"plan": "business", "interval": "month"}, follow_redirects=False)
    pid = f"tr_{fake.run}{fake.n}"
    assert fake.payments[pid]["amount_cents"] == G(39900 + 2 * 1250)
    fake.payments[pid]["status"] = "paid"
    c.post("/api/billing/mollie/webhook", data={"id": pid})
    new = billing.subscription(admin, tid)["provider_subscription_id"]
    assert new != msub and fake.subscriptions[new]["amount"] == G(39900 + 2 * 1250)


def test_the_price_is_prorated_on_what_is_left_of_the_period():
    end = datetime(2026, 12, 10, tzinfo=UTC)
    assert billing.prorated(1250, end, "month", end - timedelta(days=30)) == 1250
    assert 600 <= billing.prorated(1250, end, "month", end - timedelta(days=15)) <= 640
    assert billing.prorated(1250, end, "month", end - timedelta(minutes=1)) == 1


def test_the_console_sets_the_price_and_the_site_shows_it(db, admin):
    billing.save_addon(admin, "language", 1500, 15000)
    try:
        assert billing.addons(admin)[0]["price_month_cents"] == 1500
        c, _ = client(db)
        body = c.get("/api/public/plans").json()
        assert body["addons"][0]["code"] == "language" and body["addons"][0]["price_year_cents"] == 15000
        with pytest.raises(billing.BillingError):
            billing.save_addon(admin, "language", -1, None)
    finally:
        billing.save_addon(admin, "language", 1250, 12500)
