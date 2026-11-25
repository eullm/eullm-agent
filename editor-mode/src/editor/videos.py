"""Videos for the social networks: Instagram Reels, TikTok, YouTube Shorts.

Francesco, 2026-11-25: from the articles and from the trends of the moment,
short videos with audio made by a video model through OpenRouter; the
newsroom is offered one or more, downloads the one it likes and posts it
itself. Nothing is posted from here.

Two steps, so that only the videos somebody wants are paid for:

1. Ideas (`ideas`): the text model writes N ideas from an article or a
   trend: a title, the hook of the first second, the scene for the video
   model (in English), the narrator's words, the caption and the hashtags
   (in the article's language). About one cent of text, no credits.
2. The video (`make`), when a person presses «Genera il video»: one
   vertical 9:16 clip with audio from OpenRouter's video API (asynchronous:
   submit, poll, download), kept here for KEEP_DAYS days. Measured on
   2026-11-25 on https://openrouter.ai/api/v1/videos/models: the default
   x-ai/grok-imagine-video-1.5-lite costs 0.02 USD a second at 480p and
   0.03 at 720p, audio included (10 seconds at 720p: 0.30 USD). It costs
   the newsroom the credit price of 'video' (credits.py), never euros of
   model; the cost reported by OpenRouter goes to the usage like a model
   call.

The video model's job id is saved before waiting, so a worker restarted in
the middle waits for the same job again instead of paying a second one.
A refusal of the service (content moderation, a job expired) ends the
video as failed, not retried: the same prompt would fail the same way.

Video models write words on screen badly and must not show real people:
the scene never asks for text, logos or recognisable people; the title and
the caption are given as text to paste.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, func, select, update

from . import credits, models as m, usage
from .core_client import CoreError
from .i18n import N_
from .progress import step
from .translate import LANGUAGES

log = logging.getLogger(__name__)

OPENROUTER = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "x-ai/grok-imagine-video-1.5-lite"
ASPECT = "9:16"  # Reels, TikTok and Shorts are vertical
KEEP_DAYS = 30  # the clip's bytes; the idea, its texts and its cost stay
MAX_BYTES = 200 * 1024 * 1024
IDEAS = 3
WORDS_PER_SECOND = 2.3  # what a narrator says calmly
CATALOG_TTL = 6 * 3600


class VideoError(RuntimeError):
    """The service refused or could not make the video: not retried."""


def _height(size: str) -> int:
    size = size.strip().lower()
    if size.endswith("p") and size[:-1].isdigit():
        return int(size[:-1])
    return {"1k": 1080, "2k": 1440, "4k": 2160}.get(size, 0)


def _nearest_size(wanted: str, sizes: list[str]) -> str:
    """The resolution the model has that is closest to the wanted one, the
    lower one on a tie (HeyGen Video has 768p, not 720p)."""
    h = _height(wanted)
    known = [x for x in sizes if _height(x)]
    if not h or not known:
        return sizes[0]
    return min(known, key=lambda x: (abs(_height(x) - h), _height(x)))


class OpenRouterVideo:
    """OpenRouter's video API: POST /videos, poll the job, download its content."""

    name = "openrouter"

    def __init__(self, key: str, model: str = "", resolution: str = "720p", seconds: int = 10,
                 url: str = OPENROUTER, http: httpx.Client | None = None, poll: float = 10.0, wait: float = 1800):
        self.key, self.url = key, url.rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.resolution, self.seconds = resolution or "720p", int(seconds or 10)
        self.http = http or httpx.Client(timeout=60)
        self.poll, self.wait_for = poll, wait
        self._catalog: tuple[float, dict] | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

    def model_info(self) -> dict:
        """What the model accepts (durations, resolutions, aspect ratios, audio),
        from the public catalog; {} when it cannot be read."""
        now = time.monotonic()
        if self._catalog is None or now - self._catalog[0] > CATALOG_TTL:
            info = {}
            try:
                r = self.http.get(f"{self.url}/videos/models")
                if r.status_code < 400:
                    info = next((x for x in (r.json().get("data") or []) if x.get("id") == self.model), {}) or {}
            except (httpx.HTTPError, ValueError):
                log.info("video models catalog not read")
            self._catalog = (now, info)
        return self._catalog[1]

    def request(self, prompt: str) -> dict:
        """The body of the request, fitted to what the model accepts."""
        info = self.model_info()
        ratios = info.get("supported_aspect_ratios")
        if ratios and ASPECT not in ratios:
            raise VideoError(f"{self.model} does not make vertical {ASPECT} videos")
        seconds = self.seconds
        durations = [d for d in (info.get("supported_durations") or []) if isinstance(d, int) and d > 0]
        if durations and seconds not in durations:
            seconds = min(durations, key=lambda d: (abs(d - seconds), d))
        resolution = self.resolution
        sizes = info.get("supported_resolutions") or []
        if sizes and resolution not in sizes:
            resolution = _nearest_size(resolution, sizes)
        body = {"model": self.model, "prompt": prompt, "duration": seconds, "resolution": resolution,
                "aspect_ratio": ASPECT}
        if info.get("generate_audio") is not False:  # False: the model makes its sound itself, no switch
            body["generate_audio"] = True
        return body

    def submit(self, body: dict) -> str:
        r = self.http.post(f"{self.url}/videos", headers=self._headers(), json=body)
        if 400 <= r.status_code < 500:  # not billed: wrong options, no credit, refused prompt
            raise VideoError(f"video service answered {r.status_code}: {r.text[:300]}")
        r.raise_for_status()  # 5xx: retried later
        ref = (r.json() or {}).get("id")
        if not ref:
            raise VideoError("video service gave no job id")
        return str(ref)

    def wait(self, ref: str) -> dict:
        """The finished job (status completed), or VideoError."""
        until = time.monotonic() + self.wait_for
        while True:
            r = self.http.get(f"{self.url}/videos/{ref}", headers=self._headers())
            if r.status_code == 404:
                raise VideoError("video job not found")
            r.raise_for_status()
            job = r.json() or {}
            status = job.get("status")
            if status == "completed":
                return job
            if status in ("failed", "cancelled", "expired"):
                raise VideoError(f"video {status}: {str(job.get('error') or '')[:300]}")
            if time.monotonic() > until:
                raise TimeoutError("video service did not finish in time")
            time.sleep(self.poll)

    def download(self, ref: str, job: dict | None = None) -> bytes:
        url = ((job or {}).get("unsigned_urls") or [f"{self.url}/videos/{ref}/content?index=0"])[0]
        if not url.startswith(self.url + "/"):  # only OpenRouter's own address gets the key
            url = f"{self.url}/videos/{ref}/content?index=0"
        with self.http.stream("GET", url, headers=self._headers(), follow_redirects=True) as r:
            if r.status_code in (404, 410):
                raise VideoError(f"video no longer available ({r.status_code})")
            r.raise_for_status()
            data = bytearray()
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > MAX_BYTES:
                    raise VideoError("video too large")
        if not data:
            raise VideoError("video service returned an empty file")
        return bytes(data)


def from_settings(st) -> OpenRouterVideo | None:
    """The video service of the settings, or None without an OpenRouter key."""
    key = st.video_api_key or (st.images_api_key if (st.images_api_key or "").startswith("sk-or-") else "")
    if not key:
        return None
    return OpenRouterVideo(key, st.video_model, st.video_resolution, st.video_seconds)


def mime_of(content: bytes) -> str:
    if content[4:8] == b"ftyp":
        return "video/quicktime" if content[8:10] == b"qt" else "video/mp4"
    if content.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    return "video/mp4"


# --- ideas -----------------------------------------------------------------------------

SYSTEM = (
    "You plan short vertical videos (9:16, about {seconds} seconds, with sound) for Instagram Reels, TikTok and "
    "YouTube Shorts. An AI video model makes each one from a single prompt, to bring people to a site's {what}. "
    "Write {n} different ideas: different angles, scenes and hooks, all true to the {what}: never state or show "
    "anything it does not say.\n"
    "For each idea:\n"
    "- title: a short internal name, at most 8 words, in {language};\n"
    "- hook: what happens in the first second to stop the scroll, one sentence in {language};\n"
    "- scene: the prompt for the video model, in English, 60 to 120 words: vertical 9:16 framing, one continuous "
    "shot or two or three quick cuts, the camera movement, the subject, the place, the light, realistic like "
    "documentary footage shot on a phone or a cinema camera; the sounds of the place. Never ask for text, "
    "captions, letters, numbers, logos, brand marks, flags of real organisations, recognisable or famous real "
    "people, or children: show places, objects, technology and anonymous people seen from behind or at a "
    "distance;\n"
    "- voiceover: what a narrator says, in {language}, at most {words} words, clear and lively, true to the "
    "{what}, ending with an invitation to read more on {site};\n"
    "- caption: the text of the post, in {language}, one to three short sentences, ending with where to read "
    "more ({site});\n"
    "- hashtags: 4 to 6 hashtags without #, lowercase, the ones people really search for this subject.\n"
    'Reply with JSON only: {{"ideas": [{{"title": "...", "hook": "...", "scene": "...", "voiceover": "...", '
    '"caption": "...", "hashtags": ["...", "..."]}}]}}'
)


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _validator(n: int):
    def check(value):
        items = value.get("ideas") if isinstance(value, dict) else value
        if not isinstance(items, list) or not items:
            raise ValueError("ideas must be a non-empty list")
        out = []
        for x in items[:n]:
            if not isinstance(x, dict):
                raise ValueError("each idea is an object")
            idea = {k: _clean(x.get(k), lim) for k, lim in (
                ("title", 120), ("hook", 300), ("scene", 1500), ("voiceover", 600), ("caption", 1000))}
            if not idea["title"] or not idea["scene"]:
                raise ValueError("each idea needs a title and a scene")
            tags = x.get("hashtags") or []
            if isinstance(tags, str):
                tags = tags.replace(",", " ").split()
            idea["hashtags"] = [t for t in (_clean(t, 40).lstrip("#").replace(" ", "") for t in tags[:8]) if t]
            out.append(idea)
        return out
    return check


def _profile(s, site_id: int) -> dict:
    p = m.editorial_profiles
    return s.execute(select(p.c.body).where(p.c.site_id == site_id, p.c.status == "approved")).scalar() or {}


def ideas(db, tenant_id: str, core, draft_id: int | None = None, topic_id: int | None = None,
          site_id: int | None = None, n: int = IDEAS, seconds: int = 10, extra: dict | None = None) -> list[int]:
    """Write `n` video ideas from an article (draft_id) or a trend (topic_id
    and the site); the unused ideas of the same article or trend are
    replaced. Returns their ids."""
    from .proposals import _items_for

    with db.tenant(tenant_id) as s:
        if draft_id is not None:
            d = s.execute(select(m.drafts).where(m.drafts.c.id == draft_id)).first()
            if d is None:
                raise LookupError(draft_id)
            site_id = d.site_id
            prof = _profile(s, site_id)
            language = d.language or prof.get("language") or "it"
            what = "article"
            lead = d.body_md.split("\n## ", 1)[0][:2500]
            material = f"Title: {d.title}\n{d.subtitle or ''}\n\n{lead}"
            ref = f"draft:{draft_id}"
        else:
            label = s.execute(select(m.topics.c.label).where(m.topics.c.id == topic_id)).scalar()
            if label is None or site_id is None:
                raise LookupError(topic_id)
            prof = _profile(s, site_id)
            language = prof.get("language") or "it"
            what = "news topic"
            items = _items_for(s, topic_id, site_id, limit=8)
            material = f"Topic of the moment: {label}\n\nLatest news on it:\n" + "\n".join(
                f"- {it.title} ({it.source}): {(it.summary or '')[:300]}" for it in items)
            ref = f"topic:{topic_id}"
            social = (extra or {}).get("social") or {}
            talk = [f"On Bluesky: \"{b['label']}\" ({b.get('posts') or 0} posts)" for b in social.get("bluesky") or []]
            top = (social.get("youtube") or {}).get("top")
            if top:
                talk.append(f"Most seen YouTube Short of the last 48 hours: \"{top['title']}\" ({top['views']} views)")
            if talk:
                material += "\n\nWhat people say on the social networks right now:\n" + "\n".join(f"- {x}" for x in talk)
        domain = s.execute(select(m.sites.c.domain).where(m.sites.c.id == site_id)).scalar() or ""
    if core is None:
        raise CoreError("a model is needed for the video ideas")
    words = max(10, int(seconds * WORDS_PER_SECOND))
    system = SYSTEM.format(seconds=seconds, what=what, n=n, words=words, site=domain or "the site",
                           language=LANGUAGES.get((language or "it")[:2], language))
    audience = prof.get("audience")
    audience = audience.get("value") if isinstance(audience, dict) else audience
    user = (f"Site: {prof.get('name') or domain} ({domain}); audience: {_clean(audience, 300) or 'not determined'}; "
            f"tone: {(prof.get('style') or {}).get('tone') or 'not determined'}\n\n{material}")
    step(N_("Il modello scrive le idee per i video"))
    with usage.tagged(db, tenant_id, "video_ideas", site_id=site_id, ref=ref):
        found, _calls = core.chat_json(system, user, _validator(n))
    v = m.videos.c
    created = []
    with db.tenant(tenant_id) as s:
        old = (v.draft_id == draft_id) if draft_id is not None else ((v.topic_id == topic_id) & (v.site_id == site_id))
        s.execute(delete(m.videos).where(old, v.status == "idea"))
        for x in found:
            vid = s.execute(m.videos.insert().values(
                tenant_id=tenant_id, site_id=site_id, draft_id=draft_id, topic_id=topic_id, language=language,
                status="idea", **(extra or {}), **x).returning(v.id)).scalar_one()
            created.append(vid)
    return created


def prompt_of(row) -> str:
    """The video model's prompt: the scene, the narrator's words, the sound."""
    language = LANGUAGES.get((row.language or "it")[:2], row.language or "Italian")
    out = row.scene.strip()
    if row.voiceover:
        voice = row.voiceover.replace('"', "'")
        out += (f' Audio: a warm, natural {language} narrator voice-over says, clearly and in {language}: "{voice}" '
                "Under it, the real sounds of the scene; no song with lyrics.")
    out += " Vertical 9:16 video. No on-screen text, no subtitles, no logos, no watermarks."
    return out[:2500]


# --- the morning round -----------------------------------------------------------------

RECENT_DAYS = 7  # a topic that already had a video idea this week is not proposed again
DEFAULT_PER_DAY = {"web": 0, "social": 3}
MAX_PER_DAY = 10


def per_day(body: dict | None, kind: str = "web") -> int:
    """How many video ideas the site or channel gets every morning."""
    st = (body or {}).get("settings") or {}
    n = st.get("videos_per_day")
    return max(0, min(MAX_PER_DAY, int(n))) if isinstance(n, int) and not isinstance(n, bool) \
        else DEFAULT_PER_DAY.get(kind, 0)


def round_due(body: dict | None, kind: str, local: datetime, briefing_hour: int) -> bool:
    """Once a day, from the briefing hour, when the site asks for any."""
    st = (body or {}).get("settings") or {}
    return per_day(body, kind) > 0 and local.hour >= briefing_hour and st.get("videos_last") != local.date().isoformat()


def pick_topics(db, tenant_id: str, site_id: int, n: int, now: datetime | None = None) -> list[dict]:
    """The topics for the morning's video ideas: those that fit the site or
    channel (Opportunity) and, first, those climbing before they are
    everywhere (trends.rising); not those that had a video idea this week."""
    from . import opportunity, repo, trends

    now = now or datetime.now(UTC)
    found = opportunity.score_site(db, tenant_id, site_id, now)
    archive = repo.archive_of(db, tenant_id)
    with db.tenant(tenant_id) as s:
        up = trends.rising(s, archive, now)
        v = m.videos.c
        done = set(s.execute(select(v.topic_id).where(
            v.site_id == site_id, v.topic_id.is_not(None),
            v.created_at >= now - timedelta(days=RECENT_DAYS))).scalars())
    ranked = []
    for o in found:
        if o.topic_id in done:
            continue
        r = up.get(o.topic_id) or {}
        # Climbing, and still few news: the video comes out before the wave.
        # Talked about on Bluesky or with Shorts climbing on YouTube (social.py):
        # the people are already there.
        social = r.get("social") or None
        score = (o.score + 40 * r.get("rising", 0) + (10 if r.get("early") and r else 0)
                 + 30 * ((social or {}).get("heat") or 0))
        ranked.append({"topic_id": o.topic_id, "score": score, "gain": r.get("gain") or None, "social": social,
                       "hype": r.get("hype", round(100 * (o.components.get("hype") or 0), 1))})
    ranked.sort(key=lambda x: -x["score"])
    return ranked[:n]


def daily_round(db, tenant_id: str, site_id: int, core, n: int, seconds: int = 10,
                now: datetime | None = None) -> list[int]:
    """The morning's video ideas of a site or channel: one idea for each of
    the `n` best topics. Returns their ids."""
    created = []
    for x in pick_topics(db, tenant_id, site_id, n, now):
        try:
            created += ideas(db, tenant_id, core, topic_id=x["topic_id"], site_id=site_id, n=1, seconds=seconds,
                             extra={"auto": True, "gain": x["gain"], "hype": x["hype"], "social": x.get("social")})
        except (CoreError, LookupError) as e:
            log.warning("video idea for topic %s failed: %s", x["topic_id"], e)
    return created


# --- the video -------------------------------------------------------------------------


def allowance(s, now: datetime | None = None) -> dict:
    """The month's videos of the plan: included (None: no limit), used, left."""
    tid = s.info["tenant_id"]
    limit = s.execute(select(m.tenants.c.max_videos_month).where(m.tenants.c.tenant_id == tid)).scalar()
    if limit is None:
        return {"included": None, "used": 0, "left": None}
    v = m.videos.c
    used = s.execute(select(func.count()).select_from(m.videos).where(
        v.included.is_(True), v.made_at >= credits.month_start(s, now))).scalar() or 0
    return {"included": limit, "used": used, "left": max(0, limit - used)}


def price(s) -> int:
    """What the next video costs the newsroom in credits: 0 while the month's
    videos of the plan last, or when the newsroom does not use credits."""
    a = allowance(s)
    if a["included"] is None or a["left"]:
        return 0
    tid = s.info["tenant_id"]
    if s.execute(select(m.tenants.c.credits_month).where(m.tenants.c.tenant_id == tid)).scalar() is None:
        raise credits.NoCredits("no videos left in the plan and no credits")
    return credits.price(s, "video")


def price_or_none(s) -> int | None:
    """The price, or None when the plan's videos are used up and the newsroom has no credits."""
    try:
        return price(s)
    except credits.NoCredits:
        return None


def ask(db, tenant_id: str, video_id: int, by: str) -> None:
    """A person wants the video: check the credits and mark it queued."""
    with db.tenant(tenant_id) as s:
        row = s.execute(select(m.videos.c.status).where(m.videos.c.id == video_id)).first()
        if row is None:
            raise LookupError(video_id)
        credits.need(s, price(s))
        if row.status in ("idea", "failed"):
            s.execute(update(m.videos).where(m.videos.c.id == video_id).values(
                status="queued", error=None, made_by=by[:200] if by else None))


def make(db, tenant_id: str, video_id: int, maker: OpenRouterVideo) -> dict:
    """Make the video of an idea (or wait again for the one already asked to
    the service). Returns what happened."""
    v = m.videos.c
    with db.tenant(tenant_id) as s:
        row = s.execute(select(m.videos).where(v.id == video_id)).first()
        if row is None:
            raise LookupError(video_id)
        if row.status == "ready":
            return {"video_id": video_id, "site_id": row.site_id, "title": row.title, "ready": True}
        if not row.job_ref:
            credits.need(s, price(s))  # nothing asked to the service yet: checked again
    started = time.monotonic()
    ref = row.job_ref
    job = None
    with usage.tagged(db, tenant_id, "video", site_id=row.site_id,
                      ref=f"draft:{row.draft_id}" if row.draft_id else f"topic:{row.topic_id}"):
        try:
            if not ref:
                body = maker.request(prompt_of(row))
                step(N_("Il modello video gira la clip"))
                ref = maker.submit(body)
                with db.tenant(tenant_id) as s:
                    s.execute(update(m.videos).where(v.id == video_id).values(
                        status="making", job_ref=ref, model=maker.model, seconds=body["duration"],
                        resolution=body["resolution"]))
            else:
                step(N_("Il modello video gira la clip"))
            job = maker.wait(ref)
            step(N_("Scarico il video"))
            content = maker.download(ref, job)
        except VideoError as e:
            usage.record(maker.model, None, None, int((time.monotonic() - started) * 1000), str(e))
            with db.tenant(tenant_id) as s:  # not retried; asked again, it is a new job
                s.execute(update(m.videos).where(v.id == video_id).values(
                    status="failed", error=str(e)[:500], job_ref=None))
            return {"video_id": video_id, "site_id": row.site_id, "title": row.title, "failed": str(e)[:300]}
        cost = (job.get("usage") or {}).get("cost")
        cost = float(cost) if isinstance(cost, (int, float)) else None
        usage.record(maker.model, None, cost, int((time.monotonic() - started) * 1000))
    with db.tenant(tenant_id) as s:
        credits.lock(s, f"editor-quota:{tenant_id}")
        a = allowance(s)
        included, paid = a["included"] is not None and a["left"] > 0, 0
        if a["included"] is not None and not included:
            try:
                paid = credits.spend(s, "video", f"video:{video_id}")
            except credits.NoCredits:  # credits used elsewhere while it was made: it is already paid for
                log.warning("video %s made without credits left", video_id)
        s.execute(update(m.videos).where(v.id == video_id).values(
            status="ready", content=content, mime=mime_of(content), size_bytes=len(content), cost=cost,
            credits=paid, included=included, made_at=datetime.now(UTC), error=None))
    forget_old(db, tenant_id)
    return {"video_id": video_id, "site_id": row.site_id, "title": row.title, "ready": True}


def forget_old(db, tenant_id: str, now: datetime | None = None) -> int:
    """The bytes of the videos made more than KEEP_DAYS days ago (they are
    downloaded and posted by then); their ideas, texts and costs stay."""
    v = m.videos.c
    limit = (now or datetime.now(UTC)) - timedelta(days=KEEP_DAYS)
    with db.tenant(tenant_id) as s:
        return s.execute(update(m.videos).where(v.status == "ready", v.content.is_not(None), v.made_at < limit)
                         .values(content=None)).rowcount or 0


def of_draft(s, draft_id: int) -> list:
    v = m.videos.c
    return s.execute(select(*_listed()).where(v.draft_id == draft_id).order_by(v.id)).all()


def of_site(s, site_id: int, limit: int = 60) -> list:
    v = m.videos.c
    return s.execute(select(*_listed(), m.drafts.c.title.label("article"), m.topics.c.label.label("topic"))
                     .select_from(m.videos.outerjoin(m.drafts, m.drafts.c.id == v.draft_id)
                                  .outerjoin(m.topics, m.topics.c.id == v.topic_id))
                     .where(v.site_id == site_id).order_by(v.id.desc()).limit(limit)).all()


def _listed():
    v = m.videos.c
    return (v.id, v.site_id, v.draft_id, v.topic_id, v.language, v.title, v.hook, v.scene, v.voiceover, v.caption,
            v.hashtags, v.status, v.model, v.seconds, v.resolution, v.credits, v.error, v.size_bytes, v.created_at,
            v.made_at, v.included, v.hype, v.gain, v.auto, v.social, (v.content.is_not(None)).label("has_file"))


def post_text(v) -> str:
    """The text to paste with the video: the caption, then the hashtags."""
    tags = " ".join(f"#{t}" for t in (v.hashtags or []) if t)
    return (v.caption or "") + (f"\n\n{tags}" if tags else "")


def content(s, video_id: int):
    v = m.videos.c
    return s.execute(select(v.mime, v.content, v.title).where(v.id == video_id)).first()


def remove(db, tenant_id: str, video_id: int) -> bool:
    with db.tenant(tenant_id) as s:
        return bool(s.execute(delete(m.videos).where(m.videos.c.id == video_id,
                                                     m.videos.c.status.in_(("idea", "ready", "failed")))).rowcount)
