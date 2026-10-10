import hashlib
import hmac
import json
import socket

import httpx
import pytest
import respx
from sqlalchemy import text

from editor import drafts, publishing as pub
from editor.markdown import to_html
from test_drafts import accepted_proposal, chat
from editor.core_client import CoreClient


def public_resolver(host, port, proto=None):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def private_resolver(host, port, proto=None):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]


def approved_draft(db, tenant):
    pid, cited = accepted_proposal(db, tenant)
    answer = json.dumps({"title": "Bari in fibra", "subtitle": "La rete è completa", "sections": [{"heading": "Fatti", "claims": [
        {"text": "Open Fiber ha lavorato a Bari.", "sources": [cited[0]]},
        {"text": "La rete in fibra è completa.", "sources": [cited[0]]},
        {"text": "I lavori sono finiti.", "sources": [cited[0]]}]}]})
    with respx.mock() as router:
        router.post("http://core.test/v1/llm/chat").mock(return_value=chat(answer))
        did = drafts.write_draft(db, tenant, pid, CoreClient("http://core.test", "tok"), None)
    drafts.decide(db, tenant, did, True, "francesco")
    with db.tenant(tenant) as s:
        site_id = s.execute(text("SELECT site_id FROM editor.drafts WHERE id = :d"), {"d": did}).scalar()
    return did, site_id


def test_markdown_rendering_escapes():
    page = to_html("# T <b>\n\n_sub_\n\n## H\n\nTesto[^1] <script>x</script>\n\n[^1]: [Fonte](https://a.example/x), Nome")
    assert "<h1>T &lt;b&gt;</h1>" in page and "<em>sub</em>" in page and "<script>" not in page
    assert '<sup><a href="#fn1">1</a></sup>' in page and '<li id="fn1"><a href="https://a.example/x"' in page


def test_targets_must_be_public_and_secret_free(db, tenant, monkeypatch):
    did, site_id = approved_draft(db, tenant)
    monkeypatch.setattr(socket, "getaddrinfo", private_resolver)
    with pytest.raises(pub.PublishError, match="non-public"):
        pub.add_target(db, tenant, site_id, "wordpress", "wp", {"url": "https://intranet.example", "user": "a"}, "WP_PASS")
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    with pytest.raises(pub.PublishError, match="https"):
        pub.add_target(db, tenant, site_id, "webhook", "hook", {"url": "http://hooks.example/x"}, "HOOK")
    with pytest.raises(pub.PublishError, match="environment variable"):
        pub.add_target(db, tenant, site_id, "wordpress", "wp", {"url": "https://blog.example", "user": "a", "password": "x"}, None)


def test_nothing_leaves_without_approval(db, tenant, monkeypatch):
    did, site_id = approved_draft(db, tenant)
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    monkeypatch.setenv("WP_PASS", "app-pass-123")
    target = pub.add_target(db, tenant, site_id, "wordpress", "Blog", {"url": "https://blog.example", "user": "editor"}, "WP_PASS")
    pid = pub.request(db, tenant, did, target, "draft", "redazione")
    publisher = pub.Publisher(resolver=public_resolver)
    with respx.mock() as router:
        wp = router.post("https://blog.example/wp-json/wp/v2/posts").mock(
            return_value=httpx.Response(201, json={"id": 77, "link": "https://blog.example/?p=77"}))
        with pytest.raises(pub.PublishError, match="approval"):
            publisher.run(db, tenant, pid)
        assert not wp.called
        assert pub.decide(db, tenant, pid, True, "francesco")
        assert not pub.decide(db, tenant, pid, False, "francesco")  # decided once
        assert publisher.run(db, tenant, pid) == {"status": "published", "url": "https://blog.example/?p=77"}
        assert publisher.run(db, tenant, pid)["status"] == "published" and wp.call_count == 1  # idempotent
        sent = json.loads(wp.calls[0].request.content)
        assert sent["status"] == "draft" and sent["title"] == "Bari in fibra" and "<h2>Fatti</h2>" in sent["content"]
        assert wp.calls[0].request.headers["authorization"].startswith("Basic ")
    with pytest.raises(Exception):  # the same publication cannot be requested twice
        pub.request(db, tenant, did, target, "draft", "redazione")


def test_webhook_is_signed_and_telegram_token_is_not_logged(db, tenant, monkeypatch):
    did, site_id = approved_draft(db, tenant)
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    monkeypatch.setenv("HOOK_SECRET", "s3cret")
    monkeypatch.setenv("TG_TOKEN", "123:ABC")
    hook = pub.add_target(db, tenant, site_id, "webhook", "social", {"url": "https://hooks.example/in"}, "HOOK_SECRET")
    tg = pub.add_target(db, tenant, site_id, "telegram_channel", "canale", {"chat_id": "@canale"}, "TG_TOKEN")
    publisher = pub.Publisher(resolver=public_resolver)
    with respx.mock() as router:
        route = router.post("https://hooks.example/in").mock(return_value=httpx.Response(200))
        router.post("https://api.telegram.org/bot123:ABC/sendMessage").mock(return_value=httpx.Response(500))
        p1 = pub.request(db, tenant, did, hook, "publish", "redazione")
        p2 = pub.request(db, tenant, did, tg, "publish", "redazione")
        for p in (p1, p2):
            pub.decide(db, tenant, p, True, "francesco")
        assert publisher.run(db, tenant, p1)["status"] == "published"
        failed = publisher.run(db, tenant, p2)
    req = route.calls[0].request
    expected = hmac.new(b"s3cret", req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-editor-signature"] == f"sha256={expected}"
    assert failed["status"] == "failed" and "123:ABC" not in failed["error"]
    with db.tenant(tenant) as s:
        err = s.execute(text("SELECT error FROM editor.publications WHERE id = :p"), {"p": p2}).scalar()
    assert "123:ABC" not in err and "***" in err


def test_unapproved_drafts_cannot_be_queued(db, tenant, monkeypatch):
    did, site_id = approved_draft(db, tenant)  # status 'draft', not approved yet
    with db.tenant(tenant) as s:
        s.execute(text("UPDATE editor.drafts SET status = 'draft' WHERE id = :d"), {"d": did})
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    target = pub.add_target(db, tenant, site_id, "webhook", "h", {"url": "https://hooks.example/in"}, "X")
    with pytest.raises(pub.PublishError, match="approved drafts"):
        pub.request(db, tenant, did, target, "draft", "redazione")
