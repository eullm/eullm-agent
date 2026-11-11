"""Advertisements stay out of the plan, and the plan says when a draft went out (2026-11-11)."""
from editor import autopilot
from editor.text import sponsored


def test_sponsored_labels():
    assert sponsored("Contenuto sponsorizzato: le offerte luce e gas")
    assert sponsored("", "Articolo in collaborazione con Octopus Energy")
    assert sponsored("[AD] New phone")
    assert not sponsored("Lo sponsor della Serie A cambia", "La lega annuncia il nuovo partner")
    assert not sponsored("Arera aggiorna le tariffe di tutela")


def test_rules_reject_ads_and_expired_deadlines():
    rules = " ".join(autopilot.RULES)
    assert "advertisement" in rules
    assert "already ended by the date of publication" in rules


def test_the_plan_says_when_a_draft_went_out(db, tenant):
    from datetime import UTC, datetime

    from sqlalchemy import text

    from test_publishing import approved_draft
    from test_ui import logged_in
    from test_weekly_search import _published

    did, site_id = approved_draft(db, tenant)
    with db.tenant(tenant) as s:
        s.execute(text("UPDATE editor.proposals SET status = 'drafted' WHERE id = "
                       "(SELECT proposal_id FROM editor.drafts WHERE id = :d)"), {"d": did})
    c = logged_in(db, tenant, "owner")
    page = c.get("/trends").text
    assert "Bozza pronta" in page and "Pubblicata" not in page
    _published(db, tenant, did, site_id, datetime.now(UTC))
    page = c.get("/trends").text
    assert "Pubblicata" in page and "https://blog.example/bari-in-fibra" in page
