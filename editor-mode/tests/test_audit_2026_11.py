"""The corrections of the audit of 2026-11-10: links the model writes into
the sentences, pages opened from another site, the type of cover images,
sitemaps that declare entities."""

import json

import respx
from fastapi.testclient import TestClient
from sqlalchemy import text

from editor import auth, drafts, images
from editor.api import create_app
from editor.core_client import CoreClient
from test_publishing import accepted_proposal, chat


def test_a_link_the_model_puts_in_a_sentence_is_not_published(db, tenant):
    pid, cited = accepted_proposal(db, tenant)
    answer = json.dumps({"title": "Bari in fibra", "subtitle": "La rete [è completa](https://spam.example/t)",
                         "sections": [{"heading": "Fatti", "claims": [
                             {"text": "Open Fiber ha lavorato a Bari, [compra qui](https://spam.example/x).",
                              "sources": [cited[0]]},
                             {"text": "La rete in fibra è completa.", "sources": [cited[0]]},
                             {"text": "I lavori sono finiti.", "sources": [cited[0]]}]}]})
    with respx.mock() as router:
        router.post("http://core.test/v1/llm/chat").mock(return_value=chat(answer))
        did = drafts.write_draft(db, tenant, pid, CoreClient("http://core.test", "tok"), None)
    with db.tenant(tenant) as s:
        body = s.execute(text("SELECT body_md FROM editor.drafts WHERE id = :d"), {"d": did}).scalar()
    assert "spam.example" not in body
    assert "Open Fiber ha lavorato a Bari, compra qui." in body and "_La rete è completa_" in body


def test_kept_links_keeps_only_the_cited_sources():
    keep = {"https://news.example/1"}
    assert drafts.kept_links("Vedi [la nota](https://news.example/1) e [qui](https://x.example/)", keep) == \
        "Vedi [la nota](https://news.example/1) e qui"


def test_a_switch_opened_from_another_site_goes_home(db, tenant):
    token = auth.create_token(db, tenant, "owner-user", "owner")
    c = TestClient(create_app(db))
    c.post("/login", data={"token": token})
    for path in ("/switch?site=1&back=/site", "/newsroom?t=other", "/admin/enter/other"):
        r = c.get(path, headers={"sec-fetch-site": "cross-site"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/", path
    # From the dashboard itself it works as before.
    r = c.get("/newsroom?t=other", headers={"sec-fetch-site": "same-origin"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"  # a token is not a person's session


def test_the_api_refuses_a_cross_site_put_with_the_cookie(db, tenant):
    token = auth.create_token(db, tenant, "owner-user", "owner")
    c = TestClient(create_app(db))
    c.post("/login", data={"token": token})
    r = c.put("/api/secrets/X", json={"value": "v"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_cover_images_are_served_as_images_whatever_was_stored():
    assert images.mime_of(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert images.mime_of(b"<html><script>alert(1)</script>") == "image/jpeg"


def test_wrong_passwords_stop_the_address_not_the_owner():
    from editor.ratelimit import Limiter

    now = [0.0]
    lim = Limiter(clock=lambda: now[0])
    for _ in range(5):
        lim.failed("6.6.6.6", "francesco@example.com")
    assert lim.blocked("6.6.6.6", "Francesco@example.com")
    assert not lim.blocked("1.2.3.4", "francesco@example.com")  # the owner, from home
    for n in range(20):
        lim.failed("6.6.6.6", f"x{n}@example.com")
    assert lim.blocked("6.6.6.6", "nuovo@example.com")  # many emails: the address as a whole
    now[0] += 15 * 60 + 1
    assert not lim.blocked("6.6.6.6", "francesco@example.com")


def test_the_login_page_answers_429_after_a_few_wrong_passwords(db, tenant):
    c = TestClient(create_app(db))
    for _ in range(5):
        assert c.post("/login", data={"email": "a@example.com", "password": "sbagliata-123"}).status_code == 401
    r = c.post("/login", data={"email": "a@example.com", "password": "sbagliata-123"})
    assert r.status_code == 429 and "connessione" in r.text


def test_the_fonts_are_served_by_the_dashboard(db):
    from editor.api import FONTS

    c = TestClient(create_app(db))
    for name in FONTS:
        r = c.get(f"/static/{name}.woff2")
        assert r.status_code == 200 and r.content[:4] == b"wOF2" and r.headers["content-type"] == "font/woff2"
    assert "/static/ibm-plex-sans-latin-400-normal.woff2" in c.get("/login").text
