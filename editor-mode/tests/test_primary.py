import json

import httpx
import respx
from sqlalchemy import select, text

from editor import drafts, models as m, primary
from editor.core_client import CoreClient
from test_drafts import accepted_proposal, chat

NEWS = """<html><head><title>News</title></head><body><article>
<p>Il Fondo sanitario perde 13 miliardi, secondo il comunicato stampa della fondazione sulla spesa sanitaria.</p>
<p>Leggi il <a href="https://fondazione.example/comunicato-stampa-fondo-sanitario">comunicato stampa fondo sanitario</a>
per il dettaglio dei dati regione per regione.</p></article></body></html>"""
DOCUMENT = "<html><head><title>Comunicato stampa: fondo sanitario</title></head><body><article>" + (
    "<p>La fondazione pubblica i dati sul fondo sanitario nazionale e sulla spesa delle regioni, con il dettaglio "
    "dei tredici miliardi mancanti rispetto alle stime dell'anno precedente per ogni regione italiana. </p>" * 4
) + "</article></body></html>"
GAP = {"missing": True, "datum": "Il fondo sanitario perde 13 miliardi", "entity": "Fondazione",
       "domains": [], "query": "comunicato stampa fondo sanitario"}


def _setup(db, tenant):
    pid, cited = accepted_proposal(db, tenant)
    with db.tenant(tenant) as s:
        prop = s.execute(select(m.proposals).where(m.proposals.c.id == pid)).first()
        sources = drafts._sources(s, cited)
    return prop, [sources[i] for i in cited]


def _run(db, tenant, prop, main, answers, existing=False):
    answers = iter(answers)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://(news|rete)\.example/.*").mock(return_value=httpx.Response(
            200, text=NEWS, headers={"content-type": "text/html"}))
        router.get(url__regex=r"https://fondazione\.example/.*").mock(return_value=httpx.Response(
            200, text=DOCUMENT, headers={"content-type": "text/html"}))
        llm = router.post("http://core.test/v1/llm/chat").mock(side_effect=lambda r: chat(json.dumps(next(answers))))
        with httpx.Client() as client:
            out = primary.look(db, tenant, prop, main, existing, CoreClient("http://core.test", "tok"), client)
    return out, llm


def test_the_document_the_news_links_is_found_checked_and_kept(db, tenant):
    prop, main = _setup(db, tenant)
    ok = {"results": [{"n": 1, "entity": True, "topic": True, "date": True, "supports": True}]}
    out, llm = _run(db, tenant, prop, main, [GAP, ok])
    assert len(out) == 1 and len(llm.calls) == 2
    with db.tenant(tenant) as s:
        row = s.execute(select(m.source_items).where(m.source_items.c.id == out[0])).first()
        basis = s.execute(select(m.proposals.c.basis).where(m.proposals.c.id == prop.id)).scalar()
        used = s.execute(text("SELECT count(*) FROM editor.llm_usage WHERE purpose = 'primary_source'")).scalar()
    assert row.url == "https://fondazione.example/comunicato-stampa-fondo-sanitario" and row.duplicate_of == main[0].id
    assert basis["primary"]["found"] == [row.url] and used == 2


def test_a_candidate_the_check_does_not_confirm_is_not_used(db, tenant):
    prop, main = _setup(db, tenant)
    no = {"results": [{"n": 1, "entity": True, "topic": True, "date": False, "supports": True}]}
    out, _ = _run(db, tenant, prop, main, [GAP, no])
    assert out == []


def test_nothing_is_looked_for_when_nothing_is_missing(db, tenant):
    prop, main = _setup(db, tenant)
    out, llm = _run(db, tenant, prop, main, [{"missing": False}])
    assert out == [] and len(llm.calls) == 1


def test_a_rewrite_reuses_what_was_found_and_asks_nothing(db, tenant):
    prop, main = _setup(db, tenant)
    ok = {"results": [{"n": 1, "entity": True, "topic": True, "date": True, "supports": True}]}
    first, _ = _run(db, tenant, prop, main, [GAP, ok])
    with db.tenant(tenant) as s:
        again = s.execute(select(m.proposals).where(m.proposals.c.id == prop.id)).first()
    second, llm = _run(db, tenant, again, main, [])
    assert second == first and len(llm.calls) == 0


def test_the_daily_cap_stops_the_search(db, tenant, monkeypatch):
    prop, main = _setup(db, tenant)
    monkeypatch.setattr(primary, "PER_DAY", 1)
    out, llm = _run(db, tenant, prop, main, [GAP])
    assert out == [] and len(llm.calls) == 0


def test_a_document_already_read_means_nothing_is_missing(db, tenant):
    prop, main = _setup(db, tenant)
    out, llm = _run(db, tenant, prop, main, [], existing=True)
    assert out == [] and len(llm.calls) == 0


def test_links_are_ranked_by_what_they_say():
    from editor.html_extract import Link

    links = [Link("https://fondazione.example/comunicato-stampa-fondo", "comunicato stampa", False),
             Link("https://altro.example/sport", "sport e calcio", False),
             Link("https://twitter.com/x/comunicato-stampa-fondo", "comunicato stampa fondo", False),
             Link("https://news.example/altro-fondo-sanitario", "fondo sanitario comunicato", False)]
    got = primary.rank(links, {"comunicato", "stampa", "fondo", "sanitario"}, [], {"news.example"})
    assert got == ["https://fondazione.example/comunicato-stampa-fondo"]
