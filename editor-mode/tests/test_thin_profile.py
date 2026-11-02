from sqlalchemy import select

from editor import models as m, profile as prof, repo
from test_ui import logged_in


def thin_site(db, tenant, read=0):
    with db.tenant(tenant) as s:
        sid = repo.ensure_site(s, tenant, "nuovo.example", "Nuovo", "it")
        body = {"domain": "nuovo.example", "name": "Nuovo", "language": "it",
                "analysis": {"status": "ok", "posts_read": read},
                "sector": {"value": "Notizie", "evidence": [], "by": "model"},
                "audience": {"value": None, "evidence": [], "by": None},
                "style": {"tone": None, "formats": [], "evidence": []},
                "subtopics": [{"name": "Mondo", "share": 0.5}, {"name": "Clima", "share": 0.5}],
                "gaps": ["no article read"], "settings": dict(prof.DEFAULT_SETTINGS)}
        prof.save_draft(s, tenant, sid, body, "analysis")
    return sid


def test_a_site_with_nothing_to_read_asks_for_its_context_before_approval(db, tenant):
    sid = thin_site(db, tenant)
    owner = logged_in(db, tenant, "owner")
    start = owner.get("/start").text
    assert "Profilo da completare" in start and f"/ui/sites/{sid}/brief" in start
    assert f"/ui/sites/{sid}/approve/" not in start  # no approval of a guess
    site = owner.get("/site").text
    assert "Profilo da completare" in site and f"/ui/sites/{sid}/approve/" not in site
    r = owner.post(f"/ui/sites/{sid}/brief", data={"brief": "Notizie dal mondo per lettori curiosi",
                                                    "sections": "Mondo\nClima", "language": "it", "back": "/start"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/start"
    with db.tenant(tenant) as s:
        job = s.execute(select(m.jobs.c.kind, m.jobs.c.args).where(m.jobs.c.kind == "brief")).first()
        body = s.execute(select(m.editorial_profiles.c.body).where(m.editorial_profiles.c.site_id == sid)).scalar()
    assert job.args["auto"]["sections"] == ["Mondo", "Clima"] and job.args["site_id"] == sid
    assert body["settings"]["auto"]["brief"] == "Notizie dal mondo per lettori curiosi"


def test_a_site_with_articles_to_read_is_approved_as_before(db, tenant):
    sid = thin_site(db, tenant, read=20)
    start = logged_in(db, tenant, "owner").get("/start").text
    assert "Profilo da completare" not in start and f"/ui/sites/{sid}/approve/" in start
