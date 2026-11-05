"""Il Punto della Sera, the analysis in the articles and the stock phrases
of machine-written text (Francesco 2026-11-05)."""

import json
import re
import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import respx
from sqlalchemy import insert, select, update

from editor import autopilot as ap, drafts, evening as ev, models as m, publishing as pub, style
from editor.core_client import CoreClient
from editor.schedule import Runner

from conftest import set_plan
from test_drafts import accepted_proposal, chat
from test_publishing import public_resolver

CORE = "http://core.test/v1/llm/chat"


def core():
    return CoreClient("http://core.test", "tok")


def test_stock_phrases_are_found_in_the_article_language():
    assert style.stock("Non si tratta solo di un accordo, ma di una svolta.", "it") == ["Non si tratta solo di"]
    assert style.stock("L'intesa rappresenta un passo importante per il settore.", "it")
    assert style.stock("Resta da vedere se il governo approverà.", "it")
    assert style.stock("La decisione gioca un ruolo chiave nel piano.", "it")
    assert style.stock("This marks a turning point and underscores the importance of chips.", "en")
    assert style.stock("It remains to be seen whether rates fall.", "en") == ["remains to be seen"]
    # Plain news writing is left alone, and a language without a list too.
    assert not style.stock("Il governo ha approvato il decreto con 12 voti di scarto.", "it")
    assert not style.stock("The central bank cut rates by 0.25 points.", "en")
    assert not style.stock("Resta da vedere se il governo approverà.", "pt")


def test_a_stock_phrase_is_reworded_where_it_stands(db, tenant):
    pid, cited = accepted_proposal(db, tenant)
    one = [cited[0]]
    article = json.dumps({"title": "Bari in fibra", "subtitle": "La rete è completa", "sections": [
        {"heading": None, "claims": [
            {"text": "Open Fiber ha completato la rete a Bari.", "sources": one},
            {"text": "Il cantiere rappresenta un passo importante per la città.", "sources": one},
            {"text": "I lavori sono finiti.", "sources": one}]}]})
    asked = []

    def answer(request):
        asked.append(json.loads(request.content)["messages"])
        if len(asked) == 1:
            return chat(article)
        return chat(json.dumps({"sentences": {"1": "Con il cantiere la città ha la fibra in ogni quartiere."}}))

    with respx.mock() as router:
        router.post(CORE).mock(side_effect=answer)
        did = drafts.write_draft(db, tenant, pid, core(), None)
    assert "none of the stock phrases" in asked[0][0]["content"]  # the writer is told first
    assert 'Stock phrases: "rappresenta un passo"' in asked[1][-1]["content"]
    with db.tenant(tenant) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).one()
    assert "passo importante" not in d.body_md and "fibra in ogni quartiere" in d.body_md


def test_the_writer_is_asked_for_a_critical_reading_only_on_the_facts(db, tenant):
    from editor.drafts import _analysis_lines

    assert "Cosa cambia" in _analysis_lines({})[0] and "never as news" in _analysis_lines({})[0]
    assert _analysis_lines({"settings": {"auto": {"analysis": False}}}) == []
    assert any("presents the newsroom's own reading as an established fact" in r for r in ap.RULES)


def _day(db, tenant, n=3):
    """n articles of the site published in the last hours."""
    pid, cited = accepted_proposal(db, tenant)
    dids = []
    for k in range(n):
        article = json.dumps({"title": f"Notizia numero {k + 1} della giornata", "subtitle": "", "sections": [
            {"heading": None, "claims": [
                {"text": f"Open Fiber ha completato la rete a Bari, fase {k + 1}.", "sources": [cited[0]]},
                {"text": "La rete in fibra è completa.", "sources": [cited[0]]},
                {"text": "I lavori sono finiti.", "sources": [cited[0]]}]}]})
        with respx.mock() as router:
            router.post(CORE).mock(return_value=chat(article))
            dids.append(drafts.write_draft(db, tenant, pid, core(), None))
    with db.tenant(tenant) as s:
        site_id = s.execute(select(m.drafts.c.site_id).where(m.drafts.c.id == dids[0])).scalar()
        s.execute(update(m.drafts).where(m.drafts.c.id.in_(dids)).values(status="approved", decided_by="test"))
    target = pub.add_target(db, tenant, site_id, "webhook", "sito", {"url": "https://hooks.example/it"}, None)
    now = datetime.now(UTC)
    with db.tenant(tenant) as s:
        for k, did in enumerate(dids):
            s.execute(insert(m.publications).values(
                tenant_id=tenant, draft_id=did, target_id=target, mode="publish", payload={}, status="published",
                requested_by="test", decided_by="test", published_at=now - timedelta(hours=k + 1),
                external_url=f"https://blog.example/articoli/notizia-{k + 1}"))
    return site_id, dids, target


def _analysis(asked):
    def answer(request):
        msgs = json.loads(request.content)["messages"]
        asked.append(msgs)
        if "editor-in-chief" in msgs[0]["content"]:
            return chat(json.dumps({"publish": True, "problems": [], "section": "Rete"}))
        ids = [int(x) for x in re.findall(r"^\[(\d+)\]", msgs[-1]["content"], re.M)]
        return chat(json.dumps({"title": "Bari chiude la rete in fibra", "subtitle": "Tre fasi in un giorno",
                                "sections": [
                                    {"heading": None, "claims": [
                                        {"text": "A Bari la rete in fibra è completa.", "sources": ids[:1]},
                                        {"text": "Le fasi del giorno raccontano lo stesso cantiere.", "sources": ids}]},
                                    {"heading": "In breve", "claims": [
                                        {"text": "I lavori sono finiti.", "sources": ids[-1:]}]}]}))
    return answer


def test_the_evening_analysis_reads_the_day_and_links_its_articles(db, tenant, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    site_id, dids, target = _day(db, tenant)
    with db.tenant(tenant) as s:
        assert ev.save(s, site_id, {"enabled": True, "hour": 21, "min_articles": 3})
        body = s.execute(select(m.editorial_profiles.c.body).where(m.editorial_profiles.c.site_id == site_id,
                                                                   m.editorial_profiles.c.status == "approved")).scalar()
        assert ev.allowed(s)
    assert ev.due(body, datetime(2026, 11, 5, 21, 10)) and not ev.due(body, datetime(2026, 11, 5, 20, 59))

    # Once a day, past its hour: the tick queues it and remembers the day.
    done = []
    Runner.evening(SimpleNamespace(db=db), tenant, datetime(2026, 11, 5, 20, 30, tzinfo=UTC), "Europe/Rome", done, [])
    Runner.evening(SimpleNamespace(db=db), tenant, datetime(2026, 11, 5, 21, 30, tzinfo=UTC), "Europe/Rome", done, [])
    with db.tenant(tenant) as s:
        jobs = s.execute(select(m.jobs.c.subject).where(m.jobs.c.kind == "evening")).scalars().all()
    assert jobs == [f"evening {site_id} 2026-11-05"] and done == ["evening"]

    asked = []
    with respx.mock() as router:
        router.post(CORE).mock(side_effect=_analysis(asked))
        did = ev.write(db, tenant, site_id, core(), datetime.now(UTC), "Europe/Rome")
    prompt = asked[0][-1]["content"]
    assert "editorial director" in asked[0][0]["content"] and "Agenda setting" in asked[0][0]["content"]
    assert all(f"Notizia numero {k} della giornata" in prompt for k in (1, 2, 3))
    with db.tenant(tenant) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).one()
        fmt = s.execute(select(m.proposals.c.format).where(m.proposals.c.id == d.proposal_id)).scalar()
    assert fmt == "evening" and d.seo["section"] == "Analisi e Approfondimenti"
    assert d.subtitle.startswith("Il Punto della Sera.")
    assert "https://blog.example/articoli/notizia-1" in d.body_md and "Notizia numero 2 della giornata" in d.body_md

    # With the automatic newsroom on it goes out by itself, read with the evening rules, in its own section.
    with db.tenant(tenant) as s:
        assert ap.save(s, site_id, {**ap.DEFAULTS, "enabled": True, "min_opportunity": 0, "hours": [0, 24],
                                    "min_sources": 2, "sections": ["Rete"], "target_id": target})
    sent = []
    with respx.mock() as router:
        router.post(CORE).mock(side_effect=_analysis(asked))
        out = ap.finish(db, tenant, did, core(), sent.append)
    assert "evening analysis" in asked[-1][0]["content"] and "joins separate news events" not in asked[-1][0]["content"]
    assert out.get("target") == "sito", out
    with db.tenant(tenant) as s:
        p = s.execute(select(m.publications).where(m.publications.c.id == sent[0])).one()
    assert p.payload["section"] == "Analisi e Approfondimenti"
    # The analysis itself is never one of the day's articles of the next one.
    with db.tenant(tenant) as s:
        s.execute(update(m.publications).where(m.publications.c.id == sent[0]).values(
            status="published", published_at=datetime.now(UTC), external_url="https://blog.example/punto"))
        assert did not in {r.id for r in ev.published(s, site_id, datetime.now(UTC))}


def test_no_analysis_for_a_thin_day_or_a_plan_without_it(db, tenant, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    site_id, _, _ = _day(db, tenant, n=2)
    with db.tenant(tenant) as s:
        ev.save(s, site_id, {"enabled": True, "min_articles": 3})
    with respx.mock(assert_all_called=False) as router:
        llm = router.post(CORE)
        assert ev.write(db, tenant, site_id, core(), datetime.now(UTC)) is None
    assert not llm.calls
    set_plan(db, tenant, evening=False)
    with db.tenant(tenant) as s:
        assert not ev.allowed(s)
    done = []
    Runner.evening(SimpleNamespace(db=db), tenant, datetime(2026, 11, 5, 22, 0, tzinfo=UTC), "Europe/Rome", done, [])
    assert done == []
