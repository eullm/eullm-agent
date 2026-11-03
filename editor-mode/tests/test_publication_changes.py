"""After publication the director keeps the article in hand: edits it
freely, updates it on the site, writes it again, withdraws it."""

import json
import socket

import httpx
import pytest
import respx
from sqlalchemy import select

from editor import drafts, models as m, publishing as pub, seo
from test_publishing import ASTRO_POST, ASTRO_POST_2, _send, _target, approved_draft, public_resolver


def _claims(db, tenant, did):
    with db.tenant(tenant) as s:
        return drafts.stored_claims(s, did)


def test_the_free_editor_adds_removes_and_moves_sentences(db, tenant):
    did, _ = approved_draft(db, tenant)
    before = _claims(db, tenant, did)
    text = drafts.as_text(before)
    assert "[1]" in text
    lines = [x for x in text.splitlines() if x.strip() and not x.startswith("#")]
    new = "## Cosa cambia\n" + lines[0] + "\nUna frase scritta dal direttore, senza fonte.\n\n## Il contesto\n" + "\n".join(lines[1:])
    res = drafts.edit_text(db, tenant, did, "Bari in fibra, cosa cambia", "Sottotitolo nuovo", new)
    assert res["was_approved"] and res["status"] == "draft"  # approved again before it goes out
    after = _claims(db, tenant, did)
    assert [c["section"] for c in after][:2] == ["Cosa cambia", "Cosa cambia"]
    own = next(c for c in after if c["text"] == "Una frase scritta dal direttore, senza fonte.")
    assert own["sources"] == []
    with db.tenant(tenant) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).one()
    assert d.title == "Bari in fibra, cosa cambia" and "## Cosa cambia" in d.body_md and "## Il contesto" in d.body_md
    assert "Una frase scritta dal direttore, senza fonte." in d.body_md and not d.flags
    with pytest.raises(ValueError, match=r"\[9\]"):
        drafts.edit_text(db, tenant, did, "Bari in fibra", "", "Una frase. [9]")
    with pytest.raises(ValueError, match="empty"):
        drafts.edit_text(db, tenant, did, "Bari in fibra", "", "## Solo un titolo\n")


def _wordpress(db, tenant, monkeypatch):
    did, site_id = approved_draft(db, tenant)
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    monkeypatch.setenv(pub.secret_variable(tenant, "WP"), "app-pass")
    target = pub.add_target(db, tenant, site_id, "wordpress", "Sito", {"url": "https://blog.example", "user": "e"}, "WP")
    pid = pub.request(db, tenant, did, target, "publish", "r")
    pub.decide(db, tenant, pid, True, "francesco")
    with respx.mock() as router:
        router.post("https://blog.example/wp-json/wp/v2/posts").mock(
            return_value=httpx.Response(201, json={"id": 77, "link": "https://blog.example/bari/"}))
        pub.Publisher(resolver=public_resolver).run(db, tenant, pid)
    return did, site_id, pid


def test_wordpress_article_is_updated_and_withdrawn(db, tenant, monkeypatch):
    did, site_id, pid = _wordpress(db, tenant, monkeypatch)
    publisher = pub.Publisher(resolver=public_resolver)
    text = drafts.as_text(_claims(db, tenant, did)) + "Aggiunta del direttore.\n"
    drafts.edit_text(db, tenant, did, "Bari in fibra, aggiornato", "", text)
    with pytest.raises(pub.PublishError, match="approve"):
        publisher.update(db, tenant, pid, "francesco")
    drafts.decide(db, tenant, did, True, "francesco")
    with respx.mock() as router:
        put = router.post("https://blog.example/wp-json/wp/v2/posts/77").mock(
            return_value=httpx.Response(200, json={"id": 77, "link": "https://blog.example/bari/"}))
        assert publisher.update(db, tenant, pid, "francesco") == {"status": "updated", "url": "https://blog.example/bari/"}
    sent = json.loads(put.calls[0].request.content)
    assert sent["title"] == "Bari in fibra, aggiornato" and "Aggiunta del direttore." in sent["content"]
    with db.tenant(tenant) as s:
        row = s.execute(select(m.publications).where(m.publications.c.id == pid)).one()
        assert row.updated_by == "francesco" and row.updated_at is not None
        assert seo.site_links(s, site_id, "Bari in fibra aggiornato", "") != []
    with respx.mock() as router:
        gone = router.delete("https://blog.example/wp-json/wp/v2/posts/77").mock(return_value=httpx.Response(200, json={}))
        assert publisher.withdraw(db, tenant, pid, "francesco")["status"] == "withdrawn"
    assert gone.called
    with db.tenant(tenant) as s:
        row = s.execute(select(m.publications).where(m.publications.c.id == pid)).one()
        assert row.withdrawn_by == "francesco" and row.status == "published"
        assert seo.site_links(s, site_id, "Bari in fibra aggiornato", "") == []  # no longer linked


def test_a_failed_change_leaves_the_article_as_it_was(db, tenant, monkeypatch):
    did, _, pid = _wordpress(db, tenant, monkeypatch)
    with respx.mock() as router:
        router.delete("https://blog.example/wp-json/wp/v2/posts/77").mock(return_value=httpx.Response(500))
        out = pub.Publisher(resolver=public_resolver).withdraw(db, tenant, pid, "francesco")
    assert out["status"] == "failed"
    with db.tenant(tenant) as s:
        row = s.execute(select(m.publications).where(m.publications.c.id == pid)).one()
    assert row.withdrawn_at is None and "500" in row.error


def test_github_article_is_updated_in_place_and_withdrawn_with_its_lines(db, tenant, monkeypatch):
    from github_fake import FakeGitHub
    did, site_id = approved_draft(db, tenant)
    target = _target(db, tenant, site_id, monkeypatch, path="src/content/blog", verify=False,
                     url="https://sito.it/blog/{slug}/")
    gh = FakeGitHub({
        "src/content/blog/vecchio-articolo.md": ASTRO_POST, "src/content/blog/altro.md": ASTRO_POST_2,
        "public/llms.txt": "# Sito\n\n## Blog\n\n- [Vecchio articolo](https://sito.it/blog/vecchio-articolo/): x\n",
        "public/sitemap.xml": '<?xml version="1.0"?>\n<urlset>\n  <url><loc>https://sito.it/</loc></url>\n</urlset>\n'})
    pid, out = _send(db, tenant, did, target, "publish", gh)
    assert out["status"] == "published"
    path = "src/content/blog/bari-in-fibra.md"
    assert path in gh.files() and "https://sito.it/blog/bari-in-fibra/" in gh.text("public/llms.txt")
    publisher = pub.Publisher(resolver=public_resolver, sleep=lambda s: None)

    text = drafts.as_text(_claims(db, tenant, did)) + "Una riga nuova del direttore.\n"
    drafts.edit_text(db, tenant, did, "Bari in fibra: nuovo titolo", "", text)
    drafts.decide(db, tenant, did, True, "francesco")
    with respx.mock(assert_all_called=False) as router:
        gh.route(router)
        res = publisher.update(db, tenant, pid, "francesco")
    assert res == {"status": "updated", "url": "https://sito.it/blog/bari-in-fibra/"}
    md = gh.text(path)  # same file, same address
    assert "Una riga nuova del direttore." in md and "nuovo titolo" in md
    llms = gh.text("public/llms.txt")
    assert llms.count("https://sito.it/blog/bari-in-fibra/") == 1 and "nuovo titolo" in llms

    with respx.mock(assert_all_called=False) as router:
        gh.route(router)
        assert publisher.withdraw(db, tenant, pid, "francesco")["status"] == "withdrawn"
    assert path not in gh.files() and "bari-in-fibra" not in gh.text("public/llms.txt")
    assert "bari-in-fibra" not in gh.text("public/sitemap.xml") and "vecchio-articolo" in gh.text("public/llms.txt")

    with respx.mock(assert_all_called=False) as router:  # and back online
        gh.route(router)
        assert publisher.update(db, tenant, pid, "francesco")["status"] == "updated"
    assert path in gh.files()
    with db.tenant(tenant) as s:
        assert s.execute(select(m.publications.c.withdrawn_at).where(m.publications.c.id == pid)).scalar() is None


def test_a_version_written_again_replaces_the_one_online_at_the_same_address(db, tenant, monkeypatch):
    did, site_id, pid = _wordpress(db, tenant, monkeypatch)
    with db.tenant(tenant) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).one()
        claims = drafts.stored_claims(s, did)
        new = s.execute(m.drafts.insert().values(
            tenant_id=tenant, proposal_id=d.proposal_id, site_id=d.site_id, version=d.version + 1, title="Bari, versione due",
            subtitle="", body_md="# Bari, versione due\n", language=d.language, status="draft", generated_by="m",
        ).returning(m.drafts.c.id)).scalar_one()
        cid = s.execute(m.draft_claims.insert().values(tenant_id=tenant, draft_id=new, ordinal=0, section=None,
                                                       text=claims[0]["text"]).returning(m.draft_claims.c.id)).scalar_one()
        s.execute(m.draft_claim_sources.insert().values(tenant_id=tenant, claim_id=cid, item_id=claims[0]["sources"][0]))
    drafts.mark_replacement(db, tenant, new, did)
    publisher = pub.Publisher(resolver=public_resolver)
    with pytest.raises(pub.PublishError, match="approve"):
        publisher.update(db, tenant, pid, "francesco", draft_id=new)
    drafts.decide(db, tenant, new, True, "francesco")
    with respx.mock() as router:
        put = router.post("https://blog.example/wp-json/wp/v2/posts/77").mock(
            return_value=httpx.Response(200, json={"id": 77, "link": "https://blog.example/bari/"}))
        assert publisher.update(db, tenant, pid, "francesco", draft_id=new)["status"] == "updated"
    sent = json.loads(put.calls[0].request.content)
    assert sent["title"] == "Bari, versione due"
    with db.tenant(tenant) as s:
        row = s.execute(select(m.publications).where(m.publications.c.id == pid)).one()
        assert row.draft_id == new and (s.execute(select(m.drafts.c.seo).where(m.drafts.c.id == new)).scalar() or {})["replaces"] == did


def test_the_draft_page_gives_the_director_the_controls(db, tenant, monkeypatch):
    from fastapi.testclient import TestClient

    from editor import auth
    from editor.api import create_app
    did, _, pid = _wordpress(db, tenant, monkeypatch)
    c = TestClient(create_app(db))
    c.post("/login", data={"token": auth.create_token(db, tenant, "dir", "owner")}, follow_redirects=False)
    page = c.get(f"/drafts/{did}").text
    assert f'action="/ui/publications/{pid}/update"' in page and f'action="/ui/publications/{pid}/withdraw"' in page
    assert f'action="/ui/drafts/{did}/redo"' in page
    editor = c.get(f"/drafts/{did}?edit=1").text
    assert 'name="body"' in editor and "Fonti da citare" in editor
    body = drafts.as_text(_claims(db, tenant, did)) + "Riga del direttore.\n"
    r = c.post(f"/ui/drafts/{did}/edit", data={"title": "Bari in fibra", "subtitle": "", "body": body})
    assert r.status_code == 200 and "va riapprovata" in r.text
    assert "approvalo di nuovo" in c.get(f"/drafts/{did}").text
    assert "Spunta la conferma" in c.post(f"/ui/publications/{pid}/withdraw", data={}).text
    assert "Ritiro in corso" in c.post(f"/ui/publications/{pid}/withdraw", data={"confirm": "1"}).text
    with db.tenant(tenant) as s:
        job = s.execute(select(m.jobs.c.kind, m.jobs.c.args).where(m.jobs.c.kind == "change")).one()
    assert (job.args if isinstance(job.args, dict) else json.loads(job.args))["op"] == "withdraw"
