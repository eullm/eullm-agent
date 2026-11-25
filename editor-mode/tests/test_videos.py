"""Videos for the social networks: ideas from an article or a trend, then,
when a person asks, a vertical clip with audio from OpenRouter's video API,
paid in credits only when it is made, downloaded from the dashboard."""

import json

import httpx
import pytest
import respx
from sqlalchemy import select, text

from editor import credits, models as m, videos
from editor.config import Settings
from editor.core_client import CoreClient

from conftest import set_plan
from test_drafts import chat
from test_publishing import approved_draft
from test_ui import logged_in

OR = "https://openrouter.ai/api/v1"
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64

IDEAS = {"ideas": [
    {"title": "La fibra arriva a Bari", "hook": "Un tombino si apre e ne esce la luce",
     "scene": "Vertical 9:16 shot, a street in Bari at dawn, a technician seen from behind opens a manhole",
     "voiceover": "A Bari la fibra arriva in ogni quartiere. Leggi tutto sul sito.",
     "caption": "La fibra ottica a Bari: cosa cambia.", "hashtags": ["#Bari", "fibra ottica", "internet"]},
    {"title": "Cantieri di notte", "hook": "Luci arancioni nel buio", "scene": "Night works, vertical framing",
     "voiceover": "Di notte i cantieri posano i cavi.", "caption": "Di notte si posa la fibra.",
     "hashtags": ["fibra"]},
]}


def maker(**kw):
    return videos.OpenRouterVideo("sk-or-test", http=httpx.Client(), poll=0, **kw)


def catalog(router, model=videos.DEFAULT_MODEL, durations=(1, 5, 10, 15), sizes=("480p", "720p"),
            ratios=("16:9", "9:16")):
    return router.get(f"{OR}/videos/models").mock(return_value=httpx.Response(200, json={"data": [{
        "id": model, "supported_durations": list(durations), "supported_resolutions": list(sizes),
        "supported_aspect_ratios": list(ratios), "generate_audio": True}]}))


def service(router, final="completed", cost=0.3):
    submit = router.post(f"{OR}/videos").mock(return_value=httpx.Response(202, json={
        "id": "job1", "polling_url": f"{OR}/videos/job1", "status": "pending"}))
    done = {"id": "job1", "status": final, "unsigned_urls": [f"{OR}/videos/job1/content?index=0"],
            "usage": {"cost": cost}}
    if final != "completed":
        done = {"id": "job1", "status": final, "error": "Generated video rejected by content moderation."}
    poll = router.get(f"{OR}/videos/job1").mock(side_effect=[
        httpx.Response(200, json={"id": "job1", "status": "in_progress"}), httpx.Response(200, json=done)])
    content = router.get(f"{OR}/videos/job1/content").mock(return_value=httpx.Response(200, content=MP4))
    return submit, poll, content


def with_ideas(db, tenant):
    did, _ = approved_draft(db, tenant)
    with respx.mock(assert_all_called=False) as router:
        router.post("http://core.test/v1/llm/chat").mock(return_value=chat(json.dumps(IDEAS)))
        ids = videos.ideas(db, tenant, CoreClient("http://core.test", "tok"), draft_id=did)
    return did, ids


def test_ideas_are_written_from_the_article_and_replace_the_unused_ones(db, tenant):
    did, ids = with_ideas(db, tenant)
    assert len(ids) == 2
    with db.tenant(tenant) as s:
        rows = videos.of_draft(s, did)
        used = s.execute(text("SELECT purpose FROM editor.llm_usage ORDER BY id DESC LIMIT 1")).scalar()
    assert [r.status for r in rows] == ["idea", "idea"] and used == "video_ideas"
    assert rows[0].hashtags == ["Bari", "fibraottica", "internet"]  # no #, no spaces
    assert videos.post_text(rows[0]).endswith("#Bari #fibraottica #internet")
    with respx.mock(assert_all_called=False) as router:  # asked again: new ideas instead of the old ones
        router.post("http://core.test/v1/llm/chat").mock(return_value=chat(json.dumps({"ideas": IDEAS["ideas"][:1]})))
        again = videos.ideas(db, tenant, CoreClient("http://core.test", "tok"), draft_id=did)
    with db.tenant(tenant) as s:
        assert [r.id for r in videos.of_draft(s, did)] == again


def test_the_video_is_vertical_with_audio_and_fits_the_model(db, tenant):
    did, ids = with_ideas(db, tenant)
    with respx.mock(assert_all_called=False) as router:
        catalog(router, durations=(6, 8))  # 10 seconds asked: the nearest it makes
        submit, _, _ = service(router)
        out = videos.make(db, tenant, ids[0], maker(resolution="1080p", seconds=10))
    body = json.loads(submit.calls[0].request.content)
    assert out["ready"] and body["aspect_ratio"] == "9:16" and body["generate_audio"] is True
    assert body["duration"] == 8 and body["resolution"] == "720p"  # 1080p not offered: the best below
    assert body["model"] == videos.DEFAULT_MODEL and "narrator" in body["prompt"] and "No on-screen text" in body["prompt"]
    assert submit.calls[0].request.headers["authorization"] == "Bearer sk-or-test"
    with db.tenant(tenant) as s:
        row = s.execute(select(m.videos).where(m.videos.c.id == ids[0])).first()
        cost = s.execute(text("SELECT model, cost FROM editor.llm_usage WHERE purpose = 'video'")).first()
    assert row.status == "ready" and bytes(row.content) == MP4 and row.job_ref == "job1" and row.seconds == 8
    assert cost.model == videos.DEFAULT_MODEL and abs(cost.cost - 0.3) < 1e-9


def test_a_model_without_vertical_videos_is_refused_before_paying(db, tenant):
    _, ids = with_ideas(db, tenant)
    with respx.mock(assert_all_called=False) as router:
        catalog(router, ratios=("16:9",))
        submit = router.post(f"{OR}/videos")
        out = videos.make(db, tenant, ids[0], maker())
    assert "failed" in out and not submit.called


def test_a_refused_video_costs_nothing_and_can_be_tried_again(db, tenant):
    _, ids = with_ideas(db, tenant)
    set_plan(db, tenant, credits_month=500, max_videos_month=0)
    with respx.mock(assert_all_called=False) as router:
        catalog(router)
        service(router, final="failed")
        out = videos.make(db, tenant, ids[0], maker())
    with db.tenant(tenant) as s:
        row = s.execute(select(m.videos.c.status, m.videos.c.job_ref, m.videos.c.error)
                        .where(m.videos.c.id == ids[0])).first()
        assert credits.state(s)["month_used"] == 0
    assert out["failed"] and row.status == "failed" and row.job_ref is None and "moderation" in row.error


def test_a_video_is_paid_in_credits_only_when_made(db, tenant):
    _, ids = with_ideas(db, tenant)
    set_plan(db, tenant, credits_month=150, max_videos_month=0)  # a plan of articles: videos in credits
    with db.tenant(tenant) as s:
        assert videos.price(s) == 100
    videos.ask(db, tenant, ids[0], "redattore")
    with respx.mock(assert_all_called=False) as router:
        catalog(router)
        service(router)
        videos.make(db, tenant, ids[0], maker())
    with db.tenant(tenant) as s:
        assert credits.state(s)["month_used"] == 100
        with pytest.raises(credits.NoCredits):  # 50 left
            credits.need(s, videos.price(s))
    with pytest.raises(credits.NoCredits):
        videos.ask(db, tenant, ids[1], "redattore")


def test_a_restarted_worker_waits_for_the_same_job(db, tenant):
    _, ids = with_ideas(db, tenant)
    with respx.mock(assert_all_called=False) as router:
        catalog(router)
        submit, poll, _ = service(router)
        poll.side_effect = httpx.ConnectError("worker lost the network")
        with pytest.raises(httpx.ConnectError):
            videos.make(db, tenant, ids[0], maker())
    with db.tenant(tenant) as s:
        assert s.execute(select(m.videos.c.job_ref).where(m.videos.c.id == ids[0])).scalar() == "job1"
    with respx.mock(assert_all_called=False) as router:
        catalog(router)
        submit, _, _ = service(router)
        assert videos.make(db, tenant, ids[0], maker())["ready"]
    assert not submit.called  # the same job, not a second one paid


def test_old_files_are_forgotten_the_texts_stay(db, tenant):
    from datetime import UTC, datetime, timedelta

    _, ids = with_ideas(db, tenant)
    with respx.mock(assert_all_called=False) as router:
        catalog(router)
        service(router)
        videos.make(db, tenant, ids[0], maker())
    assert videos.forget_old(db, tenant, datetime.now(UTC) + timedelta(days=videos.KEEP_DAYS + 1)) == 1
    with db.tenant(tenant) as s:
        row = s.execute(select(m.videos.c.content, m.videos.c.caption).where(m.videos.c.id == ids[0])).first()
    assert row.content is None and row.caption


def test_the_key_comes_from_the_settings_or_the_covers():
    assert videos.from_settings(Settings()) is None
    assert videos.from_settings(Settings(images_api_key="bfl-key")) is None
    v = videos.from_settings(Settings(images_api_key="sk-or-covers", video_seconds=12))
    assert v.key == "sk-or-covers" and v.seconds == 12 and v.model == videos.DEFAULT_MODEL
    v = videos.from_settings(Settings(images_api_key="sk-or-covers", video_api_key="sk-or-video",
                                      video_model="google/veo-3.1-lite"))
    assert v.key == "sk-or-video" and v.model == "google/veo-3.1-lite"


def test_the_pages_offer_ideas_make_and_download(db, tenant):
    did, ids = with_ideas(db, tenant)
    c = logged_in(db, tenant, "editor")
    assert "Video per i social" in c.get(f"/drafts/{did}").text
    page = c.get("/videos").text
    assert "La fibra arriva a Bari" in page and "#Bari #fibraottica" in page
    r = c.post(f"/ui/drafts/{did}/video-ideas", follow_redirects=False)  # no model in this app
    assert r.status_code == 303
    r = c.post(f"/ui/videos/{ids[0]}/make", data={"back": "/videos"}, follow_redirects=False)
    assert r.status_code == 303  # no video service: a message, nothing queued
    with db.tenant(tenant) as s:
        assert not s.execute(select(m.jobs.c.id).where(m.jobs.c.kind == "video")).first()
        s.execute(m.videos.update().where(m.videos.c.id == ids[0]).values(status="ready", content=MP4))
    r = c.get(f"/videos/{ids[0]}/file?download=1")
    assert r.status_code == 200 and r.content == MP4 and r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"].startswith('attachment; filename="la-fibra-arriva-a-bari')
    assert logged_in(db, tenant, "viewer").post(f"/ui/videos/{ids[1]}/delete").status_code == 403


def test_make_queues_the_job_with_a_video_service(db, tenant):
    from fastapi.testclient import TestClient

    from editor import auth
    from editor.api import create_app

    _, ids = with_ideas(db, tenant)
    token = auth.create_token(db, tenant, "editor-user", "editor")
    c = TestClient(create_app(db, videomaker=maker()))
    c.post("/login", data={"token": token})
    assert c.post(f"/ui/videos/{ids[0]}/make", data={"back": "https://evil.example"},
                  follow_redirects=False).headers["location"] == "/videos"
    with db.tenant(tenant) as s:
        job = s.execute(select(m.jobs.c.kind, m.jobs.c.args).where(m.jobs.c.kind == "video")).first()
        status = s.execute(select(m.videos.c.status).where(m.videos.c.id == ids[0])).scalar()
    assert job.args["video_id"] == ids[0] and status == "queued"
    assert "Genera il video" not in c.get("/videos").text.split(f'id="video-{ids[0]}"')[1].split('class="video-idea"')[0]


def test_a_social_plan_includes_videos_then_credits(db, tenant):
    _, ids = with_ideas(db, tenant)
    set_plan(db, tenant, credits_month=0, max_videos_month=1)
    with db.tenant(tenant) as s:
        assert videos.price(s) == 0 and videos.allowance(s)["left"] == 1
    with respx.mock(assert_all_called=False) as router:
        catalog(router)
        service(router)
        videos.make(db, tenant, ids[0], maker())
    with db.tenant(tenant) as s:
        row = s.execute(select(m.videos.c.included, m.videos.c.credits).where(m.videos.c.id == ids[0])).first()
        assert row.included and row.credits == 0
        assert videos.allowance(s) == {"included": 1, "used": 1, "left": 0}
        assert videos.price_or_none(s) == 100  # credits on, none left
    with pytest.raises(credits.NoCredits):
        videos.ask(db, tenant, ids[1], "redattore")


def test_rising_topics_come_first_in_the_morning(db, tenant):
    from datetime import UTC, datetime, timedelta

    from editor import repo, trends

    archive = repo.archive_of(db, tenant)
    now = datetime.now(UTC)
    with db.tenant(archive) as s:
        for hours, topics in ((10, [{"topic_id": 1, "hype": 50, "items": 20}, {"topic_id": 2, "hype": 10, "items": 2}]),
                              (0, [{"topic_id": 1, "hype": 52, "items": 22}, {"topic_id": 2, "hype": 30, "items": 4},
                                   {"topic_id": 3, "hype": 12, "items": 1}])):
            s.execute(m.trend_snapshots.insert().values(tenant_id=archive, taken_at=now - timedelta(hours=hours),
                                                        topics=topics))
        up = trends.rising(s, archive, now)
    assert up[2]["gain"] == 20 and up[2]["rising"] == 1 and up[2]["early"]
    assert up[3]["new"] and up[1]["rising"] < up[2]["rising"]


def test_a_social_channel_has_no_site_and_gets_its_profile_from_words(db, tenant):
    c = logged_in(db, tenant, "owner")
    r = c.post("/ui/channels", data={"name": "AI Today Shorts", "handle": "@aitoday.shorts",
                                     "brief": "Novità di AI spiegate semplici", "language": "it"},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/site"
    with db.tenant(tenant) as s:
        site = s.execute(select(m.sites).where(m.sites.c.kind == "social")).first()
        job = s.execute(select(m.jobs.c.kind, m.jobs.c.args).where(m.jobs.c.kind == "brief")).first()
    assert site.domain == "@aitoday-shorts" and site.handle == "@aitoday.shorts" and site.name == "AI Today Shorts"
    assert job.args["site_id"] == site.id and "no website" in job.args["auto"]["brief"]
    assert videos.per_day(None, "social") == 3 and videos.per_day(None, "web") == 0
    assert videos.per_day({"settings": {"videos_per_day": 5}}, "web") == 5


def test_the_morning_round_writes_one_idea_per_topic(db, tenant, monkeypatch):

    did, _ = approved_draft(db, tenant)
    with db.tenant(tenant) as s:
        site_id = s.execute(select(m.drafts.c.site_id).where(m.drafts.c.id == did)).scalar()
        topic = s.execute(select(m.topics.c.id).limit(1)).scalar()
    assert topic is not None
    monkeypatch.setattr(videos, "pick_topics", lambda *a, **k: [{"topic_id": topic, "score": 90, "gain": 12.0,
                                                                 "hype": 40.0}])
    with respx.mock(assert_all_called=False) as router:
        router.post("http://core.test/v1/llm/chat").mock(return_value=chat(json.dumps(IDEAS)))
        ids = videos.daily_round(db, tenant, site_id, CoreClient("http://core.test", "tok"), 1)
    with db.tenant(tenant) as s:
        row = s.execute(select(m.videos).where(m.videos.c.id == ids[0])).first()
    assert len(ids) == 1 and row.auto and row.gain == 12.0 and row.topic_id == topic and row.draft_id is None
    from datetime import datetime
    body = {"settings": {"videos_per_day": 2}}
    assert videos.round_due(body, "web", datetime(2026, 11, 25, 9), 8)
    assert not videos.round_due(body | {"settings": {"videos_per_day": 2, "videos_last": "2026-11-25"}}, "web",
                                datetime(2026, 11, 25, 9), 8)
    assert not videos.round_due(body, "web", datetime(2026, 11, 25, 7), 8)
