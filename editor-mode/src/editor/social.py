"""What the social networks say about the topics of the moment (Francesco,
2026-11-25: "deve fare anche ricerche sui social direttamente").

At every trend snapshot the archive asks two networks that open their data
to everyone, with no scraping:

- Bluesky: the network's trends (public API, no key): what people are
  posting about right now, with the number of posts. A model says which
  trend is the same story as which topic, in any language.
- YouTube: for the topics at the top, the Shorts published in the last 48
  hours on the topic, ordered by views (YouTube Data API, a free Google
  key; a search costs 100 of the 10,000 daily units, so at most YT_TOPICS
  topics per snapshot).

TikTok and Instagram give no access to their trends (TikTok's Research API
is for universities only, its Creative Center may not be harvested; Meta
has no trends API), so they are not read.

Each topic of the snapshot gets `social` with what was found and `heat`
(0-1). The morning round of the videos puts first the topics that heat up
on the social networks before the news are everywhere. A network that does
not answer is skipped: the snapshot is never lost for it."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

import httpx

from .core_client import CoreError
from .i18n import N_
from .progress import step

log = logging.getLogger(__name__)

BLUESKY = "https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrends"
YOUTUBE = "https://www.googleapis.com/youtube/v3"
TRENDS = 25
YT_TOPICS = 8  # 8 snapshots a day x 8 searches x 100 units = 6,400 of the 10,000 free units
YT_WINDOW = timedelta(hours=48)
YT_RESULTS = 10


def _num(x) -> int:
    try:
        return max(0, int(x))
    except (TypeError, ValueError):
        return 0


def bluesky_trends(client: httpx.Client, limit: int = TRENDS) -> list[dict]:
    """Bluesky's trends of the moment: label, description, posts, since."""
    r = client.get(BLUESKY, params={"limit": limit}, timeout=20)
    r.raise_for_status()
    out = []
    for x in (r.json() or {}).get("trends") or []:
        label = str(x.get("displayName") or x.get("topic") or "").strip()
        if not label:
            continue
        link = str(x.get("link") or "")
        out.append({"label": label[:120], "description": str(x.get("description") or "")[:300],
                    "posts": _num(x.get("postCount")), "since": x.get("startedAt"),
                    "category": x.get("category"),
                    "url": f"https://bsky.app{link}" if link.startswith("/") else None})
    return out


MATCH = (
    "You match the topics of a news archive with the trends of a social network. A trend matches a topic "
    "only when it is the same story or event, not just the same broad subject. Topics and trends can be in "
    "different languages. Reply with JSON only: {\"matches\": [{\"topic\": <topic number>, \"trend\": <trend "
    "number>}]}; an empty list when nothing matches.")


def _match_validator(topics: int, trends: int):
    def check(value):
        items = value.get("matches") if isinstance(value, dict) else value
        if not isinstance(items, list):
            raise ValueError("matches must be a list")
        out = []
        for x in items:
            if not isinstance(x, dict):
                raise ValueError("each match is an object")
            t, b = x.get("topic"), x.get("trend")
            if isinstance(t, int) and isinstance(b, int) and 1 <= t <= topics and 1 <= b <= trends:
                out.append((t - 1, b - 1))
        return out
    return check


def match(core, topics: list[dict], trends: list[dict]) -> list[tuple[int, int]]:
    """(topic index, trend index) of the same story, as the model sees it."""
    if not topics or not trends:
        return []
    user = ("Topics:\n" + "\n".join(f"{i}. {t['label']}: {(t.get('summary') or '')[:160]}"
                                    for i, t in enumerate(topics, 1))
            + "\n\nTrends:\n" + "\n".join(f"{i}. {b['label']}: {b['description'][:160]}"
                                          for i, b in enumerate(trends, 1)))
    found, _calls = core.chat_json(MATCH, user, _match_validator(len(topics), len(trends)))
    return found


def youtube(client: httpx.Client, key: str, query: str, now: datetime) -> dict:
    """The Shorts on `query` of the last 48 hours: how many, their views,
    views per hour and the most seen."""
    after = (now - YT_WINDOW).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = client.get(f"{YOUTUBE}/search", timeout=20, params={
        "key": key, "part": "id", "q": query[:200], "type": "video", "videoDuration": "short",
        "publishedAfter": after, "order": "viewCount", "maxResults": YT_RESULTS})
    r.raise_for_status()
    ids = [x["id"]["videoId"] for x in (r.json() or {}).get("items") or []
           if isinstance(x.get("id"), dict) and x["id"].get("videoId")]
    if not ids:
        return {"videos": 0, "views": 0, "per_hour": 0}
    r = client.get(f"{YOUTUBE}/videos", timeout=20, params={
        "key": key, "part": "statistics,snippet", "id": ",".join(ids)})
    r.raise_for_status()
    views, per_hour, top = 0, 0.0, None
    for x in (r.json() or {}).get("items") or []:
        n = _num((x.get("statistics") or {}).get("viewCount"))
        sn = x.get("snippet") or {}
        try:
            at = datetime.fromisoformat(str(sn.get("publishedAt")).replace("Z", "+00:00"))
            hours = max(1.0, (now - at).total_seconds() / 3600)
        except ValueError:
            hours = YT_WINDOW.total_seconds() / 3600
        views += n
        per_hour += n / hours
        if top is None or n > top["views"]:
            top = {"title": str(sn.get("title") or "")[:160], "views": n,
                   "url": f"https://www.youtube.com/shorts/{x.get('id')}"}
    return {"videos": len(ids), "views": views, "per_hour": round(per_hour), "top": top}


def heat(social: dict | None) -> float:
    """0-1: 10,000 Bluesky posts, or 10,000 views an hour of new Shorts, are the top."""
    if not social:
        return 0.0
    posts = sum(_num(b.get("posts")) for b in social.get("bluesky") or [])
    yt = _num((social.get("youtube") or {}).get("per_hour"))
    return round(max(min(1.0, math.log10(posts + 1) / 4), min(1.0, math.log10(yt + 1) / 4)), 3)


def enrich(db, archive: str, topics: list[dict], client: httpx.Client, core=None, youtube_key: str = "",
           now: datetime | None = None) -> dict:
    """Add `social` to the snapshot's topics (in place). Returns what was read."""
    from . import usage

    now = now or datetime.now(UTC)
    found: dict[int, dict] = {}
    read = {"bluesky": None, "youtube": 0}
    if core is not None:
        try:
            trends = bluesky_trends(client)
            read["bluesky"] = len(trends)
            if trends:
                step(N_("Cerco i temi del momento su Bluesky"))
                with usage.tagged(db, archive, "social_match", ref="trend"):
                    pairs = match(core, topics, trends)
                for ti, bi in pairs:
                    b = trends[bi]
                    found.setdefault(ti, {}).setdefault("bluesky", []).append(
                        {k: b[k] for k in ("label", "posts", "since", "url")})
        except (httpx.HTTPError, ValueError, CoreError) as e:
            log.warning("bluesky trends skipped: %s", e)
    if youtube_key:
        step(N_("Cerco gli Short dei temi su YouTube"))
        for i, t in enumerate(topics[:YT_TOPICS]):
            try:
                yt = youtube(client, youtube_key, t["label"], now)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("youtube search skipped: %s", e)
                break  # quota or key: the next topics would fail the same way
            read["youtube"] += 1
            if yt["videos"]:
                found.setdefault(i, {})["youtube"] = yt
    for i, social in found.items():
        social["heat"] = heat(social)
        topics[i]["social"] = social
    return read
