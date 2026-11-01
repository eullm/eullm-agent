"""Credits: what goes beyond the plan (articles and translations over the
month's quota, covers redrawn after the free ones) is paid with the month's
credits first, then with the extra ones of packs bought or recorded."""

import re
import pytest
import respx
from sqlalchemy import func, insert, select, text

from editor import accounts, billing, credits, images, models as m, quotas
from editor.db import Database

from conftest import set_plan
from test_billing import FakeGateway, client, key, signup, unique
from test_images import fake_bfl, imager
from test_publishing import approved_draft


@pytest.fixture
def admin(db):
    a = Database.from_url(db.admin_url)
    yield a
    a.engine.dispose()


def fill_drafts(db, tenant, n):
    """n drafts this month, as if written."""
    did, site_id = approved_draft(db, tenant)
    with db.tenant(tenant) as s:
        prop = s.execute(select(m.drafts.c.proposal_id).where(m.drafts.c.id == did)).scalar()
        have = s.execute(select(func.count()).select_from(m.drafts)).scalar()
        for i in range(max(0, n - have)):
            s.execute(insert(m.drafts).values(tenant_id=tenant, proposal_id=prop, site_id=site_id, version=10 + i,
                                              title="x", body_md="x", status="draft", generated_by="model"))
    return did


def test_without_credits_nothing_changes(db, tenant):
    """A newsroom without monthly credits (local installation, no plan):
    covers are free and the plan's limit stops as before."""
    did = fill_drafts(db, tenant, 1)
    set_plan(db, tenant, max_drafts_month=1)
    with db.tenant(tenant) as s:
        assert credits.state(s) == {"enabled": False}
        assert credits.cover_cost(s, did) == 0
        with pytest.raises(quotas.QuotaExceeded, match="drafts limit reached"):
            quotas.check(s, "drafts", pay="article")
    with respx.mock() as router:
        for _ in range(5):
            fake_bfl(router)
            images.make(db, tenant, did, imager(), None)
    with db.tenant(tenant) as s:
        assert s.execute(select(func.count()).select_from(m.credit_ledger)).scalar() == 0


def test_articles_beyond_the_plan_are_paid_with_credits(db, tenant, admin):
    fill_drafts(db, tenant, 2)
    set_plan(db, tenant, max_drafts_month=2, credits_month=30)
    with db.tenant(tenant) as s:
        assert quotas.check(s, "drafts", pay="article") is True  # beyond the plan, the credits pay
        assert credits.spend(s, "article", "draft:1") == 25
    with db.tenant(tenant) as s:
        st = credits.state(s)
        assert (st["month_used"], st["month_left"], st["extra"]) == (25, 5, 0)
        # The automatic newsroom (no pay) stays within the plan.
        with pytest.raises(quotas.QuotaExceeded, match="drafts limit reached"):
            quotas.check(s, "drafts")
        # 5 left, an article costs 25.
        with pytest.raises(credits.NoCredits, match="25 needed, 5 left"):
            quotas.check(s, "drafts", pay="article")
    # A pack (bank transfer): the month's 5 go first, then 20 of the extra.
    billing.record_credits(admin, tenant, 500, 500, "op@example.com", "bonifico")
    with db.tenant(tenant) as s:
        quotas.check(s, "drafts", pay="article")
        credits.spend(s, "article", "draft:2")
    with db.tenant(tenant) as s:
        st = credits.state(s)
        assert (st["month_left"], st["extra"], st["left"]) == (0, 480, 480)
        assert sorted(x["delta"] for x in credits.ledger(s)) == [-25, -20, -5, 500]
    with admin.engine.connect() as conn:
        pay = conn.execute(select(m.payments).where(m.payments.c.tenant_id == tenant)).first()
    assert pay.kind == "credits" and pay.amount_cents == 500 and pay.status == "paid"


def test_three_covers_per_article_are_free_then_they_cost(db, tenant, admin):
    did = fill_drafts(db, tenant, 1)
    set_plan(db, tenant, credits_month=4)
    with respx.mock() as router:
        for _ in range(4):  # the first and two redraws free, the fourth 3 credits
            fake_bfl(router)
            images.make(db, tenant, did, imager(), None)
    with db.tenant(tenant) as s:
        assert credits.state(s)["month_used"] == 3
        assert credits.free_covers_left(s, did) == 0 and credits.cover_cost(s, did) == 3
    with db.tenant(tenant) as s, pytest.raises(credits.NoCredits):  # 1 left
        credits.need(s, credits.cover_cost(s, did))
    credits.grant(admin, tenant, 10, note="regalo")
    with respx.mock() as router:
        fake_bfl(router)
        images.make(db, tenant, did, imager(), None)
    with db.tenant(tenant) as s:
        st = credits.state(s)
        assert (st["month_left"], st["extra"]) == (0, 8)


def test_alerts_at_80_and_100_percent_once_a_month(db, tenant, admin):
    set_plan(db, tenant, credits_month=100, language="it")
    with db.tenant(tenant) as s:
        credits.spend(s, "image", credits=79)
    assert (tenant, 80) not in credits.alerts(admin)
    with db.tenant(tenant) as s:
        credits.spend(s, "image", credits=1)
    assert (tenant, 80) in credits.alerts(admin)
    assert all(t != tenant for t, _ in credits.alerts(admin))  # not twice
    with db.tenant(tenant) as s:
        credits.spend(s, "image", credits=20)
    assert (tenant, 100) in credits.alerts(admin)


def test_a_pack_is_bought_online_and_added_once(db, admin):
    fake = FakeGateway()
    fake.one_off_payment = lambda customer_id, amount_cents, currency, description, redirect_url, webhook_url, metadata: (
        fake.first_payment(customer_id, amount_cents, currency, description, redirect_url, webhook_url, metadata))
    c, sent = client(db, gateway=fake)
    owner = f"{unique('amm')}@rossi.it"
    tid = signup(c, key(db), unique("web"), owner).json()["tenant"]["tenant_id"]
    invite = re.search(r"https?://\S+/invite/\S+", sent[0][2]).group(0)
    c.post(f"/invite/{invite.rsplit('/', 1)[1]}", data={"password": "password del capo", "again": "password del capo"})
    page = c.get("/plan").text
    assert "Crediti del mese" in page and "0 / 30" in page and "Disponibili adesso" in page
    assert f'action="/account/{tid}/credits"' in page and "500 crediti · 5 €" in page
    r = c.post(f"/account/{tid}/credits", data={"pack": "small", "back": "plan"}, follow_redirects=False)
    pid = f"tr_{fake.run}1"
    assert r.headers["location"] == f"https://pay.example/{pid}" and fake.payments[pid]["amount_cents"] == 500
    assert fake.last["redirect"] == f"https://editor.example.com/account/{tid}/credits-paid?back=plan"
    c.post("/api/billing/mollie/webhook", data={"id": pid})  # still open
    with admin.tenant(tid) as s:
        assert credits.state(s)["extra"] == 0
    fake.payments[pid]["status"] = "paid"
    c.post("/api/billing/mollie/webhook", data={"id": pid})
    with admin.engine.begin() as conn:  # delivered again: added once
        conn.execute(text("UPDATE editor.payments SET status = 'open' WHERE provider_payment_id = :p"), {"p": pid})
    c.post("/api/billing/mollie/webhook", data={"id": pid})
    with admin.tenant(tid) as s:
        assert credits.state(s)["extra"] == 500
    assert "Pacchetto" in c.get("/plan").text
    assert billing.subscription(admin, tid)["status"] == "trialing"  # a pack does not touch the plan


def test_the_console_edits_prices_packs_and_credits(db, admin):
    fake = FakeGateway()
    c, sent = client(db, gateway=fake)
    op = f"{unique('op')}@example.com"
    accounts.create_user(admin, op, "Operatore", "password lunga", operator=True)
    owner = f"{unique('amm')}@rossi.it"
    tid = signup(c, key(db), unique("web"), owner).json()["tenant"]["tenant_id"]
    c.post("/login", data={"email": op, "password": "password lunga"})
    code = unique("xl").lower()[:30]
    r = c.post("/admin/credits/packs", data={"code": code, "name": "10.000 crediti", "credits": "10000",
                                            "price": "80,00"}, follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/admin/plans").text
    assert "Listino" in page and "10.000 crediti" in page
    c.post(f"/admin/credits/packs/{code}", data={"name": "XL", "credits": "12000", "price": "90", "active": "0"})
    with admin.engine.connect() as conn:
        pack = credits.get_pack(conn, code)
    assert (pack.name, pack.credits, pack.price_cents, pack.active) == ("XL", 12000, 9000, False)
    c.post(f"/admin/credits/packs/{code}/delete")
    assert all(p["code"] != code for p in credits.packs(admin))
    old = None
    with admin.engine.connect() as conn:
        old = credits.prices(conn)
    try:
        c.post("/admin/credits/prices", data={"article": "30", "translation": "12", "image": "4"})
        with admin.engine.connect() as conn:
            assert credits.prices(conn) == {"article": 30, "translation": 12, "image": 4}
    finally:
        credits.set_prices(admin, old)
    c.post(f"/admin/newsrooms/{tid}/credits", data={"credits": "200", "amount": "", "note": "regalo"})
    c.post(f"/admin/newsrooms/{tid}/credits", data={"credits": "-50", "amount": "", "note": "errore"})
    r = c.post(f"/admin/newsrooms/{tid}/credits", data={"credits": "-500", "amount": ""}, follow_redirects=False)
    assert r.status_code == 303
    with admin.tenant(tid) as s:
        assert credits.state(s)["extra"] == 150
    # The plan's credits come with the plan, and the public plans say what credits buy.
    c.post(f"/admin/newsrooms/{tid}", data={"credits_month": "40"})
    with admin.tenant(tid) as s:
        assert credits.state(s)["month"] == 40
    pub = c.get("/api/public/plans").json()
    assert pub["credits"]["prices"]["article"] == 25 and pub["credits"]["free_redraws"] == 2
    assert pub["plans"] and all("credits_month" in p for p in pub["plans"])  # other tests change the plans


def test_the_console_ties_an_old_newsroom_to_a_customer(db, admin, tenant):
    """A newsroom made before billing has people but no customer: the
    console shows who works in it and creates or links its customer."""
    from editor.accounts import ensure_user, set_member
    c, _ = client(db)
    op = f"{unique('op')}@example.com"
    accounts.create_user(admin, op, "Operatore", "password lunga", operator=True)
    owner = f"{unique('capo')}@example.com"
    with admin.engine.begin() as conn:
        set_member(conn, ensure_user(conn, owner, "Capo"), tenant, "owner")
    c.post("/login", data={"email": op, "password": "password lunga"})
    assert tenant in c.get("/admin/customers").text  # among the newsrooms without a customer
    page = c.get(f"/admin/newsrooms/{tenant}").text
    assert "Persone e cliente" in page and owner in page and "invito non ancora accettato" in page
    r = c.post(f"/admin/newsrooms/{tenant}/customer", data={"kind": "person", "name": "Capo", "email": owner,
                                                           "tax_code": "VRDMRA80A01H501U"}, follow_redirects=False)
    assert r.status_code == 303
    cust = billing.customer_of(admin, tenant)
    assert cust["name"] == "Capo" and f"#c-{cust['id']}" in c.get(f"/admin/newsrooms/{tenant}").text
    c.post(f"/admin/newsrooms/{tenant}/customer/link", data={"customer_id": ""})
    assert billing.customer_of(admin, tenant) is None
    c.post(f"/admin/newsrooms/{tenant}/customer/link", data={"customer_id": str(cust["id"])})
    assert billing.customer_of(admin, tenant)["id"] == cust["id"]


def test_a_new_newsroom_opens_on_its_new_customer_or_takes_an_existing_one(db, admin):
    c, _ = client(db)
    op = f"{unique('op')}@example.com"
    accounts.create_user(admin, op, "Operatore", "password lunga", operator=True)
    c.post("/login", data={"email": op, "password": "password lunga"})
    assert 'option value="new" selected' in c.get("/admin/new").text
    r = c.post("/admin/newsrooms", data={"name": unique("Nuova"), "owner_email": f"{unique('d')}@example.com"})
    assert r.status_code == 200 and "Crea il cliente e collegalo" in r.text and "--customer\" open" in r.text
    tid = re.search(r'id="r-([a-z0-9-]+)--customer"', r.text).group(1)
    c.post(f"/admin/newsrooms/{tid}/customer", data={"kind": "person", "name": "Capo", "email": "capo@example.com",
                                                    "tax_code": "VRDMRA80A01H501U"})
    cid = billing.customer_of(admin, tid)["id"]
    r = c.post("/admin/newsrooms", data={"name": unique("Seconda"), "owner_email": "capo@example.com",
                                         "customer": str(cid)})
    second = re.search(r'id="r-([a-z0-9-]+)--customer"', r.text).group(1)
    assert billing.customer_of(admin, second)["id"] == cid and "--customer\" open" not in r.text
