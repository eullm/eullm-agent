"""Connecting a site: the guided path from a domain to a site the newsroom
can update. The credential is tried for real before anything is saved."""

import json
import socket

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from editor import connect, models as m, publishing as pub, vault
from editor.api import create_app
from github_fake import FakeGitHub
from test_publishing import ASTRO_POST, approved_draft, public_resolver
from test_ui import logged_in

WP = "https://blog.example"


def wp_site(router, *, auth=True, role_caps=None, categories=None, posts=3):
    router.get(f"{WP}/wp-json/").mock(return_value=httpx.Response(200, json={
        "name": "Il Blog &amp; Co", "description": "Notizie", "namespaces": ["wp/v2"]}))
    me = router.get(f"{WP}/wp-json/wp/v2/users/me")
    if auth:
        me.mock(return_value=httpx.Response(200, json={
            "name": "Redazione", "roles": ["editor"],
            "capabilities": role_caps or {"edit_posts": True, "publish_posts": True, "upload_files": True}}))
    else:
        me.mock(return_value=httpx.Response(401, json={"code": "incorrect_password"}))
    router.get(f"{WP}/wp-json/wp/v2/categories").mock(return_value=httpx.Response(200, json=categories or [
        {"id": 5, "name": "Tecnologia &amp; AI", "slug": "tecnologia-ai", "count": 12},
        {"id": 1, "name": "Senza categoria", "slug": "senza-categoria", "count": 3},
        {"id": 9, "name": "Economia", "slug": "economia", "count": 4}]))
    router.get(f"{WP}/wp-json/wp/v2/posts").mock(return_value=httpx.Response(
        200, json=[{"id": 1}] if posts else [], headers={"X-WP-Total": str(posts)}))


def test_wordpress_is_tried_and_its_categories_read():
    with respx.mock(assert_all_called=False) as router:
        wp_site(router)
        found = connect.wordpress("blog.example/wp-admin/", "redazione", "abcd efgh", resolver=public_resolver)
    assert found["base"] == WP and found["name"] == "Il Blog & Co"
    assert found["can_publish"] and found["can_upload"]
    assert [c["name"] for c in found["categories"]] == ["Tecnologia & AI", "Senza categoria", "Economia"]
    assert found["sections"] == ["Tecnologia & AI", "Economia"]  # the default category says nothing about the site
    assert found["posts"] == 3


def test_wordpress_without_pretty_permalinks_goes_by_rest_route():
    """wp.aitoday.it, 2026-11-05: /wp-json/ answered 404 from the web server,
    ?rest_route= worked; the user check went to /wp-json/ and failed."""
    page404 = httpx.Response(404, text="<h1>Not Found</h1>", headers={"content-type": "text/html"})
    with respx.mock(assert_all_called=False) as router:
        router.route(host="blog.example", path__startswith="/wp-json/").mock(return_value=page404)
        router.get(f"{WP}/", params={"rest_route": "/"}).mock(return_value=httpx.Response(200, json={
            "name": "Blog", "namespaces": ["wp/v2"]}))
        me = router.get(f"{WP}/", params__contains={"rest_route": "/wp/v2/users/me"}).mock(
            return_value=httpx.Response(200, json={"name": "R", "capabilities": {"edit_posts": True}}))
        router.get(f"{WP}/", params__contains={"rest_route": "/wp/v2/categories"}).mock(
            return_value=httpx.Response(200, json=[{"id": 9, "name": "Economia", "slug": "economia", "count": 4}]))
        router.get(f"{WP}/", params__contains={"rest_route": "/wp/v2/posts"}).mock(
            return_value=httpx.Response(200, json=[], headers={"X-WP-Total": "0"}))
        found = connect.wordpress(WP, "redazione", "abcd efgh", resolver=public_resolver)
    assert me.called and dict(me.calls[0].request.url.params)["context"] == "edit"
    assert found["sections"] == ["Economia"]
    assert pub.wp_url(WP, "wp/v2/posts", "a=1", plain=True) == f"{WP}/?rest_route=/wp/v2/posts&a=1"


def test_wordpress_says_what_is_wrong():
    with respx.mock(assert_all_called=False) as router:
        wp_site(router, auth=False)
        with pytest.raises(connect.ConnectError) as e:
            connect.wordpress(WP, "redazione", "sbagliata", resolver=public_resolver)
    assert e.value.code == "auth"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{WP}/wp-json/").mock(return_value=httpx.Response(404))
        router.get(f"{WP}/").mock(return_value=httpx.Response(404))
        with pytest.raises(connect.ConnectError) as e:
            connect.wordpress(WP, "u", "p", resolver=public_resolver)
    assert e.value.code == "not_wordpress"
    with respx.mock(assert_all_called=False) as router:
        wp_site(router, role_caps={"read": True})
        with pytest.raises(connect.ConnectError) as e:
            connect.wordpress(WP, "u", "p", resolver=public_resolver)
    assert e.value.code == "role"
    with pytest.raises(connect.ConnectError) as e:
        connect.wordpress("http://blog.example", "u", "p", resolver=public_resolver)
    assert e.value.code == "https"
    with pytest.raises(connect.ConnectError) as e:
        connect.wordpress(WP, "", "", resolver=public_resolver)
    assert e.value.code == "credentials_missing"


def astro_repo(**extra):
    files = {"package.json": json.dumps({"dependencies": {"astro": "^5"}}), "vercel.json": "{}",
             "public/llms.txt": "# Sito\n", "content/blog/vecchio.md": ASTRO_POST}
    files.update(extra)
    return FakeGitHub(files)


def test_github_reads_the_site_and_where_the_articles_go():
    gh = astro_repo()
    with respx.mock(assert_all_called=False) as router:
        gh.route(router)
        found = connect.github("https://github.com/me/site", "tok", resolver=public_resolver)
    assert found["repo"] == "me/site" and found["branch"] == "main"
    assert found["generator"] == "Astro" and found["host"] == "vercel"
    assert found["articles_path"] == "content/blog" and found["articles_found"] == 1
    assert found["format"]["ext"] == ".md" and found["has_llms"] and not found["has_sitemap"]
    assert gh.token == "Bearer tok"


def test_github_without_posts_or_known_host():
    gh = FakeGitHub({"README.md": "sito", "wrangler.toml": "name = 'x'"})
    with respx.mock(assert_all_called=False) as router:
        gh.route(router)
        found = connect.github("me/site", "tok", resolver=public_resolver)
    assert found["host"] == "cloudflare" and found["articles_path"] is None and found["format"] is None


def test_github_failures_have_a_reason():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/me/site").mock(return_value=httpx.Response(404, json={}))
        with pytest.raises(connect.ConnectError) as e:
            connect.github("me/site", "tok", resolver=public_resolver)
    assert e.value.code == "repo_missing"
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/me/site").mock(return_value=httpx.Response(401, json={}))
        with pytest.raises(connect.ConnectError) as e:
            connect.github("me/site", "bad", resolver=public_resolver)
    assert e.value.code == "token_bad"
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/me/site").mock(return_value=httpx.Response(
            200, json={"default_branch": "main", "permissions": {"pull": True, "push": False}}))
        with pytest.raises(connect.ConnectError) as e:
            connect.github("me/site", "ro", resolver=public_resolver)
    assert e.value.code == "no_write"
    with pytest.raises(connect.ConnectError) as e:
        connect.github("non un repo", "tok", resolver=public_resolver)
    assert e.value.code == "repo_name"


def test_the_article_goes_in_the_category_of_its_section(db, tenant, monkeypatch):
    did, site_id = approved_draft(db, tenant)
    config = {"url": WP, "user": "e", "categories": [{"id": 5, "name": "Tecnologia & AI", "slug": "tecnologia-ai"}]}
    target = type("T", (), {"config": config})()
    post = pub.Publisher._wordpress_post(target, {"title": "T", "html": "<p>x</p>", "subtitle": "s",
                                                  "section": "tecnologia & ai"}, "publish")
    assert post["categories"] == [5]
    post = pub.Publisher._wordpress_post(target, {"title": "T", "html": "<p>x</p>", "subtitle": "s",
                                                  "section": "Sport"}, "publish")
    assert "categories" not in post  # no such category: the site's default stays
    assert connect.category_id(config, "tecnologia-ai") == 5 and connect.category_id(None, "x") is None


def test_adding_a_site_leads_to_the_connection_and_the_connection_to_the_analysis(db, tenant, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    monkeypatch.setattr(connect, "resolver", public_resolver)
    monkeypatch.setenv(vault.KEY_ENV, vault.new_key())
    c = logged_in(db, tenant, "owner")
    r = c.post("/ui/sites", data={"domain": "nuovo.example", "back": "/start"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/connect"
    with db.tenant(tenant) as s:
        site_id = s.execute(select(m.sites.c.id).where(m.sites.c.domain == "nuovo.example")).scalar()
        assert site_id is not None
        assert not s.execute(select(m.jobs.c.id).where(m.jobs.c.kind == "analysis")).first()  # not yet read
    page = c.get("/connect")
    assert page.status_code == 200 and "nuovo.example" in page.text and "WordPress" in page.text
    assert "Password delle applicazioni" in c.get("/connect?kind=wordpress").text
    assert "github.com/settings/personal-access-tokens/new" in c.get("/connect?kind=github").text
    assert "vercel.com/new" in c.get("/connect?kind=github&host=vercel").text

    # A wrong password: nothing saved, the reason on the page, the analysis not started.
    with respx.mock(assert_all_called=False) as router:
        wp_site(router, auth=False)
        bad = c.post(f"/ui/connect/{site_id}/check", data={
            "kind": "wordpress", "address": WP, "user": "redazione", "secret": "sbagliata"})
    assert bad.status_code == 200 and "ha rifiutato utente o password" in bad.text
    with db.tenant(tenant) as s:
        assert not s.execute(select(m.publish_targets.c.id).where(m.publish_targets.c.site_id == site_id)).first()
        assert not s.execute(select(m.jobs.c.id).where(m.jobs.c.kind == "analysis")).first()

    with respx.mock(assert_all_called=False) as router:
        wp_site(router)
        ok = c.post(f"/ui/connect/{site_id}/check", data={
            "kind": "wordpress", "address": WP, "user": "redazione", "secret": "abcd efgh ijkl"})
    assert ok.status_code == 200 and "Collegamento verificato" in ok.text
    assert "Tecnologia &amp; AI" in ok.text and "Economia" in ok.text
    with db.tenant(tenant) as s:
        t = s.execute(select(m.publish_targets).where(m.publish_targets.c.site_id == site_id)).one()
        assert t.kind == "wordpress" and t.config["url"] == WP and t.config["user"] == "redazione"
        assert [x["id"] for x in t.config["categories"]] == [5, 1, 9]
        assert t.secret_env == "WP_NUOVO_EXAMPLE"
        assert s.execute(select(m.jobs.c.kind).where(m.jobs.c.kind == "analysis")).scalar() == "analysis"
        assert connect.known_categories(s, site_id) == {"Tecnologia & AI": 12, "Economia": 4}
    assert vault.get(db, tenant, "WP_NUOVO_EXAMPLE") == "abcd efgh ijkl"
    # The same site connected again updates the destination instead of adding a second one.
    with respx.mock(assert_all_called=False) as router:
        wp_site(router)
        c.post(f"/ui/connect/{site_id}/check", data={"kind": "wordpress", "address": WP, "user": "redazione",
                                                      "secret": "nuova password"})
    with db.tenant(tenant) as s:
        assert len(s.execute(select(m.publish_targets.c.id).where(m.publish_targets.c.site_id == site_id)).all()) == 1
    assert vault.get(db, tenant, "WP_NUOVO_EXAMPLE") == "nuova password"
    steps = c.get("/start").text
    assert "Sito collegato" in steps

    # A WordPress with no articles yet: nothing to read, the page asks for the context instead.
    c.post("/ui/sites", data={"domain": "vuoto.example", "back": "/start"}, follow_redirects=False)
    with db.tenant(tenant) as s:
        empty_id = s.execute(select(m.sites.c.id).where(m.sites.c.domain == "vuoto.example")).scalar()
    with respx.mock(assert_all_called=False) as router:
        wp_site(router, posts=0)
        done = c.post(f"/ui/connect/{empty_id}/check", data={
            "kind": "wordpress", "address": WP, "user": "redazione", "secret": "abcd"})
    assert "Collegamento verificato" in done.text and "Scrivi il contesto" in done.text
    with db.tenant(tenant) as s:
        assert s.execute(select(func.count()).select_from(m.jobs).where(m.jobs.c.kind == "analysis")).scalar() == 1


def test_the_analysis_starts_from_the_categories_the_site_already_has(db, tenant, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    from editor import profile as prof
    from editor.site import SiteSnapshot

    from editor import repo

    with db.tenant(tenant) as s:
        site_id = repo.add_site(s, tenant, "nuovo.example")
    pub.add_target(db, tenant, site_id, "wordpress", "Sito", {
        "url": WP, "user": "e", "categories": [{"id": 5, "name": "Tecnologia", "slug": "tecnologia", "count": 7},
                                               {"id": 1, "name": "Senza categoria", "slug": "senza-categoria", "count": 9}]}, None)

    class Crawler:
        def analyse(self, domain):
            return SiteSnapshot(domain=domain, title="Blog", description="", status="partial", language="it")

    prof.analyse_site(db, tenant, "nuovo.example", Crawler(), None)
    with db.tenant(tenant) as s:
        body = s.execute(text("SELECT body FROM editor.editorial_profiles WHERE site_id = :s ORDER BY id DESC LIMIT 1"),
                         {"s": site_id}).scalar()
    body = body if isinstance(body, dict) else json.loads(body)
    assert body["settings"]["auto"]["sections"] == ["Tecnologia"]
    assert any(x["name"] == "Tecnologia" for x in body["subtopics"]) or "Tecnologia" in json.dumps(body)


def test_github_connection_is_saved_with_what_was_found(db, tenant, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    monkeypatch.setattr(connect, "resolver", public_resolver)
    monkeypatch.setenv(vault.KEY_ENV, vault.new_key())
    c = logged_in(db, tenant, "owner")
    did, site_id = approved_draft(db, tenant)
    gh = astro_repo()
    with respx.mock(assert_all_called=False) as router:
        gh.route(router)
        ok = c.post(f"/ui/connect/{site_id}/check", data={
            "kind": "github", "address": "me/site", "secret": "github_pat_x", "host": ""})
    assert ok.status_code == 200 and "Collegamento verificato" in ok.text and "Astro" in ok.text and "Vercel" in ok.text
    with db.tenant(tenant) as s:
        t = s.execute(select(m.publish_targets).where(m.publish_targets.c.site_id == site_id,
                                                      m.publish_targets.c.kind == "github")).one()
    assert t.config["repo"] == "me/site" and t.config["path"] == "content/blog" and t.config["host"] == "vercel"
    assert t.config.get("verify") is not False and vault.get(db, tenant, t.secret_env) == "github_pat_x"


def test_connecting_later_reads_the_site_without_a_destination(db, tenant):
    c = logged_in(db, tenant, "owner")
    c.post("/ui/sites", data={"domain": "senza.example"}, follow_redirects=False)
    with db.tenant(tenant) as s:
        site_id = s.execute(select(m.sites.c.id).where(m.sites.c.domain == "senza.example")).scalar()
    r = c.post(f"/ui/connect/{site_id}/later", follow_redirects=False)
    assert r.status_code == 303
    with db.tenant(tenant) as s:
        assert s.execute(select(m.jobs.c.kind).where(m.jobs.c.kind == "analysis")).scalar() == "analysis"
        assert not s.execute(select(m.publish_targets.c.id).where(m.publish_targets.c.site_id == site_id)).first()


def test_only_owners_connect(db, tenant):
    for role in ("viewer", "editor"):
        c = logged_in(db, tenant, role)
        assert c.get("/connect").status_code == 403
        assert c.post("/ui/connect/1/later").status_code == 403


def test_the_desk_of_an_automatic_site_shows_its_day_and_does_not_ask_to_decide(db, tenant):
    from datetime import UTC, datetime, timedelta

    from editor import autopilot, profile as prof

    c = logged_in(db, tenant, "owner")
    did, site_id = approved_draft(db, tenant)
    with db.tenant(tenant) as s:
        assert prof.approved_profile(s, site_id) is not None
        autopilot.save(s, site_id, {"enabled": True, "per_day": 6})
        s.execute(text("UPDATE editor.proposals SET status = 'proposed', created_at = :t WHERE site_id = :s"),
                  {"t": datetime.now(UTC) - timedelta(hours=40), "s": site_id})
    desk = c.get("/").text
    assert "La redazione automatica di blog.example, oggi" in desk and "Oggi:" in desk
    assert "la sceglie la redazione automatica" not in desk  # it picks by itself: nothing to decide one by one
    # What it did not use lapses.
    assert autopilot.lapse(db, tenant, site_id, datetime.now(UTC)) >= 1
    with db.tenant(tenant) as s:
        assert not s.execute(text("SELECT 1 FROM editor.proposals WHERE site_id = :s AND status = 'proposed'"),
                             {"s": site_id}).first()


def test_publishing_goes_by_rest_route_where_wp_json_is_not_routed():
    page404 = httpx.Response(404, text="<h1>Not Found</h1>", headers={"content-type": "text/html"})
    with respx.mock(assert_all_called=False) as router:
        router.route(host="blog.example", path__startswith="/wp-json/").mock(return_value=page404)
        post = router.post(f"{WP}/", params__contains={"rest_route": "/wp/v2/posts"}).mock(
            return_value=httpx.Response(201, json={"id": 7, "link": f"{WP}/articolo"}))
        r = pub.Publisher(http=httpx.Client(), resolver=public_resolver)._wp("POST", WP, "wp/v2/posts", json={"title": "x"})
    assert post.called and r.json()["id"] == 7
    # A REST 404 (a post that is not there) is an answer, not a missing route.
    assert not pub.not_rest(httpx.Response(404, json={"code": "rest_post_invalid_id"}))


def test_a_section_without_its_category_gets_one_on_wordpress():
    """Francesco 2026-11-05: a new site has no categories; the sections are
    decided in Redazia and their categories made on WordPress."""
    p = pub.Publisher(http=httpx.Client(), resolver=public_resolver)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{WP}/wp-json/wp/v2/categories").mock(return_value=httpx.Response(200, json=[]))
        made = router.post(f"{WP}/wp-json/wp/v2/categories").mock(return_value=httpx.Response(201, json={"id": 12}))
        assert p._wordpress_category(WP, ("u", "p"), "Intelligenza artificiale") == 12
    assert json.loads(made.calls[0].request.content) == {"name": "Intelligenza artificiale"}
    with respx.mock(assert_all_called=False) as router:  # already there: found, not made twice
        router.get(f"{WP}/wp-json/wp/v2/categories").mock(return_value=httpx.Response(200, json=[
            {"id": 4, "name": "Economia &amp; finanza"}]))
        made = router.post(f"{WP}/wp-json/wp/v2/categories")
        assert p._wordpress_category(WP, ("u", "p"), "Economia & finanza") == 4 and not made.called
    with respx.mock(assert_all_called=False) as router:  # not allowed: the article goes out without
        router.get(f"{WP}/wp-json/wp/v2/categories").mock(return_value=httpx.Response(200, json=[]))
        router.post(f"{WP}/wp-json/wp/v2/categories").mock(return_value=httpx.Response(
            403, json={"code": "rest_cannot_create"}))
        assert p._wordpress_category(WP, ("u", "p"), "Mondo") is None
