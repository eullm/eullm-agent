"""The console as a sales back office: a customer entered by hand, a
newsroom made for a new, an existing or no customer, the month's payments,
one search for everything, the people of a newsroom; and what the director
sees of it: the payments and the billing data."""

import re

import pytest
from sqlalchemy import select

from editor import accounts, billing, models as m
from editor.db import Database

from test_billing import client, fresh_ids, unique


@pytest.fixture
def admin(db):
    a = Database.from_url(db.admin_url)
    yield a
    a.engine.dispose()


def operator(db, admin):
    c, _ = client(db)
    op = f"{unique('op')}@example.com"
    accounts.create_user(admin, op, "Operatore", "password lunga", operator=True)
    c.post("/login", data={"email": op, "password": "password lunga"})
    return c


def billing_data(**extra) -> dict:
    """A company of its own: the tests share the database, and the same
    email or VAT number is the same customer."""
    return {"kind": "company", "name": unique("Studio"), "email": f"{unique('fatture')}@example.com",
            **fresh_ids(), "city": "Milano", **extra}


def customer_id(admin, email: str) -> int:
    return next(x["id"] for x in billing.customers(admin) if x["email"] == email)


def created(r) -> str:
    """The newsroom whose page the console shows after creating it."""
    return re.search(r'<header class="page-head" id="r-([a-z0-9-]+)"', r.text).group(1)


def test_a_customer_entered_by_hand_has_its_page_and_then_its_newsroom(db, admin):
    c = operator(db, admin)
    assert 'action="/admin/customers"' in c.get("/admin/customers/new").text
    data = billing_data()
    r = c.post("/admin/customers", data=data, follow_redirects=False)
    cid = customer_id(admin, data["email"])
    assert r.status_code == 303 and r.headers["location"] == f"/admin/customers/{cid}"
    page = c.get(f"/admin/customers/{cid}").text
    assert data["name"] in page and data["vat_number"] in page and f'href="/admin/new?customer={cid}"' in page
    assert f'id="c-{cid}--edit"' in page and f'id="c-{cid}--delete"' in page
    # The same VAT number again: the form comes back as written, with the way to the customer already there.
    twin = billing_data(vat_number=data["vat_number"], tax_code="")
    r = c.post("/admin/customers", data=twin)
    assert r.status_code == 400 and "partita IVA" in r.text and f'href="/admin/customers/{cid}"' in r.text
    assert twin["name"] in r.text and twin["email"] in r.text
    # Straight on to its newsroom: the new newsroom comes with the customer chosen.
    other = billing_data()
    r = c.post("/admin/customers", data={**other, "then": "room"}, follow_redirects=False)
    cid2 = customer_id(admin, other["email"])
    assert r.headers["location"] == f"/admin/new?customer={cid2}"
    form = c.get(r.headers["location"]).text
    assert 'value="existing" checked' in form and f'<option value="{cid2}" selected' in form


def test_a_newsroom_is_made_with_a_new_customer_an_existing_one_or_none(db, admin):
    c = operator(db, admin)
    assert 'value="new" checked' in c.get("/admin/new").text
    data = billing_data()
    r = c.post("/admin/newsrooms", data={"customer_mode": "new", **{f"c_{k}": v for k, v in data.items()},
                                         "name": unique("Gazzetta"), "plan": "pro"})
    assert r.status_code == 200
    tid = created(r)
    cust = billing.customer_of(admin, tid)
    assert cust["email"] == data["email"] and billing.subscription(admin, tid)["plan_code"] == "pro"
    assert data["email"] in r.text  # the director is the customer, when no other email is written
    if db.sqlite:  # one newsroom per local installation: the others are a server matter
        return
    # The same customer written again: nothing is made, the form comes back with that customer chosen.
    again = {"customer_mode": "new", **{f"c_{k}": v for k, v in billing_data(vat_number=data["vat_number"]).items()},
             "name": unique("Doppia")}
    r = c.post("/admin/newsrooms", data=again)
    assert r.status_code == 400 and 'value="existing" checked' in r.text
    assert f'<option value="{cust["id"]}" selected' in r.text and again["name"] in r.text
    # An existing customer: its email is the director's when none is written.
    r = c.post("/admin/newsrooms", data={"customer_mode": "existing", "customer_id": str(cust["id"]),
                                         "name": unique("Seconda")})
    assert billing.customer_of(admin, created(r))["id"] == cust["id"]
    # No customer: an internal newsroom, the customer is linked later from its page.
    r = c.post("/admin/newsrooms", data={"customer_mode": "none", "name": unique("Interna"),
                                         "owner_email": f"{unique('dir')}@example.com"})
    internal = created(r)
    assert billing.customer_of(admin, internal) is None and f'id="r-{internal}--customer"' in r.text
    assert "Scegli il cliente" in c.post("/admin/newsrooms", data={"customer_mode": "existing", "name": "X"}).text


def test_a_newsroom_without_customer_gets_one_from_its_page(db, admin, tenant):
    c = operator(db, admin)
    data = billing_data()
    r = c.post("/admin/customers", data={**data, "room": tenant}, follow_redirects=False)
    assert r.status_code == 303 and billing.customer_of(admin, tenant)["email"] == data["email"]
    c.post(f"/admin/newsrooms/{tenant}/customer/link", data={"customer_id": ""})
    assert billing.customer_of(admin, tenant) is None
    # Already among the customers: the window comes back with it chosen, to link it.
    r = c.post("/admin/customers", data={**billing_data(vat_number=data["vat_number"]), "room": tenant})
    assert r.status_code == 400 and "premi Collega" in r.text
    assert f'<option value="{customer_id(admin, data["email"])}" selected' in r.text


def test_payments_of_every_newsroom_on_one_page_and_in_a_csv(db, admin, tenant):
    c = operator(db, admin)
    c.post("/admin/customers", data={**billing_data(name="Contabile Srl"), "room": tenant})
    r = c.post(f"/admin/subscriptions/{tenant}", data={"plan": "pro", "interval": "month", "amount": "149",
                                                       "dialog": "record"})
    assert "Pagamento di 149" in r.text and billing.subscription(admin, tenant)["status"] == "active"
    page = c.get("/admin/payments").text
    assert "Contabile Srl" in page and "149" in page and 'href="/admin/payments.csv"' in page
    assert "Pagamento non registrato" in c.post(f"/admin/subscriptions/{tenant}", data={
        "plan": "pro", "dialog": "record"}).text
    sheet = c.get("/admin/payments.csv")
    assert sheet.headers["content-type"].startswith("text/csv")
    line = next(x for x in sheet.text.splitlines() if tenant in x)
    assert "Contabile Srl" in line and ";149,00;EUR;paid;" in line
    # The director sees it in Il mio piano, with the billing data in one line.
    from editor import auth
    d, _ = client(db)
    d.post("/login", data={"token": auth.create_token(db, tenant, "dir", "owner")}, follow_redirects=False)
    plan = d.get("/plan").text
    assert "Pagamenti" in plan and "pagamento registrato" in plan and "149" in plan
    assert "Contabile Srl" in plan and f'id="billing-{tenant}" open' not in plan  # the form stays closed


def test_renewals_stop_from_the_console(db, admin, tenant):
    c = operator(db, admin)
    billing.start(admin, tenant, "pro")
    r = c.post(f"/admin/newsrooms/{tenant}/cancel")
    assert "Rinnovi fermati" in r.text and billing.subscription(admin, tenant)["status"] == "canceled"


def test_one_search_finds_newsrooms_customers_and_people(db, admin, tenant):
    c = operator(db, admin)
    word = unique("cercami")
    one, two = billing_data(name=f"{word} Uno"), billing_data(name=f"{word} Due")
    c.post("/admin/customers", data=one)
    # One answer: straight to it.
    r = c.get("/admin/search", params={"q": f"{word} uno"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/admin/customers/{customer_id(admin, one['email'])}"
    c.post("/admin/customers", data=two)
    r = c.get("/admin/search", params={"q": word.upper()})
    assert "Risultati per" in r.text and one["email"] in r.text and two["email"] in r.text
    assert "almeno due lettere" in c.get("/admin/search", params={"q": "x"}).text


def test_the_people_of_a_newsroom_are_managed_from_the_console(db, admin, tenant):
    from editor.accounts import ensure_user, set_member
    c = operator(db, admin)
    email = f"{unique('redattore')}@example.com"
    with admin.engine.begin() as conn:
        uid = ensure_user(conn, email, "Redattore")
        set_member(conn, uid, tenant, "editor")
    old = accounts.create_invite(admin, email, "test", tenant_id=tenant, role="editor")
    page = c.get(f"/admin/newsrooms/{tenant}").text
    assert email in page and "Invito non accettato" in page and f"/people/{uid}/resend" in page
    # A new link for someone who never came in: the old one stops working.
    r = c.post(f"/admin/newsrooms/{tenant}/people/{uid}/resend")
    assert r.status_code == 200 and "Nuovo invito a" in r.text and accounts.invite(admin, old) is None
    assert [i.email for i in accounts.pending_invites(admin, tenant)].count(email) == 1
    c.post(f"/admin/newsrooms/{tenant}/people/{uid}/role", data={"role": "viewer"})
    assert next(x for x in accounts.members(admin, tenant) if x.id == uid).role == "viewer"
    c.post(f"/admin/newsrooms/{tenant}/people/{uid}/remove")
    assert all(x.id != uid for x in accounts.members(admin, tenant))


def test_the_director_sends_an_invite_again(db, admin, tenant):
    from editor import auth
    d, _ = client(db)
    d.post("/login", data={"token": auth.create_token(db, tenant, "dir", "owner")}, follow_redirects=False)
    email = f"{unique('nuova')}@example.com"
    d.post("/ui/people/invite", data={"email": email, "role": "editor"})
    first = next(i for i in accounts.pending_invites(admin, tenant) if i.email == email)
    page = d.get("/settings").text
    assert f"/ui/invites/{first.token_sha256}/resend" in page
    r = d.post(f"/ui/invites/{first.token_sha256}/resend")
    assert r.status_code == 200 and email in r.text
    left = [i.token_sha256 for i in accounts.pending_invites(admin, tenant) if i.email == email]
    assert len(left) == 1 and left[0] != first.token_sha256
    assert "già usato o scaduto" in d.post(f"/ui/invites/{first.token_sha256}/resend").text
    with admin.engine.begin() as conn:
        assert conn.execute(select(m.invites.c.role).where(m.invites.c.token_sha256 == left[0])).scalar() == "editor"
