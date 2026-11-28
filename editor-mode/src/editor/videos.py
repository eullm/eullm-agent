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

Scenes (0.39.5; Francesco, 2026-11-28: "una scena lunga non mi piace",
"scritte in sovraimpressione", "30 sec mi sono costati oltre un dollaro").
An idea is written in N scenes (Video social › Scene per video, 1 to 5),
each with its shot for the video model, a short text on screen and the
narrator's words of the scene. The video is one clip per scene, so each
stays in the model's cheaper tier (HeyGen Video: 0.02 USD a second at 480p
up to 15 seconds, 0.045 over: 30 seconds in one clip cost 1.35 USD, in
three scenes of 10 seconds 0.60). The clips have only the sound of the
place; the narrator is one text-to-speech voice through OpenRouter
(elevenlabs/eleven-multilingual-v2, 0.00008 USD a character: about 3 cents
for 30 seconds), the same in all the scenes; ffmpeg draws the texts and
joins the scenes (montage.py). Each clip's job id is saved as soon as it
is asked, so a restart waits for the same clips and never pays them twice.
The voice never sends people to the site ("lasciamo che restino sul
video"): only the caption of the post may say where to read more.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, func, select, update

from . import credits, models as m, montage, usage
from .core_client import CoreError
from .i18n import N_
from .progress import step
from .translate import LANGUAGES

log = logging.getLogger(__name__)

OPENROUTER = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "x-ai/grok-imagine-video-1.5-lite"
CHOICES = (10, 15, 20, 30)  # the durations offered to the newsroom
MAX_SECONDS = 30
LONG_SECONDS = 15  # over it some models (HeyGen Video) cost more per second
ASPECT = "9:16"  # Reels, TikTok and Shorts are vertical
KEEP_DAYS = 30  # the clip's bytes; the idea, its texts and its cost stay
MAX_BYTES = 200 * 1024 * 1024
IDEAS = 3
WORDS_PER_SECOND = 2.3  # what a narrator says calmly
CATALOG_TTL = 6 * 3600
MAX_SCENES = 5
VOICE_MODEL = "elevenlabs/eleven-multilingual-v2"
VOICE = "george"
SILENT = " Only the natural sounds of the place: no narrator, no dialogue, no song with lyrics."


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
                 url: str = OPENROUTER, http: httpx.Client | None = None, poll: float = 10.0, wait: float = 1800,
                 voice_model: str = "", voice: str = ""):
        self.key, self.url = key, url.rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.voice_model, self.voice = voice_model or VOICE_MODEL, voice or VOICE
        self._voices: tuple[float, dict] | None = None
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
                r = self.http.get(f"{self.url}/videos/models", timeout=10)
                if r.status_code < 400:
                    info = next((x for x in (r.json().get("data") or []) if x.get("id") == self.model), {}) or {}
            except (httpx.HTTPError, ValueError):
                log.info("video models catalog not read")
            self._catalog = (now, info)
        return self._catalog[1]

    def fit_seconds(self, seconds: int | None = None) -> int:
        """The duration the model makes nearest to the one asked."""
        seconds = int(seconds or self.seconds)
        info = self.model_info()
        durations = [d for d in (info.get("supported_durations") or []) if isinstance(d, int) and d > 0]
        if durations and seconds not in durations:
            seconds = min(durations, key=lambda d: (abs(d - seconds), d))
        return seconds

    def fit_resolution(self) -> str:
        sizes = self.model_info().get("supported_resolutions") or []
        return _nearest_size(self.resolution, sizes) if sizes and self.resolution not in sizes else self.resolution

    def scene_seconds(self, seconds: int | None = None, scenes: int = 1) -> int:
        """The length of each clip when `seconds` are split in `scenes`."""
        seconds = int(seconds or self.seconds)
        return self.fit_seconds(max(1, round(seconds / max(1, scenes))))

    def choices(self, scenes: int = 1) -> list[int]:
        """The durations a person can pick: 10, 15, 20, 30 seconds as the
        model makes them (in `scenes` clips), up to MAX_SECONDS."""
        out = []
        for c in CHOICES:
            x = self.scene_seconds(c, scenes) * max(1, scenes)
            if x not in out and x <= MAX_SECONDS:
                out.append(x)
        return sorted(out)

    def cost(self, seconds: int) -> float | None:
        """What the clip costs on OpenRouter (USD), from the catalog's prices
        per second; a longer tier where the model has one (HeyGen Video over
        15 seconds). None when the model is priced otherwise (tokens)."""
        skus = self.model_info().get("pricing_skus") or {}
        res = self.fit_resolution()
        rate = skus.get(f"duration_seconds_{res}") or skus.get("duration_seconds")
        if seconds > LONG_SECONDS:
            rate = skus.get(f"long_duration_seconds_{res}") or rate
        try:
            return float(rate) * seconds if rate is not None else None
        except (TypeError, ValueError):
            return None

    def request(self, prompt: str, seconds: int | None = None) -> dict:
        """The body of the request, fitted to what the model accepts."""
        info = self.model_info()
        ratios = info.get("supported_aspect_ratios")
        if ratios and ASPECT not in ratios:
            raise VideoError(f"{self.model} does not make vertical {ASPECT} videos")
        seconds = self.fit_seconds(seconds)
        resolution = self.fit_resolution()
        body = {"model": self.model, "prompt": prompt, "duration": seconds, "resolution": resolution,
                "aspect_ratio": ASPECT}
        if info.get("generate_audio") is not False:  # False: the model makes its sound itself, no switch
            body["generate_audio"] = True
        return body

    def voice_info(self) -> dict:
        """The voice model's voices and price per character, {} when unknown."""
        now = time.monotonic()
        if self._voices is None or now - self._voices[0] > CATALOG_TTL:
            info: dict = {}
            try:
                r = self.http.get(f"{self.url}/audio/speech/models", timeout=10)
                x = next((y for y in (r.json().get("data") or []) if y.get("id") == self.voice_model), None) \
                    if r.status_code < 400 else None
                if x:
                    params = ((x.get("output_modalities") or [{}])[0] or {}).get("supported_parameters") or {}
                    info["voices"] = (params.get("voice") or {}).get("values") or []
                r = self.http.get(f"{self.url}/models", params={"output_modalities": "speech"}, timeout=10)
                if r.status_code < 400:
                    x = next((y for y in (r.json().get("data") or []) if y.get("id") == self.voice_model), None)
                    if x:
                        info["per_char"] = float((x.get("pricing") or {}).get("prompt") or 0)
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                log.info("speech models catalog not read")
            self._voices = (now, info)
        return self._voices[1]

    def speech(self, text: str) -> tuple[bytes, float | None]:
        """The narrator saying `text` (MP3) and its estimated cost in USD."""
        info = self.voice_info()
        voices = info.get("voices") or []
        voice = self.voice if not voices or self.voice in voices else voices[0]
        r = self.http.post(f"{self.url}/audio/speech", headers=self._headers(), timeout=120, json={
            "model": self.voice_model, "input": text, "voice": voice, "response_format": "mp3"})
        if 400 <= r.status_code < 500:
            raise VideoError(f"voice service answered {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
        if not r.content:
            raise VideoError("voice service returned an empty file")
        rate = info.get("per_char")
        return r.content, (rate * len(text) if rate is not None else None)

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
    return OpenRouterVideo(key, st.video_model, st.video_resolution, st.video_seconds,
                           voice_model=getattr(st, "video_voice_model", ""), voice=getattr(st, "video_voice", ""))


def mime_of(content: bytes) -> str:
    if content[4:8] == b"ftyp":
        return "video/quicktime" if content[8:10] == b"qt" else "video/mp4"
    if content.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    return "video/mp4"


# --- ideas -----------------------------------------------------------------------------

SYSTEM = (
    "You plan short vertical videos (9:16, {seconds} seconds, with sound) for Instagram Reels, TikTok and "
    "YouTube Shorts, about a site's {what}. Each video is {scenes} scene(s) of about {scene_seconds} seconds; an AI "
    "video model makes each scene from its own prompt, a narrator voice speaks over them and a short text appears "
    "on screen. Write {n} different ideas: different angles, scenes and hooks, all true to the {what}: never "
    "state or show anything it does not say. The video must hold people on the video itself: never invite them "
    "to visit, read or click anything, never name the site in the voice or the texts.\n"
    "For each idea:\n"
    "- title: a short internal name, at most 8 words, in {language};\n"
    "- hook: what happens in the first second to stop the scroll, one sentence in {language};\n"
    "- scenes: exactly {scenes} scene(s), in order, each with:\n"
    "  - shot: the prompt for the video model, in English, 40 to 90 words: vertical 9:16 framing, one continuous "
    "shot, the camera movement, the subject, the place, the light, realistic like documentary footage shot on a "
    "phone or a cinema camera; the sounds of the place. The scenes of an idea look like one film (same light and "
    "style) but show different things. Never ask for text, captions, letters, numbers, logos, brand marks, flags "
    "of real organisations, recognisable or famous real people, or children: show places, objects, technology "
    "and anonymous people seen from behind or at a distance;\n"
    "  - text: the words on screen during the scene, in {language}, 2 to 6 words, punchy, the strongest fact or "
    "number of the scene (the first scene's text is the hook);\n"
    "  - voice: what the narrator says during the scene, in {language}, at most {scene_words} words, clear and "
    "lively; the last scene ends the story with a strong line, not with a call to visit anything;\n"
    "- caption: the text of the post, in {language}, one to three short sentences; it may end with where to read "
    "more ({site});\n"
    "- hashtags: 4 to 6 hashtags without #, lowercase, the ones people really search for this subject.\n"
    'Reply with JSON only: {{"ideas": [{{"title": "...", "hook": "...", "scenes": [{{"shot": "...", "text": "...", '
    '"voice": "..."}}], "caption": "...", "hashtags": ["...", "..."]}}]}}'
)

# "leggi su eullm.eu" (Francesco, 2026-11-28: "non va bene, lasciamo che restino sul video")
INVITE = re.compile(r"\b(leggi (di più|tutto|l'articolo|su)|scopri di più|read more|read the full|link in bio|"
                    r"visit (our|the) (site|website)|visita il (nostro )?sito|mehr lesen|lesen sie mehr|"
                    r"lisez la suite|en savoir plus|lee más|más información en)\b", re.IGNORECASE)


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _tags(tags) -> list[str]:
    tags = tags or []
    if isinstance(tags, str):
        tags = tags.replace(",", " ").split()
    return [t for t in (_clean(t, 40).lstrip("#").replace(" ", "") for t in tags[:8]) if t]


def _scenes(items, n: int, need_shot: bool = True) -> list[dict]:
    out = []
    for x in (items if isinstance(items, list) else [])[:n]:
        if not isinstance(x, dict):
            continue
        sc = {"shot": _clean(x.get("shot"), 900), "text": _clean(x.get("text"), 60), "voice": _clean(x.get("voice"), 400)}
        if sc["shot"] or not need_shot:
            out.append(sc)
    return out


def joined(scenes: list[dict]) -> dict:
    """The whole scene and voice of the scenes, as the ideas without scenes have them."""
    return {"scene": "\n\n".join(x["shot"] for x in scenes if x.get("shot"))[:4000],
            "voiceover": " ".join(x["voice"] for x in scenes if x.get("voice"))[:1500]}


def _validator(n: int, scenes: int = 1):
    def check(value):
        items = value.get("ideas") if isinstance(value, dict) else value
        if not isinstance(items, list) or not items:
            raise ValueError("ideas must be a non-empty list")
        out = []
        for x in items[:n]:
            if not isinstance(x, dict):
                raise ValueError("each idea is an object")
            idea = {k: _clean(x.get(k), lim) for k, lim in (("title", 120), ("hook", 300), ("caption", 1000))}
            sc = _scenes(x.get("scenes"), scenes)
            if sc:
                idea.update(joined(sc), scenes=sc)
            else:  # one scene the old way: one clip with the narrator, no texts on screen
                idea.update(scene=_clean(x.get("scene"), 1500), voiceover=_clean(x.get("voiceover"), 600))
            if not idea["title"] or not idea["scene"]:
                raise ValueError("each idea needs a title and its scenes")
            if INVITE.search(idea["voiceover"]) or any(INVITE.search(y["text"]) for y in sc):
                raise ValueError("the voice and the texts on screen never invite to read or visit anything")
            idea["hashtags"] = _tags(x.get("hashtags"))
            out.append(idea)
        return out
    return check


def _profile(s, site_id: int) -> dict:
    p = m.editorial_profiles
    return s.execute(select(p.c.body).where(p.c.site_id == site_id, p.c.status == "approved")).scalar() or {}


def ideas(db, tenant_id: str, core, draft_id: int | None = None, topic_id: int | None = None,
          site_id: int | None = None, n: int = IDEAS, seconds: int = 10, extra: dict | None = None,
          language: str | None = None, scenes: int | None = None) -> list[int]:
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
            language = language or language_of(prof, d.language)
            what = "article"
            lead = d.body_md.split("\n## ", 1)[0][:2500]
            material = f"Title: {d.title}\n{d.subtitle or ''}\n\n{lead}"
            ref = f"draft:{draft_id}"
        else:
            label = s.execute(select(m.topics.c.label).where(m.topics.c.id == topic_id)).scalar()
            if label is None or site_id is None:
                raise LookupError(topic_id)
            prof = _profile(s, site_id)
            language = language or language_of(prof)
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
    scenes = scenes or scenes_of(prof, seconds)
    each = max(1, round(seconds / scenes))
    system = SYSTEM.format(seconds=seconds, what=what, n=n, scenes=scenes, scene_seconds=each,
                           scene_words=max(4, int(each * WORDS_PER_SECOND)), site=domain or "the site",
                           language=LANGUAGES.get((language or "it")[:2], language))
    audience = prof.get("audience")
    audience = audience.get("value") if isinstance(audience, dict) else audience
    user = (f"Site: {prof.get('name') or domain} ({domain}); audience: {_clean(audience, 300) or 'not determined'}; "
            f"tone: {(prof.get('style') or {}).get('tone') or 'not determined'}\n\n{material}")
    step(N_("Il modello scrive le idee per i video"))
    with usage.tagged(db, tenant_id, "video_ideas", site_id=site_id, ref=ref):
        found, _calls = core.chat_json(system, user, _validator(n, scenes))
    v = m.videos.c
    created = []
    with db.tenant(tenant_id) as s:
        old = (v.draft_id == draft_id) if draft_id is not None else ((v.topic_id == topic_id) & (v.site_id == site_id))
        s.execute(delete(m.videos).where(old, v.status == "idea"))
        for x in found:
            vid = s.execute(m.videos.insert().values(
                tenant_id=tenant_id, site_id=site_id, draft_id=draft_id, topic_id=topic_id, language=language,
                status="idea", seconds=seconds, **(extra or {}), **x).returning(v.id)).scalar_one()
            created.append(vid)
    return created


RELANGUAGE = (
    "You rewrite the texts of a short vertical video for the social networks in {language}: same idea, same "
    "facts, natural for native speakers (not a word-for-word translation). Keep the same number of scenes, in "
    "order: each scene's text on screen stays 2 to 6 punchy words, its voice about as long as now. Never invite "
    "people to read, visit or click anything. Hashtags: the ones people use in {language}, without #. Reply with "
    "JSON only: {{\"title\": ..., \"hook\": ..., \"scenes\": [{{\"text\": ..., \"voice\": ...}}], "
    "\"caption\": ..., \"hashtags\": [...]}}.")


def relanguage(db, tenant_id: str, video_id: int, core, language: str) -> dict:
    """The same idea in another language: a new idea next to the first, its
    texts rewritten (the shots for the video model stay in English)."""
    language = _lang_code(language) or "it"
    with db.tenant(tenant_id) as s:
        row = s.execute(select(m.videos).where(m.videos.c.id == video_id)).first()
    if row is None:
        raise LookupError(video_id)
    if core is None:
        raise CoreError("a model is needed for the video ideas")
    old = scenes_of_row(row)
    if not row.scenes:  # an idea of before the scenes: its voice as one scene
        old = [{"shot": "", "text": "", "voice": row.voiceover or ""}]

    def check(value):
        if not isinstance(value, dict):
            raise ValueError("an object is needed")
        out = {k: _clean(value.get(k), lim) for k, lim in (("title", 120), ("hook", 300), ("caption", 1000))}
        if not row.scenes and value.get("voiceover") and not value.get("scenes"):
            value = {**value, "scenes": [{"text": "", "voice": value["voiceover"]}]}
        new = _scenes(value.get("scenes"), len(old), need_shot=False)
        if not out["title"] or len(new) != len(old) or not all(x["voice"] for x in new):
            raise ValueError(f"title and {len(old)} scenes with their voice are needed")
        if any(INVITE.search(y["voice"]) or INVITE.search(y["text"]) for y in new):
            raise ValueError("the voice and the texts on screen never invite to read or visit anything")
        if not row.scenes:
            out.update(voiceover=new[0]["voice"], hashtags=_tags(value.get("hashtags")))
            return out
        sc = [{"shot": o["shot"], "text": x["text"], "voice": x["voice"]} for o, x in zip(old, new, strict=True)]
        out.update(joined(sc), scenes=sc, hashtags=_tags(value.get("hashtags")))
        return out

    user = json.dumps({"title": row.title, "hook": row.hook, "caption": row.caption, "hashtags": row.hashtags,
                       "scenes": [{"text": x["text"], "voice": x["voice"]} for x in old]}, ensure_ascii=False)
    step(N_("Il modello riscrive l'idea in un'altra lingua"))
    with usage.tagged(db, tenant_id, "video_ideas", site_id=row.site_id, ref=f"video:{video_id}"):
        found, _calls = core.chat_json(RELANGUAGE.format(language=LANGUAGES[language]), user, check)
    keep = {k: getattr(row, k) for k in ("site_id", "draft_id", "topic_id", "seconds", "hype", "gain",
                                          "auto", "social")}
    if not row.scenes:
        keep["scene"] = row.scene
    with db.tenant(tenant_id) as s:
        vid = s.execute(m.videos.insert().values(tenant_id=tenant_id, language=language, status="idea", **keep,
                                                 **found).returning(m.videos.c.id)).scalar_one()
    return {"video_id": vid, "site_id": row.site_id, "language": language, "title": found["title"]}


RESCENE = (
    "You split the script of a short vertical video (9:16, {seconds} seconds) for the social networks into exactly "
    "{scenes} scene(s) of about {each} seconds; an AI video model makes each scene from its own prompt, a narrator "
    "voice speaks over them and a short text appears on screen. Keep the idea, the facts and the language of the "
    "texts ({language}). Never invite people to read, visit or click anything, never name the site in the voice "
    "or the texts. Each scene has:\n"
    "- shot: the prompt for the video model, in English, 40 to 90 words: vertical 9:16, one continuous shot, the "
    "camera movement, the subject, the place, the light, realistic documentary look, the sounds of the place; the "
    "scenes look like one film but show different things. Never ask for text, captions, letters, numbers, logos, "
    "brand marks, recognisable or famous real people, or children;\n"
    "- text: the words on screen, in {language}, 2 to 6 words, punchy;\n"
    "- voice: what the narrator says during the scene, in {language}, at most {words} words.\n"
    'Reply with JSON only: {{"scenes": [{{"shot": "...", "text": "...", "voice": "..."}}]}}')


def rescene(db, tenant_id: str, video_id: int, core, scenes: int) -> bool:
    """The idea's script in `scenes` scenes (Francesco, 2026-11-28: "non si
    possono scegliere le scene da fare?"), written again by the model when
    the person picks another number of scenes before making the video. The
    ideas of before 0.39.5 get their scenes this way. False when nothing had
    to change (or the clips are already asked)."""
    scenes = max(1, min(MAX_SCENES, int(scenes)))
    with db.tenant(tenant_id) as s:
        row = s.execute(select(m.videos).where(m.videos.c.id == video_id)).first()
    if row is None:
        raise LookupError(video_id)
    if row.parts or row.job_ref or row.status == "ready" or (row.scenes and len(row.scenes) == scenes):
        return False
    if core is None:
        raise CoreError("a model is needed to write the scenes")
    seconds = row.seconds or 10
    each = max(1, round(seconds / scenes))
    language = LANGUAGES.get((row.language or "it")[:2], row.language or "Italian")
    system = RESCENE.format(seconds=seconds, scenes=scenes, each=each, language=language,
                            words=max(4, int(each * WORDS_PER_SECOND)))
    user = json.dumps({"title": row.title, "hook": row.hook, "voiceover": row.voiceover,
                       "scenes": row.scenes or [{"shot": row.scene, "voice": row.voiceover}]}, ensure_ascii=False)

    def check(value):
        sc = _scenes(value.get("scenes") if isinstance(value, dict) else value, scenes)
        if len(sc) != scenes or not all(x["voice"] for x in sc):
            raise ValueError(f"exactly {scenes} scenes, each with its shot and its voice")
        if any(INVITE.search(y["voice"]) or INVITE.search(y["text"]) for y in sc):
            raise ValueError("the voice and the texts on screen never invite to read or visit anything")
        return sc

    step(N_("Il modello divide il video in {n} scene"), n=scenes)
    with usage.tagged(db, tenant_id, "video_ideas", site_id=row.site_id, ref=f"video:{video_id}"):
        found, _calls = core.chat_json(system, user, check)
    with db.tenant(tenant_id) as s:
        s.execute(update(m.videos).where(m.videos.c.id == video_id).values(scenes=found, **joined(found)))
    return True


def scenes_of_row(row) -> list[dict]:
    """The scenes of an idea; one scene for the ideas written before 0.39.5."""
    sc = [x for x in (getattr(row, "scenes", None) or []) if isinstance(x, dict) and x.get("shot")]
    return sc or [{"shot": row.scene or "", "text": "", "voice": row.voiceover or ""}]


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


def _lang_code(x) -> str | None:
    x = x.get("value") if isinstance(x, dict) else x
    x = str(x or "").strip().lower()[:2]
    return x if x in LANGUAGES else None


def language_of(body: dict | None, fallback: str | None = None) -> str:
    """The language of the site's or channel's videos (Video social › Idee
    ogni mattina; Francesco, 2026-11-27: "deve scegliere anche la lingua"),
    else the article's, else the profile's, else Italian."""
    body = body or {}
    return (_lang_code((body.get("settings") or {}).get("videos_language")) or _lang_code(fallback)
            or _lang_code(body.get("language")) or "it")


def scenes_of(body: dict | None, seconds: int = 10) -> int:
    """How many scenes the site's or channel's videos have (Video social ›
    Scene per video), else one every 10 seconds."""
    n = ((body or {}).get("settings") or {}).get("videos_scenes")
    if isinstance(n, int) and not isinstance(n, bool):
        return max(1, min(MAX_SCENES, n))
    return max(1, min(MAX_SCENES, math.ceil((seconds or 10) / 10)))


def seconds_of(body: dict | None, default: int = 10) -> int:
    """The length of the site's or channel's videos (Video social › Idee ogni
    mattina), else the server's (Impostazioni › Video social)."""
    n = ((body or {}).get("settings") or {}).get("videos_seconds")
    return max(4, min(MAX_SECONDS, n)) if isinstance(n, int) and not isinstance(n, bool) else default


def site_seconds(db, tenant_id: str, site_id: int | None, default: int = 10) -> int:
    if site_id is None:
        return default
    with db.tenant(tenant_id) as s:
        return seconds_of(_profile(s, site_id), default)


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


UNIT_SECONDS = 10  # the credit price of "Video per i social" is for every 10 seconds


def units(seconds: int | None, maker: OpenRouterVideo | None = None, scenes: int = 1) -> float:
    """How many times the price of a 10-second video: what the clips cost
    against 10 seconds of the same model (so a clip over 15 seconds of a
    model dearer there costs more, three clips of 10 seconds do not), or the
    10-second blocks when the model's price is not known."""
    seconds = seconds or UNIT_SECONDS
    scenes = max(1, scenes or 1)
    if maker is not None:
        try:
            each = maker.scene_seconds(seconds, scenes) if scenes > 1 else seconds
            one, base = maker.cost(each), maker.cost(UNIT_SECONDS)
        except Exception:  # noqa: BLE001 - the catalog not reachable: the blocks
            one = base = None
        if one and base:
            return max(1.0, scenes * one / base)
    return float(max(1, math.ceil(seconds / UNIT_SECONDS)))


def price(s, seconds: int | None = None, maker: OpenRouterVideo | None = None, scenes: int = 1) -> int:
    """What the next video costs the newsroom in credits: 0 while the month's
    videos of the plan last, or when the newsroom does not use credits;
    otherwise the price of "Video per i social" for every 10 seconds
    (Francesco, 2026-11-27: "almeno 20/30 sec per dare un messaggio utile")."""
    a = allowance(s)
    if a["included"] is None or a["left"]:
        return 0
    tid = s.info["tenant_id"]
    if s.execute(select(m.tenants.c.credits_month).where(m.tenants.c.tenant_id == tid)).scalar() is None:
        raise credits.NoCredits("no videos left in the plan and no credits")
    return round(credits.price(s, "video") * units(seconds, maker, scenes))


def price_or_none(s, seconds: int | None = None, maker: OpenRouterVideo | None = None,
                  scenes: int = 1) -> int | None:
    """The price, or None when the plan's videos are used up and the newsroom has no credits."""
    try:
        return price(s, seconds, maker, scenes)
    except credits.NoCredits:
        return None


def plans(maker: OpenRouterVideo, can_rewrite: bool = True) -> list[tuple[int, int]]:
    """The (seconds, scenes) a person can pick before making a video: each
    scene from 5 to 15 seconds (the model's cheaper tier), or one clip of any
    length; only one clip when the server cannot edit scenes (no ffmpeg)."""
    out: list[tuple[int, int]] = []
    most = MAX_SCENES if montage.available() and can_rewrite else 1
    for n in range(1, most + 1):
        for total in CHOICES:
            each = maker.scene_seconds(total, n)
            if n > 1 and not 5 <= each <= LONG_SECONDS:
                continue
            x = (each * n, n)
            if x[0] <= MAX_SECONDS and x not in out:
                out.append(x)
    return sorted(out)


def ask(db, tenant_id: str, video_id: int, by: str, seconds: int | None = None,
        maker: OpenRouterVideo | None = None, scenes: int | None = None) -> None:
    """A person wants the video: check the credits and mark it queued."""
    with db.tenant(tenant_id) as s:
        row = s.execute(select(m.videos.c.status, m.videos.c.scene, m.videos.c.voiceover, m.videos.c.scenes)
                        .where(m.videos.c.id == video_id)).first()
        if row is None:
            raise LookupError(video_id)
        credits.need(s, price(s, seconds, maker, scenes or scene_count(row, maker)))
        if row.status in ("idea", "failed"):
            s.execute(update(m.videos).where(m.videos.c.id == video_id).values(
                status="queued", error=None, made_by=by[:200] if by else None,
                **({"seconds": int(seconds)} if seconds else {})))


def scene_count(row, maker: OpenRouterVideo | None = None) -> int:
    """How many clips the video of an idea is made of: its scenes when the
    server can edit them (ffmpeg), else one clip."""
    if not getattr(row, "scenes", None) or not montage.available():
        return 1
    return len(scenes_of_row(row))


def clip_prompt(shot: str) -> str:
    return (shot.strip() + SILENT + " Vertical 9:16 video. No on-screen text, no subtitles, no logos, "
            "no watermarks.")[:2500]


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
        n = scene_count(row, maker)
        if not row.job_ref and not row.parts:
            credits.need(s, price(s, row.seconds or maker.seconds, maker, n))  # nothing asked yet: checked again
    if row.parts or (row.scenes and not row.job_ref and montage.available()):
        return _make_scenes(db, tenant_id, row, maker)
    started = time.monotonic()
    ref = row.job_ref
    secs = row.seconds or maker.seconds
    job = None
    with usage.tagged(db, tenant_id, "video", site_id=row.site_id,
                      ref=f"draft:{row.draft_id}" if row.draft_id else f"topic:{row.topic_id}"):
        try:
            if not ref:
                body = maker.request(prompt_of(row), row.seconds or maker.seconds)
                step(N_("Il modello video gira la clip"))
                ref = maker.submit(body)
                secs = body["duration"]
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
            return _failed(db, tenant_id, row, maker, started, e)
        cost = _cost(job)
        usage.record(maker.model, None, cost, int((time.monotonic() - started) * 1000))
    return _ready(db, tenant_id, row, maker, content, cost, units(secs, maker))


def _cost(job: dict | None) -> float | None:
    cost = ((job or {}).get("usage") or {}).get("cost")
    return float(cost) if isinstance(cost, (int, float)) else None


def _failed(db, tenant_id: str, row, maker: OpenRouterVideo, started: float, e: Exception) -> dict:
    usage.record(maker.model, None, None, int((time.monotonic() - started) * 1000), str(e))
    with db.tenant(tenant_id) as s:  # not retried; asked again, it is a new job
        s.execute(update(m.videos).where(m.videos.c.id == row.id).values(
            status="failed", error=str(e)[:500], job_ref=None, parts=None))
    return {"video_id": row.id, "site_id": row.site_id, "title": row.title, "failed": str(e)[:300]}


def _ready(db, tenant_id: str, row, maker: OpenRouterVideo, content: bytes, cost: float | None,
           unit: float, note: str | None = None) -> dict:
    """The video is made: keep it and pay it (the plan's videos, else credits)."""
    v = m.videos.c
    with db.tenant(tenant_id) as s:
        credits.lock(s, f"editor-quota:{tenant_id}")
        a = allowance(s)
        included, paid = a["included"] is not None and a["left"] > 0, 0
        if a["included"] is not None and not included:
            try:
                paid = credits.spend(s, "video", f"video:{row.id}",
                                     credits=round(credits.price(s, "video") * unit))
            except credits.NoCredits:  # credits used elsewhere while it was made: it is already paid for
                log.warning("video %s made without credits left", row.id)
        s.execute(update(m.videos).where(v.id == row.id).values(
            status="ready", content=content, mime=mime_of(content), size_bytes=len(content), cost=cost,
            credits=paid, included=included, made_at=datetime.now(UTC), error=note))
    forget_old(db, tenant_id)
    return {"video_id": row.id, "site_id": row.site_id, "title": row.title, "ready": True}


def _make_scenes(db, tenant_id: str, row, maker: OpenRouterVideo) -> dict:
    """One clip per scene (asked all at once, their job ids saved at once),
    the narrator's voice of each scene, the texts on screen, and the scenes
    joined by ffmpeg (montage.py)."""
    v = m.videos.c
    scenes = scenes_of_row(row)
    total = row.seconds or maker.seconds
    parts = [dict(x) for x in (row.parts or [])]
    each = parts[0]["seconds"] if parts else maker.scene_seconds(total, len(scenes))
    started = time.monotonic()
    cost: float | None = None
    with usage.tagged(db, tenant_id, "video", site_id=row.site_id,
                      ref=f"draft:{row.draft_id}" if row.draft_id else f"topic:{row.topic_id}"):
        try:
            for i in range(len(parts), len(scenes)):
                body = maker.request(clip_prompt(scenes[i]["shot"]), each)
                step(N_("Il modello video gira la scena {n} di {total}"), n=i + 1, total=len(scenes))
                ref = maker.submit(body)
                each = body["duration"]
                parts.append({"ref": ref, "seconds": each})
                with db.tenant(tenant_id) as s:  # saved at once: a restart waits for it, never pays it again
                    s.execute(update(m.videos).where(v.id == row.id).values(
                        status="making", parts=parts, model=maker.model, seconds=each * len(scenes),
                        resolution=body["resolution"]))
            made = []
            for i, p in enumerate(parts):
                step(N_("Il modello video gira la scena {n} di {total}"), n=i + 1, total=len(scenes))
                job = maker.wait(p["ref"])
                made.append(maker.download(p["ref"], job))
                c = _cost(job)
                if c is not None:
                    cost = (cost or 0) + c
            usage.record(maker.model, None, cost, int((time.monotonic() - started) * 1000))
        except VideoError as e:
            return _failed(db, tenant_id, row, maker, started, e)
    note, voices, spoken = None, [], None
    step(N_("La voce legge il testo delle scene"))
    with usage.tagged(db, tenant_id, "video_voice", site_id=row.site_id, ref=f"video:{row.id}"):
        t0 = time.monotonic()
        for sc in scenes:
            if not sc.get("voice"):
                voices.append(None)
                continue
            try:
                audio, c = maker.speech(sc["voice"])
            except VideoError as e:  # the clips are paid: the video comes out with their sound only
                log.warning("voice of video %s not made: %s", row.id, e)
                note = f"voice: {e}"[:500]
                voices = [None] * len(scenes)
                break
            voices.append(audio)
            if c is not None:
                spoken = (spoken or 0) + c
        if any(voices):
            usage.record(maker.voice_model, None, spoken, int((time.monotonic() - t0) * 1000))
    step(N_("Monto le scene con le scritte"))
    content = montage.assemble([{"clip": clip, "voice": voice, "text": sc.get("text") or ""}
                                for clip, voice, sc in zip(made, voices, scenes, strict=True)], each)
    if spoken is not None:
        cost = (cost or 0) + spoken
    return _ready(db, tenant_id, row, maker, content, cost, units(each * len(scenes), maker, len(scenes)), note)


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
            v.made_at, v.included, v.hype, v.gain, v.auto, v.social, v.scenes,
            (v.content.is_not(None)).label("has_file"))


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
