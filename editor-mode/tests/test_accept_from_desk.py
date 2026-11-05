"""Accepting proposals from the Desk and from Trend e piano: every page
still opens while the drafts are being written."""

import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from editor import auth, picks
from editor.api import create_app
from editor.core_client import CoreClient
from test_proposals import setup_site, NOW, PROFILE


def test_pages_open_while_accepted_proposals_are_written(db, tenant):
    site_id = setup_site(db, tenant)
    with db.tenant(tenant) as s:
        s.execute(text("UPDATE editor.editorial_profiles SET body = :b"),
                  {"b": json.dumps({**PROFILE, "settings": {**PROFILE["settings"], "proposals_per_day": 3}})})
    ids = picks.fill(db, tenant, site_id, 3, None, now=NOW)
    token = auth.create_token(db, tenant, "owner-user", "owner")
    c = TestClient(create_app(db, core=CoreClient("http://core.test", "tok")))
    c.post("/login", data={"token": token}, follow_redirects=False)
    for back in ("/", "/trends"):
        assert c.get(back).status_code == 200
    for i, back in zip(ids, ("/", "/trends", "/")):
        r = c.post(f"/ui/proposals/{i}", data={"accept": "1", "back": back})
        assert r.status_code == 200, r.text[:300]
    for p in ("/", "/trends", "/drafts", "/activity", "/index", "/sources", "/site"):
        assert c.get(p).status_code == 200, p


def test_the_automatic_newsroom_plans_first_the_news_more_outlets_tell(db, tenant):
    """Francesco 2026-11-05: 167 proposals from single news, none told by
    two outlets, and nothing written from 13:00 on; then "se non c'è la
    seconda testata va fatto comunque". The news the outlets the site asks
    for tell come first, the others follow."""
    from editor import autopilot as ap, proposals

    site_id = setup_site(db, tenant)
    ids = picks.fill(db, tenant, site_id, 3, None, now=NOW, outlets=2)
    with db.tenant(tenant) as s:
        counts = [ap.source_count(s, i) for i in ids]
        assert ids and counts == sorted(counts, key=lambda n: n < 2) and counts[0] >= 2
        # These count toward the day's planning, the proposals of the Desk do not.
        assert proposals.made_today(s, site_id, NOW - timedelta(days=3), outlets=True) == len(ids)


def test_news_fewer_outlets_tell_are_still_proposed(db, tenant):
    site_id = setup_site(db, tenant)
    assert picks.fill(db, tenant, site_id, 3, None, now=NOW, outlets=3)  # none told by three: proposed anyway
