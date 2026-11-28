"""Videos for the social networks: ideas from an article or a trend, then,
when a person asks, a vertical clip with audio from OpenRouter's video API,
paid in credits only when it is made, downloaded from the dashboard."""

import json
import subprocess

import httpx
import pytest
import respx
from sqlalchemy import select, text

from editor import credits, models as m, montage, videos
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
     "voiceover": "A Bari la fibra arriva in ogni quartiere.",
     "caption": "La fibra ottica a Bari: cosa cambia.", "hashtags": ["#Bari", "fibra ottica", "internet"]},
    {"title": "Cantieri di notte", "hook": "Luci arancioni nel buio", "scene": "Night works, vertical framing",
     "voiceover": "Di notte i cantieri posano i cavi.", "caption": "Di notte si posa la fibra.",
     "hashtags": ["fibra"]},
]}


def maker(**kw):
    return videos.OpenRouterVideo("sk-or-test", http=httpx.Client(), poll=0, **kw)


def catalog(router, model=videos.DEFAULT_MODEL, durations=(1, 5, 10, 15), sizes=("480p", "720p"),
            ratios=("16:9", "9:16"), audio=True, skus=None):
    return router.get(f"{OR}/videos/models").mock(return_value=httpx.Response(200, json={"data": [{
        "id": model, "supported_durations": list(durations), "supported_resolutions": list(sizes),
        "supported_aspect_ratios": list(ratios), "generate_audio": audio, "pricing_skus": skus or {}}]}))


HEYGEN_SKUS = {"duration_seconds_480p": "0.02", "duration_seconds_768p": "0.036",
               "long_duration_seconds_480p": "0.045", "long_duration_seconds_768p": "0.075"}


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


def test_heygen_gets_its_nearest_resolution_and_no_audio_switch(db, tenant):
    did, ids = with_ideas(db, tenant)
    with respx.mock(assert_all_called=False) as router:
        catalog(router, model="heygen/heygen-video-1", durations=tuple(range(5, 31)),
                sizes=("480p", "768p", "1080p", "2K"), audio=False)  # its sound is always on
        submit, _, _ = service(router, cost=0.36)
        out = videos.make(db, tenant, ids[0], maker(model="heygen/heygen-video-1", resolution="720p"))
    body = json.loads(submit.calls[0].request.content)
    assert out["ready"] and body["resolution"] == "768p" and body["duration"] == 10
    assert "generate_audio" not in body and body["aspect_ratio"] == "9:16"


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


def test_the_person_picks_the_length_and_pays_what_the_model_costs(db, tenant):
    _, ids = with_ideas(db, tenant)
    set_plan(db, tenant, credits_month=1000, max_videos_month=0)
    with db.tenant(tenant) as s:  # model price unknown: every 10 seconds
        assert videos.price(s, 30) == 300 and videos.price(s, 25) == 300 and videos.price(s, 8) == 100
    with respx.mock(assert_all_called=False) as router:
        catalog(router, model="heygen/heygen-video-1", durations=tuple(range(5, 31)), sizes=("480p", "768p"),
                audio=False, skus=HEYGEN_SKUS)
        submit, _, _ = service(router, cost=1.35)
        mk = maker(model="heygen/heygen-video-1", seconds=10, resolution="480p")
        assert mk.choices() == [10, 15, 20, 30]
        with db.tenant(tenant) as s:  # 0.20 $ for 10 s, 0.30 $ for 15, 1.35 $ for 30 (over 15 s it costs more)
            assert [videos.price(s, x, mk) for x in (10, 15, 30)] == [100, 150, 675]
        videos.ask(db, tenant, ids[0], "redattore", 30, mk)
        videos.make(db, tenant, ids[0], mk)
    assert json.loads(submit.calls[0].request.content)["duration"] == 30
    with db.tenant(tenant) as s:
        assert credits.state(s)["month_used"] == 675
        row = s.execute(select(m.videos.c.credits, m.videos.c.seconds).where(m.videos.c.id == ids[0])).first()
    assert row.credits == 675 and row.seconds == 30


def test_an_idea_in_another_language_stays_next_to_the_first(db, tenant):
    _, ids = with_ideas(db, tenant)
    redo = {"title": "Fiber reaches Bari", "hook": "A manhole opens", "voiceover": "In Bari fiber reaches every "
            "district.", "caption": "Fiber in Bari: what changes.", "hashtags": ["#Bari", "fiber"]}
    with respx.mock(assert_all_called=False) as router:
        router.post("http://core.test/v1/llm/chat").mock(return_value=chat(json.dumps(redo)))
        out = videos.relanguage(db, tenant, ids[0], CoreClient("http://core.test", "tok"), "en")
    with db.tenant(tenant) as s:
        first = s.execute(select(m.videos).where(m.videos.c.id == ids[0])).first()
        new = s.execute(select(m.videos).where(m.videos.c.id == out["video_id"])).first()
    assert new.language == "en" and new.title == "Fiber reaches Bari" and new.hashtags == ["Bari", "fiber"]
    assert new.scene == first.scene and new.status == "idea" and first.language == "it"
    assert videos.language_of({"settings": {"videos_language": "de"}, "language": "it"}) == "de"
    assert videos.language_of({"language": {"value": "en"}}) == "en" and videos.language_of(None, "fr") == "fr"
    assert videos.seconds_of({"settings": {"videos_seconds": 99}}) == 30 and videos.seconds_of(None, 15) == 15


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
    with respx.mock(assert_all_called=False) as router:
        catalog(router, model="heygen/heygen-video-1", durations=tuple(range(5, 31)), sizes=("480p",), audio=False,
                skus=HEYGEN_SKUS)
        c = TestClient(create_app(db, videomaker=maker(model="heygen/heygen-video-1", resolution="480p")))
        c.post("/login", data={"token": token})
        page = c.get("/videos").text
        assert '<option value="30">30 secondi</option>' in page, page[page.find("sec-"):page.find("sec-") + 900]
        assert "Rifai in questa lingua" not in page  # no model in this app
        assert c.post(f"/ui/videos/{ids[0]}/make", data={"back": "https://evil.example", "seconds": "20"},
                      follow_redirects=False).headers["location"] == "/videos"
        with db.tenant(tenant) as s:
            job = s.execute(select(m.jobs.c.kind, m.jobs.c.args).where(m.jobs.c.kind == "video")).first()
            row = s.execute(select(m.videos.c.status, m.videos.c.seconds).where(m.videos.c.id == ids[0])).first()
        assert job.args["video_id"] == ids[0] and row.status == "queued" and row.seconds == 20
        page = c.get("/videos").text
        assert "Genera il video" not in page.split(f'id="video-{ids[0]}"')[1].split('class="video-idea"')[0]
        # the floating box follows the job wherever the page is scrolled
        assert 'id="work-float"' in page and 'data-status="queued"' in page and "Terminato" in page
        r = c.post(f"/ui/videos/{ids[1]}/language", data={"language": "en"}, follow_redirects=False)
        assert r.status_code == 303  # no model in this app: a message


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


# --- videos in scenes (0.39.5) ----------------------------------------------------------------

SCENES = {"ideas": [{
    "title": "La fibra arriva a Bari", "hook": "Un tombino si apre e ne esce la luce",
    "scenes": [{"shot": f"Vertical 9:16 shot {i}, a street in Bari", "text": f"Scritta {i}",
                "voice": f"Parte {i} della voce."} for i in range(1, 4)],
    "caption": "La fibra ottica a Bari: cosa cambia. Su eullm.eu", "hashtags": ["fibra"]}]}
needs_ffmpeg = pytest.mark.skipif(not montage.available(), reason="ffmpeg is not installed")


@pytest.fixture(scope="module")
def clip_bytes(tmp_path_factory):
    if not montage.available():
        return b""
    out = tmp_path_factory.mktemp("clips") / "c.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=240x426:rate=24", "-f", "lavfi",
                    "-i", "sine=f=440", "-t", "2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out.read_bytes()


@pytest.fixture(scope="module")
def voice_bytes(tmp_path_factory):
    if not montage.available():
        return b""
    out = tmp_path_factory.mktemp("voice") / "v.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220", "-t", "1", str(out)], check=True)
    return out.read_bytes()


def scene_ideas(db, tenant, n=3):
    did, _ = approved_draft(db, tenant)
    with respx.mock(assert_all_called=False) as router:
        llm = router.post("http://core.test/v1/llm/chat").mock(return_value=chat(json.dumps(SCENES)))
        ids = videos.ideas(db, tenant, CoreClient("http://core.test", "tok"), draft_id=did, n=1, seconds=30,
                           scenes=n)
    return ids, json.loads(llm.calls[0].request.content)["messages"][0]["content"]


def scene_service(router, clip, voice, fail_voice=False):
    jobs = iter(f"job{i}" for i in range(10))
    submit = router.post(f"{OR}/videos").mock(side_effect=lambda req: httpx.Response(202, json={"id": next(jobs)}))
    router.get(url__regex=rf"{OR}/videos/job\d+$").mock(side_effect=lambda req: httpx.Response(200, json={
        "id": req.url.path.rsplit("/", 1)[1], "status": "completed", "usage": {"cost": 0.2}}))
    router.get(url__regex=rf"{OR}/videos/job\d+/content").mock(return_value=httpx.Response(200, content=clip))
    router.get(f"{OR}/audio/speech/models").mock(return_value=httpx.Response(200, json={"data": [{
        "id": videos.VOICE_MODEL, "output_modalities": [{"supported_parameters": {"voice": {"values": ["george"]}}}]}]}))
    router.get(f"{OR}/models").mock(return_value=httpx.Response(200, json={"data": [{
        "id": videos.VOICE_MODEL, "pricing": {"prompt": "0.00008"}}]}))
    speech = router.post(f"{OR}/audio/speech").mock(
        return_value=httpx.Response(400, json={"error": "no voice"}) if fail_voice else httpx.Response(200, content=voice))
    return submit, speech


def heygen():
    return maker(model="heygen/heygen-video-1", seconds=30, resolution="480p")


def heygen_catalog(router):
    catalog(router, model="heygen/heygen-video-1", durations=tuple(range(5, 31)), sizes=("480p", "768p"),
            audio=False, skus=HEYGEN_SKUS)


def test_ideas_come_in_scenes_and_never_send_people_away(db, tenant):
    ids, system = scene_ideas(db, tenant)
    assert "3 scene(s) of about 10 seconds" in system and "never invite them" in system
    assert "invitation to read" not in system
    with db.tenant(tenant) as s:
        row = s.execute(select(m.videos).where(m.videos.c.id == ids[0])).first()
    assert [x["text"] for x in row.scenes] == ["Scritta 1", "Scritta 2", "Scritta 3"]
    assert row.voiceover == "Parte 1 della voce. Parte 2 della voce. Parte 3 della voce." and "shot 2" in row.scene
    check = videos._validator(1, 2)
    bad = {"ideas": [{"title": "x", "scenes": [{"shot": "a", "text": "b", "voice": "Leggi tutto su eullm.eu"}]}]}
    with pytest.raises(ValueError):
        check(bad)
    assert videos.scenes_of({"settings": {"videos_scenes": 9}}) == 5 and videos.scenes_of(None, 30) == 3


def test_scenes_keep_each_clip_in_the_cheap_tier(db, tenant):
    set_plan(db, tenant, credits_month=1000, max_videos_month=0)
    with respx.mock(assert_all_called=False) as router:
        heygen_catalog(router)
        mk = heygen()
        with db.tenant(tenant) as s:  # 30 s: one clip 1.35 $, three clips of 10 s 0.60 $
            assert videos.price(s, 30, mk) == 675 and videos.price(s, 30, mk, 3) == 300
        assert mk.choices(3) == [15, 21, 30] and mk.scene_seconds(30, 3) == 10


@needs_ffmpeg
def test_a_video_in_scenes_is_one_clip_each_with_voice_and_texts(db, tenant, clip_bytes, voice_bytes):
    ids, _ = scene_ideas(db, tenant)
    set_plan(db, tenant, credits_month=1000, max_videos_month=0)
    with respx.mock(assert_all_called=False) as router:
        heygen_catalog(router)
        submit, speech = scene_service(router, clip_bytes, voice_bytes)
        videos.ask(db, tenant, ids[0], "redattore", 30, heygen())
        out = videos.make(db, tenant, ids[0], heygen())
    assert out["ready"] and submit.call_count == 3 and speech.call_count == 3
    bodies = [json.loads(c.request.content) for c in submit.calls]
    assert all(b["duration"] == 10 and "no narrator" in b["prompt"] for b in bodies)
    assert json.loads(speech.calls[0].request.content) == {"model": videos.VOICE_MODEL, "input": "Parte 1 della voce.",
                                                            "voice": "george", "response_format": "mp3"}
    with db.tenant(tenant) as s:
        row = s.execute(select(m.videos).where(m.videos.c.id == ids[0])).first()
        assert credits.state(s)["month_used"] == 300
        voice = s.execute(text("SELECT cost FROM editor.llm_usage WHERE purpose = 'video_voice'")).scalar()
    assert row.status == "ready" and row.credits == 300 and row.seconds == 30 and len(row.parts) == 3
    assert abs(row.cost - (0.6 + voice)) < 1e-9 and 0 < voice < 0.01
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", "-"],
                           input=bytes(row.content), capture_output=True, check=True)
    assert 29 < float(probe.stdout) < 31


@needs_ffmpeg
def test_a_restart_in_the_middle_never_pays_a_scene_twice(db, tenant, clip_bytes, voice_bytes):
    ids, _ = scene_ideas(db, tenant)
    with respx.mock(assert_all_called=False) as router:
        heygen_catalog(router)
        scene_service(router, clip_bytes, voice_bytes)
        router.get(url__regex=rf"{OR}/videos/job\d+$").mock(side_effect=httpx.ConnectError("worker lost the network"))
        with pytest.raises(httpx.ConnectError):
            videos.make(db, tenant, ids[0], heygen())
    with db.tenant(tenant) as s:
        parts = s.execute(select(m.videos.c.parts).where(m.videos.c.id == ids[0])).scalar()
    assert [p["ref"] for p in parts] == ["job0", "job1", "job2"]
    with respx.mock(assert_all_called=False) as router:
        heygen_catalog(router)
        submit, _ = scene_service(router, clip_bytes, voice_bytes)
        assert videos.make(db, tenant, ids[0], heygen())["ready"]
    assert not submit.called


@needs_ffmpeg
def test_without_the_voice_the_paid_clips_still_make_the_video(db, tenant, clip_bytes, voice_bytes):
    ids, _ = scene_ideas(db, tenant, n=2)
    with respx.mock(assert_all_called=False) as router:
        heygen_catalog(router)
        scene_service(router, clip_bytes, voice_bytes, fail_voice=True)
        assert videos.make(db, tenant, ids[0], heygen())["ready"]
    with db.tenant(tenant) as s:
        row = s.execute(select(m.videos.c.status, m.videos.c.error).where(m.videos.c.id == ids[0])).first()
    assert row.status == "ready" and "voice" in row.error


def test_the_idea_card_shows_its_scenes_and_the_site_form_the_number(db, tenant):
    scene_ideas(db, tenant)
    c = logged_in(db, tenant, "owner")
    page = c.get("/videos").text
    assert "Scritta 2" in page and "Parte 3 della voce." in page and "Inquadratura per il modello video" in page
    if montage.available():
        assert 'name="scenes"' in page and "3 scene" in page
    with db.tenant(tenant) as s:
        site_id = s.execute(select(m.sites.c.id)).scalar()
    assert c.post(f"/ui/sites/{site_id}/videos-per-day", data={"n": 2, "scenes": 4},
                  follow_redirects=False).status_code == 303
    with db.tenant(tenant) as s:
        body = s.execute(select(m.editorial_profiles.c.body).where(m.editorial_profiles.c.site_id == site_id,
                                                                    m.editorial_profiles.c.status == "approved")).scalar()
    assert body["settings"]["videos_scenes"] == 4
