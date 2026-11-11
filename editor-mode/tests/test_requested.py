"""Articles asked for in the newsroom's own words (Francesco 2026-11-05: "c'è
un form dove posso richiedere un articolo con un mio prompt personale?")."""

import json
import re

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from editor import auth, models as m, requested as rq
from editor.api import create_app
from editor.core_client import CoreClient

from test_drafts import chat
from test_proposals import setup_site

CORE = "http://core.test/v1/llm/chat"

PRESS = "<html><head><title>Starlink, nuovi prezzi per l'Italia</title></head><body><article><p>" + (
    "Starlink ha annunciato nuovi prezzi per l'abbonamento residenziale in Italia, con il kit a 199 euro e il "
    "canone mensile a 29 euro per chi vive nelle zone rurali senza fibra. ") * 4 + "</p></article></body></html>"


def core():
    return CoreClient("http://core.test", "tok")


def test_links_and_words_of_a_request():
    assert rq.links_of("vedi https://a.example/x, e http://b.example/y.\nhttps://a.example/x") == [
        "https://a.example/x", "http://b.example/y"]
    assert len(rq.links_of(" ".join(f"https://s{i}.example/" for i in range(9)))) == rq.MAX_LINKS
    terms = rq.terms_of("Scrivi un articolo per i lettori sui prezzi di Starlink https://press.example/x")
    assert "starlink" in terms and "prezzi" in terms
    assert not terms & {"scrivi", "articolo", "lettori", "press", "example"}


def _answer(asked):
    def answer(request):
        msgs = json.loads(request.content)["messages"]
        asked.append(msgs)
        system = msgs[0]["content"]
        if "search words" in system:
            return chat(json.dumps({"terms": ["Starlink", "satellite", "prezzi", "Italia"]}))
        if system.startswith("You write original news articles"):
            main = [int(x) for x in re.findall(r"^\[(\d+)\] MAIN", msgs[1]["content"], re.M)]
            return chat(json.dumps({"title": "Starlink in Italia: kit a 199 euro, canone a 29", "subtitle": "",
                                    "sections": [{"heading": None, "claims": [
                                        {"text": "Starlink ha fissato in Italia il kit a 199 euro e il canone a 29 euro al mese.",
                                         "sources": main[:1]},
                                        {"text": "I nuovi prezzi riguardano anche il satellite in Italia.",
                                         "sources": main[1:2] or main[:1]},
                                        {"text": "L'offerta è pensata per le zone rurali senza fibra.",
                                         "sources": main[:1]}]}]}))
        return chat("{}")
    return answer


def test_a_request_is_written_from_its_links_and_the_archive_news(db, tenant):
    site_id = setup_site(db, tenant)
    asked = []
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
        router.get(url__regex=r".*/\.well-known/tdmrep\.json").mock(return_value=httpx.Response(404))
        router.get("https://press.example/starlink").mock(
            return_value=httpx.Response(200, text=PRESS, headers={"content-type": "text/html"}))
        router.get(url__regex=r"https://(news|rete)\.example/.*").mock(return_value=httpx.Response(404))
        router.post(CORE).mock(side_effect=_answer(asked))
        with httpx.Client() as client:
            out = rq.ask(db, tenant, site_id, "Un articolo per chi vive in campagna: quanto costa ora Starlink?",
                         ["https://press.example/starlink"], "francesco", core(), client)
    assert out.get("draft_id") and out["links"] == 1 and out["found"] >= 1 and not out["unread"], out
    writer = next(x for x in asked if x[0]["content"].startswith("You write original news articles"))[-1]["content"]
    assert "asked for by the newsroom in its own words" in writer
    assert "Starlink, nuovi prezzi per l'Italia" in writer  # the link, read
    assert "Starlink abbassa i prezzi del satellite in Italia" in writer  # the archive's news on the subject
    assert "pizza" not in writer and "calcio" not in writer
    with db.tenant(tenant) as s:
        p = s.execute(select(m.proposals).where(m.proposals.c.id == out["proposal_id"])).one()
        d = s.execute(select(m.drafts).where(m.drafts.c.id == out["draft_id"])).one()
    assert p.format == "request" and p.decided_by == "francesco" and p.status == "drafted"
    assert d.title.startswith("Starlink in Italia") and "press.example" in d.body_md

    # The same link asked again is not read again.
    with respx.mock(assert_all_called=False) as router:
        page = router.get("https://press.example/starlink")
        router.post(CORE).mock(side_effect=_answer([]))
        with httpx.Client() as client:
            rq.ask(db, tenant, site_id, "Starlink e i prezzi in Italia, in breve", ["https://press.example/starlink"],
                   "francesco", core(), client)
    assert not page.calls


def test_nothing_to_cite_means_no_draft(db, tenant):
    site_id = setup_site(db, tenant)
    asked = []

    def answer(request):
        asked.append(json.loads(request.content)["messages"])
        return chat(json.dumps({"terms": ["vulcani", "Islanda", "eruzione"]}))

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
        router.get(url__regex=r".*/\.well-known/tdmrep\.json").mock(return_value=httpx.Response(404))
        router.get("https://blocked.example/x").mock(return_value=httpx.Response(403))
        router.post(CORE).mock(side_effect=answer)
        with httpx.Client() as client:
            out = rq.ask(db, tenant, site_id, "Un approfondimento sulle eruzioni dei vulcani in Islanda",
                         ["https://blocked.example/x"], "francesco", core(), client)
    assert out == {"none": True, "unread": ["https://blocked.example/x"], "site_id": site_id}
    assert len(asked) == 1  # only the search words: no article is written
    with db.tenant(tenant) as s:
        assert not s.execute(select(func.count()).select_from(m.proposals).where(
            m.proposals.c.format == "request")).scalar()


def test_the_desk_takes_a_request_and_queues_it(db, tenant):
    site_id = setup_site(db, tenant)
    c = TestClient(create_app(db, core=core()))
    assert c.post("/login", data={"token": auth.create_token(db, tenant, "editor-user", "editor")},
                  follow_redirects=False).status_code == 303
    assert "Chiedi un articolo" in c.get("/").text
    r = c.post("/ui/requests", data={"site_id": site_id, "text": "corto"}, follow_redirects=False)
    assert r.status_code == 303
    r = c.post("/ui/requests", data={"site_id": site_id, "text": "Un articolo sui prezzi di Starlink in Italia",
                                     "links": "https://press.example/starlink\nnon un link"}, follow_redirects=False)
    assert r.status_code == 303
    with db.tenant(tenant) as s:
        jobs = s.execute(select(m.jobs.c.args).where(m.jobs.c.kind == "request")).scalars().all()
    assert jobs == [{"site_id": site_id, "text": "Un articolo sui prezzi di Starlink in Italia",
                     "links": ["https://press.example/starlink"], "by": "editor-user", "kind": "article"}]
    assert "Un menù intero" not in c.get("/").text  # not a site of recipes
    c.post("/ui/requests", data={"site_id": site_id, "text": "Menù della Vigilia di pesce", "kind": "menu"})
    with db.tenant(tenant) as s:
        kinds = [a["kind"] for a in s.execute(select(m.jobs.c.args).where(m.jobs.c.kind == "request")).scalars()]
    assert kinds == ["article", "article"]


def test_a_site_of_recipes_asks_for_a_whole_menu(db, tenant, monkeypatch):
    from editor import drafts, profile as prof

    site_id = setup_site(db, tenant)
    with db.tenant(tenant) as s:
        assert prof.mark_recipe_site(s, site_id, ["I Primi", "I Secondi", "I Dessert"])
    c = TestClient(create_app(db, core=core()))
    c.post("/login", data={"token": auth.create_token(db, tenant, "editor-user", "editor")})
    assert "Un menù intero per un'occasione" in c.get("/").text
    c.post("/ui/requests", data={"site_id": site_id, "text": "Menù della Vigilia di pesce per otto", "kind": "menu"})
    with db.tenant(tenant) as s:
        args = s.execute(select(m.jobs.c.args).where(m.jobs.c.kind == "request")).scalar()
    assert args["kind"] == "menu"
    written = []
    monkeypatch.setattr(drafts, "write_draft", lambda db_, t, pid, core_, client: written.append(pid) or 0)
    with respx.mock(assert_all_called=False) as router:
        router.post(CORE).mock(return_value=chat(json.dumps({"terms": ["Starlink", "prezzi"]})))
        with pytest.raises(Exception):  # the stub writes no draft: only the proposal matters here
            rq.ask(db, tenant, site_id, "Menù della Vigilia: Starlink prezzi", [], "francesco", core(), None,
                   kind="menu")
    with db.tenant(tenant) as s:
        fmt = s.execute(select(m.proposals.c.format).where(m.proposals.c.id == written[0])).scalar()
    assert fmt == "menu"
