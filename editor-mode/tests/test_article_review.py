import httpx
from sqlalchemy import select

from editor import autopilot, models as m, publishing
from test_seo import seo_draft


def terms(title):
    return autopilot._angle_terms(title, None)


def test_the_same_story_retold_waits_but_a_new_development_goes_on():
    told = [(terms("Elezioni di metà mandato USA: i democratici vincono il Senato, la Camera è contesa"), {1, 2})]
    # nearly the same words: the same story, even with a news item nobody used
    assert autopilot.overlaps(terms("Elezioni di metà mandato USA: i democratici vincono il Senato, Camera contesa"), {3}, told)
    # same subject, nothing new (same items, half of the words): told again
    assert autopilot.overlaps(terms("Elezioni di metà mandato: i democratici vincono il Senato"), {1}, told)
    # same subject, a news item no article used and another angle: a development
    assert not autopilot.overlaps(terms("Elezioni di metà mandato: Hakeem Jeffries speaker, i primi debutti alla Camera"), {9}, told)
    assert not autopilot.overlaps(terms("Offerte Black Friday 2026 in anticipo"), {1}, told)


def test_the_editor_in_chief_fixes_a_typo_and_never_rewrites(db, tenant):
    did, _ = seo_draft(db, tenant)
    with db.tenant(tenant) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).first()
    typo = d.title.replace("la rete", "i rete")
    with db.tenant(tenant) as s:
        s.execute(m.drafts.update().where(m.drafts.c.id == did).values(
            title=typo, body_md=d.body_md.replace(d.title, typo, 1)))
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).first()
    assert autopilot.proofread(db, tenant, d, {"title": d.title.replace("i rete", "la rete"),
                                               "subtitle": "Tutta un'altra frase, riscritta del tutto"})
    with db.tenant(tenant) as s:
        after = s.execute(select(m.drafts).where(m.drafts.c.id == did)).first()
    assert after.title == "Fibra a Bari: la rete è completa" and "# Fibra a Bari: la rete è completa" in after.body_md
    assert after.subtitle == d.subtitle  # a rewrite is not a proofread


def test_only_github_failing_on_its_side_is_tried_again():
    hiccup = publishing._github_hiccup
    assert hiccup(publishing.PublishError("GitHub, merge: 502 Server Error"))
    assert hiccup(httpx.ConnectTimeout("slow"))
    assert not hiccup(publishing.PublishError("GitHub, merge: 422 not mergeable"))
    assert not hiccup(publishing.PublishError("the site's checks failed (x): the site did not change"))


def test_check_now_reads_github_again(monkeypatch):
    import time

    from editor import installs

    calls = []

    class Client:
        def get(self, url, headers=None):
            calls.append(url)
            return httpx.Response(200, json={"tag_name": f"v0.0.{len(calls)}", "html_url": ""},
                                  request=httpx.Request("GET", url))

    monkeypatch.setattr(installs, "_release", {"at": 0.0, "release": None})
    assert installs.latest(Client())["version"] == "0.0.1"
    assert installs.latest(Client())["version"] == "0.0.1"  # kept
    installs._release["at"] = time.monotonic() - 20
    assert installs.latest(Client(), fresh=True)["version"] == "0.0.2"  # "Controlla adesso"


def test_the_same_story_in_other_words_goes_out_once(db, tenant):
    """aitoday.it, 2026-11-06: the government challenging the Veneto law on
    end of life went out twice, two minutes apart, with two titles."""
    from types import SimpleNamespace

    from editor.text import tokens
    from test_publishing import approved_draft

    first = set(tokens("Fine vita, il governo impugna la legge del Veneto davanti alla Consulta"))
    second = set(tokens("Il governo impugna la legge del Veneto sul fine vita e la manda alla Consulta"))
    assert autopilot.same_title(first, second)
    assert not autopilot.same_title(set(tokens("Elezioni di metà mandato USA: i democratici vincono il Senato")),
                                    set(tokens("Elezioni di metà mandato: Hakeem Jeffries speaker, i primi debutti")))
    # Two proposals of the same round: the second waits even with a longer, different angle.
    told = [(terms("x"), {1}, first)]
    assert autopilot.overlaps(autopilot._angle_terms("Il governo impugna la legge del Veneto sul fine vita",
                                                     "la Consulta, i precedenti di Toscana e Sardegna, le reazioni"),
                              {7}, told, second)

    # Written at the same time, the second to finish meets the first, already approved.
    did, site_id = approved_draft(db, tenant)
    with db.tenant(tenant) as s:
        s.execute(m.drafts.update().where(m.drafts.c.id == did).values(
            title="Fine vita, il governo impugna la legge del Veneto davanti alla Consulta"))
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).one()
        twin = SimpleNamespace(id=-1, site_id=site_id, proposal_id=-1,
                               title="Il governo impugna la legge del Veneto sul fine vita e la manda alla Consulta")
        assert autopilot.told_already(s, twin) == d.title
        other = SimpleNamespace(id=-1, site_id=site_id, proposal_id=-1, title="Un altro fatto del tutto diverso")
        assert autopilot.told_already(s, other) is None
        assert autopilot.told_already(s, SimpleNamespace(id=-1, site_id=site_id, proposal_id=d.proposal_id,
                                                         title=d.title)) is None  # its own version
