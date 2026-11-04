"""The automatic newsroom: its settings and guardrails, the hourly choice
of articles, the AI editor-in-chief, the label, the publication with the
translations, the profile written from a brief and the switch on the page."""

import json
import socket
from datetime import UTC, datetime

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, text, update

from editor import auth, autopilot as ap, drafts, models as m, proposals as props, publishing as pub, repo
from editor import opportunity as opp, profile as prof, translate as tr
from editor.api import create_app
from editor.core_client import CoreClient

from conftest import set_plan
from test_drafts import accepted_proposal, chat
from test_proposals import setup_site
from test_publishing import public_resolver
from test_translate import ENGLISH

CORE = "http://core.test/v1/llm/chat"


def core():
    return CoreClient("http://core.test", "tok")


def settings(db, tenant, site_id, **values):
    with db.tenant(tenant) as s:
        assert ap.save(s, site_id, {**ap.DEFAULTS, "enabled": True, "min_opportunity": 0, "hours": [0, 24],
                                    **values})


def test_settings_and_guardrails():
    with pytest.raises(ValueError, match="per_day"):
        ap.check_settings({"per_day": 0})
    with pytest.raises(ValueError, match="hours"):
        ap.check_settings({"hours": [9, 9]})
    cfg = ap.check_settings({"avoid": [" Calcio ", "calcio", ""], "sections": ["Mondo", " "]})
    assert cfg["avoid"] == ["calcio"] and cfg["sections"] == ["Mondo"] and cfg["credit"] is True
    assert ap.avoided("Il CALCIO in tv", ["calcio"]) == "calcio" and ap.avoided("calciomercato", ["calcio"]) is None
    night = {"hours": [22, 6]}
    assert ap.in_hours(night, datetime(2026, 10, 28, 23)) and not ap.in_hours(night, datetime(2026, 10, 28, 12))
    assert ap.hours_left(night, datetime(2026, 10, 28, 23)) == 7
    body = "# T\n\nTesto.[^1]\n\n## Fonti\n\n[^1]: [A](https://a.example), A\n"
    labelled = ap.add_label(body, "it")
    assert labelled.index("senza revisione umana") < labelled.index("## Fonti")
    assert "[Redazia](https://redazia.com)" in labelled and ap.add_label(labelled, "it") == labelled
    assert "redazia.com" not in ap.add_label(body, "en", credit=False)
    lines = ap.brief_lines({"settings": {"auto": {"brief": "Notizie dal mondo", "avoid": ["calcio"]}}})
    assert lines == ["Newsroom brief: Notizie dal mondo", "Never write about: calcio"]


def test_each_hour_takes_its_share_of_the_day(db, tenant):
    site_id = setup_site(db, tenant)
    props.propose(db, tenant, site_id, opp.score_site(db, tenant, site_id), None, limit=5)
    settings(db, tenant, site_id, per_day=1, avoid=["starlink"])
    queued = []
    now = datetime(2026, 10, 28, 11, tzinfo=UTC)
    with db.tenant(tenant) as s:
        s.execute(text("UPDATE editor.proposals SET created_at = :t"), {"t": now})
    out = ap.run(db, tenant, site_id, None, now, "Europe/Rome", queued.append)
    assert out["accepted"] == queued and len(queued) == 1
    with db.tenant(tenant) as s:
        chosen = s.execute(select(m.proposals).where(m.proposals.c.id == queued[0])).one()
        assert chosen.status == "accepted" and chosen.decided_by == ap.AUTO and ap.source_count(s, chosen.id) >= 2
        left = s.execute(select(m.proposals.c.title, m.proposals.c.status)).all()
    assert all(r.status == "rejected" for r in left if "starlink" in r.title.lower())
    # The day's one article is taken: nothing more until tomorrow.
    assert ap.run(db, tenant, site_id, None, now, "Europe/Rome", queued.append) == {"done": 1}
    # Written and discarded, it does not count: the day still wants one, and
    # after two tries the newsroom stops, so the cost stays known.
    with db.tenant(tenant) as s:
        s.execute(update(m.proposals).where(m.proposals.c.id == queued[0]).values(status="drafted"))
        for v in (1, 2):  # written again once: still one article tried
            s.execute(insert(m.drafts).values(tenant_id=tenant, proposal_id=queued[0], site_id=site_id, version=v,
                                              title="x", body_md="x", status="rejected", decided_by=ap.AUTO,
                                              generated_by="model"))
        since = datetime(2026, 10, 27, 23, tzinfo=UTC)
        assert ap.made_since(s, site_id, since) == (0, 1)
        first = s.execute(select(m.proposals).where(m.proposals.c.id == queued[0])).one()
        s.execute(insert(m.proposals).values(tenant_id=tenant, site_id=site_id, topic_id=first.topic_id,
                                             profile_id=first.profile_id, opportunity=50, title="y",
                                             generated_by="model", status="drafted", decided_by=ap.AUTO, created_at=now))
        other = s.execute(select(func.max(m.proposals.c.id))).scalar()
        s.execute(insert(m.drafts).values(tenant_id=tenant, proposal_id=other, site_id=site_id, version=1,
                                          title="y", body_md="y", status="rejected", decided_by=ap.AUTO,
                                          generated_by="model"))
        assert ap.made_since(s, site_id, since) == (0, 2)
    assert ap.run(db, tenant, site_id, None, now, "Europe/Rome", queued.append) == {"tried": 2, "kept": 0}
    # The plan's articles of the month are over: it waits, it never pays with credits.
    set_plan(db, tenant, max_drafts_month=0)
    assert ap.run(db, tenant, site_id, None, now, "Europe/Rome", queued.append) == {"waiting": "month"}
    set_plan(db, tenant, max_drafts_month=None)
    settings(db, tenant, site_id, hours=[3, 4])
    assert ap.run(db, tenant, site_id, None, now, "Europe/Rome", queued.append) == {"waiting": "hours"}
    settings(db, tenant, site_id, enabled=False)
    assert ap.run(db, tenant, site_id, None, now, "Europe/Rome", queued.append) == {}


def test_an_important_story_goes_out_beyond_the_day_brought_forward_from_tomorrow(db, tenant):
    from datetime import timedelta

    site_id = setup_site(db, tenant)
    props.propose(db, tenant, site_id, opp.score_site(db, tenant, site_id), None, limit=5)
    settings(db, tenant, site_id, per_day=1, borrow=1, extra_min_opportunity=0)
    queued = []
    now = datetime(2026, 10, 28, 11, tzinfo=UTC)
    with db.tenant(tenant) as s:
        s.execute(text("UPDATE editor.proposals SET created_at = :t"), {"t": now})
    run = lambda at: ap.run(db, tenant, site_id, None, at, "Europe/Rome", lambda pid, paid=False: queued.append((pid, paid)))
    assert len(run(now)["accepted"]) == 1  # the day's article
    with db.tenant(tenant) as s:  # a second story, told by the same outlets
        first = s.execute(select(m.proposals).where(m.proposals.c.id == queued[0][0])).one()
        s.execute(insert(m.proposals).values(tenant_id=tenant, site_id=site_id, topic_id=first.topic_id,
                                             profile_id=first.profile_id, opportunity=80, title="Seconda notizia",
                                             generated_by="model", status="proposed", created_at=now))
        second = s.execute(select(func.max(m.proposals.c.id))).scalar()
        for item in s.execute(select(m.proposal_citations.c.item_id).where(
                m.proposal_citations.c.proposal_id == first.id)).scalars().all():
            s.execute(insert(m.proposal_citations).values(tenant_id=tenant, proposal_id=second, item_id=item))
    out = run(now)  # the day is full: one brought forward from tomorrow, free
    assert out["extra"] == "borrowed" and len(out["accepted"]) == 1 and queued[-1][1] is False
    with db.tenant(tenant) as s:
        body = s.execute(select(m.editorial_profiles.c.body).where(m.editorial_profiles.c.site_id == site_id)).scalars().first()
    assert body["settings"]["auto_over"]["borrowed"] == 1
    out = run(now)
    assert out["done"] == 1 and out["borrowed"] == 1 and out["left_out"] >= 0  # the limit of anticipations is reached
    # Tomorrow it owes the day it took: with two a day it makes one.
    settings(db, tenant, site_id, per_day=2, borrow=1, extra_min_opportunity=0)
    over = ap.over_today({"auto_over": {"day": "2026-10-28", "borrowed": 1, "paid": 0, "debt": 0}},
                         (now + timedelta(days=1)).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Rome")))
    assert over == {"day": "2026-10-29", "borrowed": 0, "paid": 0, "debt": 1}
    # What passes the bar and the limit left out is counted.
    with db.tenant(tenant) as s:
        assert ap.left_out(s, site_id, ap.settings_of(body), now) >= 0


def _written(db, tenant, again=None):
    pid, cited = again or accepted_proposal(db, tenant)
    answer = json.dumps({"title": "Bari in fibra", "subtitle": "La rete è completa", "sections": [
        {"heading": "Fatti", "claims": [{"text": "Open Fiber ha lavorato a Bari.", "sources": [cited[0]]},
                                        {"text": "La rete in fibra è completa.", "sources": [cited[0]]},
                                        {"text": "I lavori sono finiti.", "sources": [cited[0]]}]}]})
    with respx.mock() as router:
        router.post(CORE).mock(return_value=chat(answer))
        did = drafts.write_draft(db, tenant, pid, core(), None)
    with db.tenant(tenant) as s:
        site_id = s.execute(select(m.drafts.c.site_id).where(m.drafts.c.id == did)).scalar()
    return did, site_id, (pid, cited)


def test_the_editor_in_chief_lets_it_through_or_stops_it(db, tenant, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    did, site_id, prop = _written(db, tenant)
    settings(db, tenant, site_id, min_sources=1, sections=["Rete", "Mondo"])
    main = pub.add_target(db, tenant, site_id, "webhook", "sito", {"url": "https://hooks.example/it"}, None)
    english = pub.add_target(db, tenant, site_id, "webhook", "site en", {"url": "https://hooks.example/en",
                                                                         "language": "en"}, None)
    tr.set_languages(db, tenant, site_id, ["en"])
    sent, translations = [], []
    with respx.mock() as router:
        check = router.post(CORE).mock(return_value=chat(json.dumps({"publish": True, "problems": [], "section": "rete"})))
        out = ap.finish(db, tenant, did, core(), sent.append, lambda language, d: translations.append(language))
        asked = json.loads(check.calls[0].request.content)["messages"]
        assert "editor-in-chief" in asked[0]["content"] and "superlative" in asked[0]["content"]
        # It checks the article against what the sources say, not against itself.
        assert "Cited sources" in asked[-1]["content"]
        # The reasons of a refusal are read in the panel: in the newsroom's language, in plain words.
        assert "Reasons in: Italiano" in asked[-1]["content"] and "never a rule's number" in asked[0]["content"]
    assert out["target"] == "sito" and translations == ["en"] and sent == [out["publication_id"]]
    with db.tenant(tenant) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).one()
        p = s.execute(select(m.publications).where(m.publications.c.id == sent[0])).one()
    assert d.status == "approved" and d.decided_by == ap.AUTO and "senza revisione umana" in d.body_md
    assert p.status == "approved" and p.target_id == main and p.mode == "publish" and p.requested_by == ap.AUTO
    assert "senza revisione umana" in p.payload["markdown"] and p.payload["section"] == "Rete"
    # The translation goes to the English destination, with the English label.
    with respx.mock() as router:
        router.post(CORE).mock(return_value=chat(json.dumps(ENGLISH)))
        tid = tr.translate(db, tenant, did, "en", core())
    assert ap.owns(db, tenant, tid) and not ap.owns(db, tenant, did)  # asked by hand, it still goes out by itself
    out = ap.finish_translation(db, tenant, tid, sent.append)
    with db.tenant(tenant) as s:
        p = s.execute(select(m.publications).where(m.publications.c.id == sent[-1])).one()
    assert out["target"] == "site en" and p.target_id == english and "without human review" in p.payload["markdown"]
    assert p.payload["section"] == "Rete"  # the translation is in its original's section

    # The next one breaks a rule: set to wait for a person, it stays a draft, with the reason.
    settings(db, tenant, site_id, min_sources=1, sections=["Rete", "Mondo"], on_problem="hold")
    did2, _, _ = _written(db, tenant, prop)
    with respx.mock() as router:
        router.post(CORE).mock(return_value=chat(json.dumps(
            {"publish": False, "problems": ["accuses a named company of fraud without an official source"]})))
        out = ap.finish(db, tenant, did2, core(), sent.append)
    assert out["held"] == ["accuses a named company of fraud without an official source"]
    with db.tenant(tenant) as s:
        assert s.execute(select(m.drafts.c.status).where(m.drafts.c.id == did2)).scalar() == "draft"
        s.execute(text("UPDATE editor.proposals SET decided_by = :a"), {"a": ap.AUTO})
        day = ap.today(s, site_id, datetime(2000, 1, 1, tzinfo=UTC))
    assert day["held"] == 1 and len(sent) == 2
    # Too few sources: stopped before asking the model.
    settings(db, tenant, site_id, min_sources=3, on_problem="hold")
    did3, _, _ = _written(db, tenant, prop)
    with respx.mock(assert_all_called=False) as router:
        llm = router.post(CORE).mock(return_value=chat(json.dumps({"publish": True})))
        out = ap.finish(db, tenant, did3, core(), sent.append)
    assert not llm.calls and "fonti diverse citate: 1, ne servono almeno 3" in out["held"]


def test_the_automatic_newsroom_fixes_or_discards_and_draws_only_what_passes(db, tenant, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    did, site_id, prop = _written(db, tenant)
    settings(db, tenant, site_id, min_sources=1)  # on_problem "fix" by default
    pub.add_target(db, tenant, site_id, "webhook", "sito", {"url": "https://hooks.example/it"}, None)
    sent, covers, notes = [], [], []
    ready = [_written(db, tenant, prop)[0], _written(db, tenant, prop)[0]]  # the versions written again

    def rewrite(old, why):
        notes.append(why)
        return ready.pop(0)

    refuse = chat(json.dumps({"publish": False, "problems": ["the title promises what the article does not say"]}))
    ok = chat(json.dumps({"publish": True, "problems": []}))
    # Refused, written again by the stronger model, then let through: one cover, for the new one.
    with respx.mock() as router:
        router.post(CORE).mock(side_effect=[refuse, ok])
        out = ap.finish(db, tenant, did, core(), sent.append, rewrite=rewrite, cover=covers.append)
    new = out["draft_id"]
    assert out["rewritten_from"] == did and new != did and covers == [new] and len(sent) == 1
    assert "title promises" in notes[0]
    with db.tenant(tenant) as s:
        status = dict(s.execute(select(m.drafts.c.id, m.drafts.c.status).where(m.drafts.c.id.in_([did, new]))).all())
    assert status == {did: "rejected", new: "approved"}
    # Refused twice: discarded, no cover, nothing published, nobody asked.
    did2, _, _ = _written(db, tenant, prop)
    with respx.mock() as router:
        router.post(CORE).mock(side_effect=[refuse, refuse])
        out = ap.finish(db, tenant, did2, core(), sent.append, rewrite=rewrite, cover=covers.append)
    assert out["discarded"] and covers == [new] and len(sent) == 1
    with db.tenant(tenant) as s:
        assert s.execute(select(m.drafts.c.status).where(m.drafts.c.id == out["draft_id"])).scalar() == "rejected"


def test_a_new_site_gets_its_profile_from_the_brief(db, tenant):
    with db.tenant(tenant) as s:
        site_id = repo.add_site(s, tenant, "aitoday.example")
    cfg = {**ap.DEFAULTS, "brief": "Notizie dal mondo curate dall'AI", "sections": ["Mondo", "Tecnologia"]}
    answer = {"name": "AI Today", "summary": "Notizie dal mondo.", "sector": "Informazione generale",
              "audience": "Lettori italiani", "tone": "chiaro", "formats": ["news", "bogus"],
              "subtopics": [{"name": "Mondo", "keywords": ["esteri", "world"]},
                            {"name": "Tecnologia", "keywords": ["tecnologia", "technology"]}]}
    with respx.mock() as router:
        router.post(CORE).mock(return_value=chat(json.dumps(answer)))
        version = ap.profile_from_brief(db, tenant, site_id, "it", core(), cfg)
    with db.tenant(tenant) as s:
        p = s.execute(select(m.editorial_profiles).where(m.editorial_profiles.c.site_id == site_id)).one()
        site = s.execute(select(m.sites).where(m.sites.c.id == site_id)).one()
    assert version == 1 and p.status == "draft" and p.body["sector"]["value"] == "Informazione generale"
    assert [x["name"] for x in p.body["subtopics"]] == ["Mondo", "Tecnologia"] and p.body["style"]["formats"] == ["news"]
    assert p.body["settings"]["auto"]["sections"] == ["Mondo", "Tecnologia"] and p.body["settings"]["proposals_per_day"]
    assert site.name == "AI Today" and site.language == "it"


def test_the_switch_on_the_site_page(db, tenant):
    site_id = setup_site(db, tenant)
    token = auth.create_token(db, tenant, "owner-user", "owner")
    c = TestClient(create_app(db, core=core()))
    assert c.post("/login", data={"token": token}, follow_redirects=False).status_code == 303
    assert "Redazione automatica" in c.get("/site?tab=auto").text
    r = c.post(f"/ui/sites/{site_id}/auto", data={
        "brief": "Notizie sulla rete", "sections": "Fibra\nSatellite", "avoid": "calcio, politica", "per_day": "4",
        "min_opportunity": "40", "min_sources": "2", "start": "7", "end": "23", "enabled": "1", "credit": "1"})
    assert "Redazione automatica accesa" in r.text and "Ferma subito" in r.text
    with db.tenant(tenant) as s:
        cfg = ap.settings_of(prof_body(s, site_id))
    assert cfg["enabled"] and cfg["avoid"] == ["calcio", "politica"] and cfg["sections"] == ["Fibra", "Satellite"]
    page = c.get("/site?tab=auto").text
    assert "Ultimo giro" in page and "non ancora fatto" in page
    assert ">Italiano</option>" in page and "Il piano ha" not in page  # no monthly limit, no warning
    # The articles of the day against the plan's articles of the month.
    set_plan(db, tenant, max_drafts_month=60)
    page = c.get("/site?tab=auto").text
    assert "4 articoli al giorno qui fanno circa 120 articoli in 30 giorni, ma il piano ne ha 60" in page
    assert "ne restano 60: bastano per circa 15 giorni" in page and "si ferma fino al mese dopo" in page
    set_plan(db, tenant, max_drafts_month=600)
    assert "Circa 120 articoli in 30 giorni" in c.get("/site?tab=auto").text
    ap.remember(db, tenant, site_id, {"waiting": "hours"}, datetime(2026, 11, 2, 4, tzinfo=UTC))
    ap.remember(db, tenant, site_id, {"accepted": [], "wanted": 1}, datetime(2026, 11, 2, 9, tzinfo=UTC))
    page = c.get("/site?tab=auto").text
    assert "nessuna proposta adatta" in page and "almeno 40" in page
    with db.tenant(tenant) as s:  # saving the settings keeps the last round
        assert ap.save(s, site_id, {**cfg, "per_day": 5}) and prof_body(s, site_id)["settings"]["auto_last"]["wanted"] == 1
    assert "Redazione automatica fermata" in c.post(f"/ui/sites/{site_id}/auto/stop").text
    with db.tenant(tenant) as s:
        assert not ap.settings_of(prof_body(s, site_id))["enabled"]
    r = c.post(f"/ui/sites/{site_id}/auto", data={"brief": "Notizie sulla rete", "action": "profile", "language": "it"})
    with db.tenant(tenant) as s:
        job = s.execute(text("SELECT kind, args FROM editor.jobs WHERE kind = 'brief'")).one()
    args = job.args if isinstance(job.args, dict) else json.loads(job.args)
    assert args["site_id"] == site_id and args["auto"]["brief"] == "Notizie sulla rete"


def prof_body(s, site_id):
    return s.execute(select(m.editorial_profiles.c.body).where(
        m.editorial_profiles.c.site_id == site_id, m.editorial_profiles.c.status == "approved")).scalar()


def test_sources_shown_discreetly():
    body = ("# T\n\nPrima frase.[^1] Seconda.[^2][^3]\n\n## Fonti\n\n"
            "[^1]: [Rete completa](https://a.example/1), Open Fiber\n"
            "[^2]: [Altro](https://a.example/2), Open Fiber\n"
            "[^3]: [Notizia](https://b.example/x)\n")
    out = drafts.discreet_sources(body)
    assert "[^" not in out and "Prima frase. Seconda." in out
    assert out.endswith("**Fonti:** [Open Fiber](https://a.example/1) · [Notizia](https://b.example/x)\n")
    assert drafts.discreet_sources("# T\n\nTesto.\n") == "# T\n\nTesto.\n"


def test_a_site_with_discreet_sources_publishes_them_in_one_line(db, tenant, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    did, site_id, _ = _written(db, tenant)
    settings(db, tenant, site_id, min_sources=1)
    pub.add_target(db, tenant, site_id, "webhook", "sito", {"url": "https://hooks.example/it"}, None)
    with db.tenant(tenant) as s:
        assert prof.set_sources_style(s, site_id, "list")
    sent = []
    with respx.mock() as router:
        router.post(CORE).mock(return_value=chat(json.dumps({"publish": True, "problems": []})))
        ap.finish(db, tenant, did, core(), sent.append)
    with db.tenant(tenant) as s:
        p = s.execute(select(m.publications).where(m.publications.c.id == sent[0])).one()
        d = s.execute(select(m.drafts.c.body_md).where(m.drafts.c.id == did)).scalar()
    assert "[^1]" in d  # the draft keeps its notes
    assert "[^" not in p.payload["markdown"] and "**Fonti:** [" in p.payload["markdown"]
    assert "senza revisione umana" in p.payload["markdown"]
    token = auth.create_token(db, tenant, "owner-user", "owner")
    c = TestClient(create_app(db, core=core()))
    c.post("/login", data={"token": token}, follow_redirects=False)
    assert "Fonti negli articoli salvate" in c.post(f"/ui/sites/{site_id}/sources-style", data={"style": "notes"}).text
    with db.tenant(tenant) as s:
        assert prof_body(s, site_id)["settings"]["sources_style"] == "notes"


def test_the_automatic_newsroom_looks_for_sources_once_a_day():
    now = datetime(2026, 11, 2, 20, tzinfo=UTC)
    assert ap.sources_due(None, now) and ap.sources_due("not a date", now)
    assert not ap.sources_due(datetime(2026, 11, 2, 7, tzinfo=UTC).isoformat(), now)
    assert ap.sources_due(datetime(2026, 11, 1, 19, tzinfo=UTC).isoformat(), now)


def test_the_morning_takes_its_share(db, tenant):
    site_id = setup_site(db, tenant)
    props.propose(db, tenant, site_id, opp.score_site(db, tenant, site_id), None, limit=5)
    settings(db, tenant, site_id, per_day=8, hours=[7, 23])
    set_plan(db, tenant, max_drafts_month=None)
    seven = datetime(2026, 11, 3, 6, tzinfo=UTC)  # 7:00 in Rome, the first hour of the window
    with db.tenant(tenant) as s:
        s.execute(text("UPDATE editor.proposals SET created_at = :t"), {"t": seven})
    out = ap.run(db, tenant, site_id, None, seven, "Europe/Rome", lambda pid: None)
    assert out["wanted"] == 4  # half of the day at once, not 8 / 16 hours
    assert ap.due_by({"per_day": 15, "hours": [7, 24], "morning_share": 50}, datetime(2026, 11, 3, 7)) == 8
    assert ap.due_by({"per_day": 15, "hours": [7, 24], "morning_share": 50}, datetime(2026, 11, 3, 23)) == 15


def test_a_late_start_catches_up_and_a_good_single_news_counts(db, tenant):
    site_id = setup_site(db, tenant)
    props.propose(db, tenant, site_id, opp.score_site(db, tenant, site_id), None, limit=5)
    settings(db, tenant, site_id, per_day=8, hours=[7, 23])
    set_plan(db, tenant, max_drafts_month=None)
    nine = datetime(2026, 11, 3, 8, tzinfo=UTC)  # 9:00 in Rome: the server came up after the first hour
    with db.tenant(tenant) as s:
        s.execute(text("UPDATE editor.proposals SET created_at = :t, opportunity = 30"), {"t": nine})
    cfg = {**ap.settings_of(None), "min_sources": 0, "avoid": [], "min_opportunity": 40}
    with db.tenant(tenant) as s:
        assert len(ap.choose(s, site_id, cfg, nine, 5)) > 0  # 30 is over half of 40
        cfg["min_opportunity"] = 70
        assert ap.choose(s, site_id, cfg, nine, 5) == []
    out = ap.run(db, tenant, site_id, None, nine, "Europe/Rome", lambda pid: None)
    assert out["wanted"] >= 4  # the morning's share is still owed, not 8 / 14 hours


def test_other_outlets_telling_the_same_story_count_as_sources(db, tenant):
    site_id = setup_site(db, tenant)
    props.propose(db, tenant, site_id, opp.score_site(db, tenant, site_id), None, limit=5)
    si = m.source_items
    with db.tenant(tenant) as s:
        pid, item = s.execute(select(m.proposal_citations.c.proposal_id, m.proposal_citations.c.item_id)
                              .limit(1)).one()
        topic = s.execute(select(m.proposals.c.topic_id).where(m.proposals.c.id == pid)).scalar()
        # The story as one feed told it: one source.
        s.execute(m.proposal_citations.delete().where(m.proposal_citations.c.proposal_id == pid,
                                                      m.proposal_citations.c.item_id != item))
        if topic:
            s.execute(m.topic_items.delete().where(m.topic_items.c.topic_id == topic))
        row = s.execute(select(si).where(si.c.id == item)).one()._asdict()
        before = ap.source_count(s, pid)
        src = s.execute(select(m.sources).where(m.sources.c.id == row["source_id"])).one()._asdict()
        other = s.execute(insert(m.sources).values(**{**{k: v for k, v in src.items() if k != "id"},
                                                      "tenant_id": tenant, "name": "Altra testata",
                                                      "url": "https://altra.example/feed"})
                          .returning(m.sources.c.id)).scalar_one()
        # Another outlet with the same story, outside the topic: the article reads it too.
        s.execute(insert(si).values(**{**{k: v for k, v in row.items() if k != "id"}, "source_id": other,
                                       "tenant_id": tenant, "url": "https://altra.example/stessa-notizia",
                                       "canonical_url": "https://altra.example/stessa-notizia",
                                       "url_hash": "a" * 64, "content_hash": "b" * 64, "duplicate_of": None,
                                       "fetched_at": datetime.now(UTC)}))
        assert ap.source_count(s, pid) == before + 1 and ap.source_count(s, pid, 1) <= before
