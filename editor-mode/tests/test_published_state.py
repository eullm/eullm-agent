"""Published articles say so everywhere, and two sends to the same site
repository at once do not fail on llms.txt (Francesco 2026-11-11)."""

import httpx
from sqlalchemy import text

from editor import activity

from github_fake import FakeGitHub
from test_publishing import _send, _target, approved_draft
from test_ui import logged_in

class ConflictOnce(FakeGitHub):
    """The first merge finds the base moved under it, as when another
    article went out a moment before."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.refused = False

    def handle(self, request):
        if request.method == "PUT" and request.url.path.endswith("/merge") and not self.refused:
            self.refused = True
            self.calls.append(("PUT", "merge refused"))
            return httpx.Response(405, json={"message": "Pull Request has merge conflicts"})
        return super().handle(request)

def test_a_merge_conflict_is_built_again_on_the_new_site(db, tenant, monkeypatch):
    did, site_id = approved_draft(db, tenant)
    target = _target(db, tenant, site_id, monkeypatch)
    gh = ConflictOnce({"README.md": "sito"})
    gh.checks["*"] = ["success"]
    pid, out = _send(db, tenant, did, target, "publish", gh)
    assert gh.refused and out["status"] == "published", out
    assert "content/blog/bari-in-fibra.md" in gh.files()

def test_drafts_and_activity_say_published(db, tenant, monkeypatch):
    did, site_id = approved_draft(db, tenant)
    target = _target(db, tenant, site_id, monkeypatch)
    gh = FakeGitHub({"README.md": "sito"})
    gh.checks["*"] = ["success"]
    c = logged_in(db, tenant, "owner")
    assert "Approvata" in c.get("/drafts").text
    pid, out = _send(db, tenant, did, target, "publish", gh)
    assert out["status"] == "published"
    page = c.get("/drafts").text
    assert 'data-state="published"' in page
    assert "Pubblicata" in c.get(f"/drafts/{did}").text
    # The sending shows in the box of its own site.
    with db.tenant(tenant) as s:
        s.execute(text("INSERT INTO editor.jobs (tenant_id, kind, subject, args, status, priority) "
                       "VALUES (:t, 'publish', 'publication', :a, 'queued', 10)"),
                  {"t": tenant, "a": '{"publication_id": %d}' % pid})

        from zoneinfo import ZoneInfo
        box = activity.recent(s, ZoneInfo("Europe/Rome"), lambda e: e, site=site_id)
    assert any(x["what"] == "Pubblicazione" and x["title"] == "Bari in fibra" for x in box["items"]), box["items"]
