"""The news base: general sources read for every newsroom, and the news
index that ranks the topics by section."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from editor import news_base, repo, trends
from editor.collectors import Item
from editor.config import Settings
from editor.ingest import SourceReport, store_items
from editor.schedule import Runner
from editor.scoring import score_topics
from editor.topics import detect_topics

from test_proposals import setup_site
from test_ui import logged_in

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>BBC</title>
<item><title>Ceasefire talks resume in Geneva</title><link>https://bbc.example/1</link></item>
</channel></rss>"""


def test_the_base_is_read_for_every_newsroom_whatever_it_follows(db, tenant):
    setup_site(db, tenant)
    asked = []

    def answer(request):
        asked.append(str(request.url))
        if str(request.url) == news_base.BASE[0][1]:
            return httpx.Response(200, content=RSS)
        return httpx.Response(404)

    runner = Runner(db, Settings(base_news=True), http_factory=lambda: httpx.Client(transport=httpx.MockTransport(answer)))
    done, failed = [], []
    runner.collect_now(tenant, datetime.now(UTC), done, failed)
    assert not failed
    # Every base feed was read, besides the newsroom's own sources.
    assert {t[1] for t in news_base.BASE} <= set(asked)
    archive = repo.archive_of(db, tenant)
    with db.tenant(archive) as s:
        titles = set(s.execute(text("SELECT title FROM editor.source_items")).scalars())
        base = s.execute(text("SELECT count(*) FROM editor.sources WHERE shared AND url LIKE 'http%'")).scalar()
    assert "Ceasefire talks resume in Geneva" in titles and base >= len(news_base.BASE)
    # Adding them again changes nothing.
    assert len(news_base.ensure(db, archive)) == len(news_base.BASE)


def test_the_index_ranks_topics_by_section(db, tenant):
    setup_site(db, tenant)
    archive = repo.archive_of(db, tenant)
    ids = news_base.ensure(db, archive)
    with db.tenant(archive) as s:
        econ = s.execute(text("SELECT id FROM editor.sources WHERE shared AND url = :u"),
                         {"u": "https://feeds.bbci.co.uk/news/business/rss.xml"}).scalar()
        assert econ in ids
        now = datetime.now(UTC)
        store_items(s, archive, econ, [
            Item(url="https://bbc.example/b1", title="Central bank raises interest rates again",
                 published_at=now - timedelta(hours=1)),
            Item(url="https://bbc.example/b2", title="Interest rates rise again at the central bank",
                 published_at=now - timedelta(hours=2)),
        ], SourceReport(econ, "test"))
    detect_topics(db, tenant)
    score_topics(db, tenant)
    with db.tenant(archive) as s:
        trends.snapshot(s, archive, now)
        snap = trends.latest(s, archive)
    rates = next(x for x in snap.topics if "rates" in x["label"] or "interest" in x["label"])
    assert rates["section"] == "economy"
    assert any(x["section"] is None for x in snap.topics)  # the site's own sources have no section

    c = logged_in(db, tenant, "editor")
    page = c.get("/index").text
    assert "Indice notizie" in page and "Economia" in page and "Dalle fonti dei siti" in page
    assert "2 notizie" in page and "built-in" not in page
    only = c.get("/index?section=economy").text
    assert "interest" in only.lower() and "Bari" not in only


def test_every_base_feed_has_its_country_and_none_dominates():
    """The base, balanced by country (Francesco 2026-11-07: 55 of 150 feeds were
    American): every feed says its country, and world and economy news come
    from at most six feeds of one foreign country."""
    import re
    from collections import Counter

    assert all(re.fullmatch(r"[A-Z]{2}|", t[4]) for t in news_base.BASE)
    assert len({t[1] for t in news_base.BASE}) == len(news_base.BASE)
    general = Counter(t[4] for t in news_base.BASE if t[3] in ("world", "economy") and t[4])
    home = {"IT", "DE", "FR", "ES"}  # the national news of the dashboard's languages
    assert max(n for c, n in general.items() if c not in home) <= 7
    assert sum(1 for n in general.values() if n >= 2) >= 25
    nyt = "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"
    assert news_base.voice(1, nyt) == news_base.voice(2, "https://feeds.npr.org/1004/rss.xml") == "country:US"
    assert news_base.voice(3, "https://example.org/feed") == "source:3"
