"""The social networks around the topics of the moment: Bluesky's trends
matched by the model, the Shorts of the last 48 hours on YouTube, and what
they weigh in the morning's video ideas."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import respx
from sqlalchemy import select

from editor import models as m, repo, social, trends, videos
from editor.core_client import CoreClient

from test_drafts import chat
from test_publishing import approved_draft

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)
BSKY = {"trends": [
    {"topic": "a1", "displayName": "Lead explosion in Iowa", "description": "People argue about it",
     "postCount": 6505, "startedAt": "2026-11-24T16:04:23Z", "link": "/profile/x/feed/a1", "category": "politics"},
    {"topic": "b2", "displayName": "Mistral Large 5", "description": "The new open model is out",
     "postCount": 900, "startedAt": "2026-11-25T08:00:00Z", "link": "/profile/x/feed/b2"},
]}
TOPICS = [{"topic_id": 7, "label": "Esce Mistral Large 5", "summary": "Il nuovo modello aperto", "hype": 40, "items": 3},
          {"topic_id": 8, "label": "Fibra a Bari", "summary": "", "hype": 30, "items": 9}]


def youtube_answers(router, views=(120000, 3000)):
    router.get(f"{social.YOUTUBE}/search").mock(return_value=httpx.Response(200, json={"items": [
        {"id": {"videoId": f"v{i}"}} for i in range(len(views))]}))
    router.get(f"{social.YOUTUBE}/videos").mock(return_value=httpx.Response(200, json={"items": [
        {"id": f"v{i}", "statistics": {"viewCount": str(n)},
         "snippet": {"title": f"Short {i}", "publishedAt": (NOW - timedelta(hours=10)).isoformat()}}
        for i, n in enumerate(views)]}))


def test_bluesky_trends_are_matched_to_the_topics_by_the_model(db, tenant):
    topics = [dict(t) for t in TOPICS]
    with respx.mock(assert_all_called=False) as router:
        router.get(social.BLUESKY).mock(return_value=httpx.Response(200, json=BSKY))
        llm = router.post("http://core.test/v1/llm/chat").mock(
            return_value=chat(json.dumps({"matches": [{"topic": 1, "trend": 2}, {"topic": 9, "trend": 1}]})))
        with httpx.Client() as client:
            read = social.enrich(db, repo.archive_of(db, tenant), topics, client, CoreClient("http://core.test", "tok"),
                                 now=NOW)
    assert read == {"bluesky": 2, "youtube": 0} and llm.called
    sb = topics[0]["social"]["bluesky"]
    assert sb == [{"label": "Mistral Large 5", "posts": 900, "since": "2026-11-25T08:00:00Z",
                   "url": "https://bsky.app/profile/x/feed/b2"}]
    assert 0.7 < topics[0]["social"]["heat"] < 0.8 and "social" not in topics[1]  # topic 9 does not exist


def test_youtube_shorts_of_the_last_two_days_give_views_per_hour(db, tenant):
    topics = [dict(t) for t in TOPICS]
    with respx.mock(assert_all_called=False) as router:
        youtube_answers(router)
        with httpx.Client() as client:
            read = social.enrich(db, repo.archive_of(db, tenant), topics, client, None, youtube_key="yt-key", now=NOW)
        search = router.calls[0].request.url.params
    assert read["youtube"] == 2 and read["bluesky"] is None  # no model: Bluesky cannot be matched
    assert search["videoDuration"] == "short" and search["order"] == "viewCount" and search["q"] == "Esce Mistral Large 5"
    assert search["publishedAfter"] == "2026-11-23T12:00:00Z"
    yt = topics[0]["social"]["youtube"]
    assert yt["videos"] == 2 and yt["views"] == 123000 and yt["per_hour"] == 12300
    assert yt["top"] == {"title": "Short 0", "views": 120000, "url": "https://www.youtube.com/shorts/v0"}
    assert topics[0]["social"]["heat"] == 1.0


def test_a_network_that_does_not_answer_costs_nothing(db, tenant):
    topics = [dict(t) for t in TOPICS]
    with respx.mock(assert_all_called=False) as router:
        router.get(social.BLUESKY).mock(return_value=httpx.Response(503))
        search = router.get(f"{social.YOUTUBE}/search").mock(return_value=httpx.Response(403, json={"error": "quota"}))
        with httpx.Client() as client:
            read = social.enrich(db, repo.archive_of(db, tenant), topics, client, CoreClient("http://core.test", "tok"),
                                 youtube_key="yt-key", now=NOW)
    assert read == {"bluesky": None, "youtube": 0} and search.call_count == 1  # quota over: no more searches
    assert all("social" not in t for t in topics)


def test_topics_hot_on_social_networks_come_first_in_the_morning(db, tenant):
    archive = repo.archive_of(db, tenant)
    hot = {"bluesky": [{"label": "Mistral Large 5", "posts": 900}], "heat": 0.74}
    with db.tenant(archive) as s:
        for hours, topics in ((10, [{"topic_id": 1, "hype": 50, "items": 20}]),
                              (0, [{"topic_id": 1, "hype": 50, "items": 20, "social": hot},
                                   {"topic_id": 2, "hype": 30, "items": 4}])):
            s.execute(m.trend_snapshots.insert().values(tenant_id=archive, taken_at=NOW - timedelta(hours=hours),
                                                        topics=topics))
        up = trends.rising(s, archive, NOW)
    assert up[1]["social"] == hot and up[1]["gain"] == 0 and up[2]["new"] and "social" not in up[2]


def test_the_idea_knows_what_the_social_networks_say(db, tenant, monkeypatch):
    did, _ = approved_draft(db, tenant)
    with db.tenant(tenant) as s:
        site_id = s.execute(select(m.drafts.c.site_id).where(m.drafts.c.id == did)).scalar()
        topic = s.execute(select(m.topics.c.id).limit(1)).scalar()
    hot = {"bluesky": [{"label": "Mistral Large 5", "posts": 900, "url": "https://bsky.app/x"}],
           "youtube": {"videos": 4, "views": 50000, "per_hour": 2000,
                       "top": {"title": "Mistral in 60s", "views": 40000, "url": "https://www.youtube.com/shorts/a"}},
           "heat": 0.83}
    monkeypatch.setattr(videos, "pick_topics", lambda *a, **k: [
        {"topic_id": topic, "score": 90, "gain": None, "hype": 40.0, "social": hot}])
    idea = {"ideas": [{"title": "Mistral in un minuto", "scene": "Vertical 9:16 shot of a lab", "voiceover": "x",
                       "caption": "y", "hashtags": ["ai"]}]}
    with respx.mock(assert_all_called=False) as router:
        llm = router.post("http://core.test/v1/llm/chat").mock(return_value=chat(json.dumps(idea)))
        ids = videos.daily_round(db, tenant, site_id, CoreClient("http://core.test", "tok"), 1)
    sent = json.loads(llm.calls[0].request.content)["messages"][1]["content"]
    assert 'On Bluesky: "Mistral Large 5" (900 posts)' in sent and "Mistral in 60s" in sent
    with db.tenant(tenant) as s:
        row = s.execute(select(m.videos).where(m.videos.c.id == ids[0])).first()
    assert row.social == hot
