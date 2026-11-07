"""The primary source of a central fact, looked for when the sources do not
have it. An outlet reporting a press release, a report or an announcement
is not the document: an article whose central figure or decision comes
from one is written from the document when it can be found, not from the
outlet's retelling (external review, 2026-11-04: the GIMBE release, the
OpenAI announcement).

Cheap and ordered: first the links of the news themselves, then the home
page of the body that published it; every candidate is read once (a page
already known is reused) and kept only if a check confirms that it comes
from that body, is about that subject, is of the right date and states the
fact. No search engine is used: the Core lets the newsroom read pages, and
a model-suggested page is trusted only after the check. The work has a
daily cap, and what was found (or not) is remembered on the proposal, so a
rewrite does not look again."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select, update

from . import models as m
from .core_client import CoreClient, CoreError
from .html_extract import parse_html
from .i18n import N_
from .progress import step
from .site import RobotsCache, tdm_marked
from .text import tokens

log = logging.getLogger(__name__)

PURPOSE = "primary_source"
PER_DAY = 40  # model calls a day for the newsroom (two for each article looked at)
MAX_PAGES = 5  # pages read for one article, the news' own included
MAX_CANDIDATES = 4  # candidates the check reads
EXCERPT = 1800  # characters of a candidate the check reads
SOCIAL = ("twitter.com", "x.com", "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "youtu.be",
          "t.co", "bit.ly", "tiktok.com", "reddit.com", "wikipedia.org")

GAP_SYSTEM = (
    "You are the research desk of a newsroom. Decide whether the article about to be written is missing its "
    "primary source. Reply true only when the article's central fact (a figure, a decision, an announcement, a "
    "study's result) comes from a document or statement of an identifiable body (a statistics office, a "
    "ministry, a company, an agency, a court, a foundation, a research group) that none of the items given is: "
    "the items are outlets retelling it. Reply false when the items themselves witnessed the event, or when "
    "an original document is already among them. Reply with JSON only: "
    '{"missing": true|false, "datum": "<the central fact, one sentence>", "entity": "<the body that published '
    'it>", "domains": ["<website domain of that body, only if you are sure, at most 2>"], '
    '"query": "<five to eight words the primary page would contain>"}'
)
CHECK_SYSTEM = (
    "You check candidate pages as the primary source of a fact. A candidate is accepted only if all four hold: "
    "(entity) it is published by the body that made the statement, (topic) it is about the same subject, "
    "(date) its date is consistent with the news (not older than a few weeks before it, not after it, or "
    "undated but clearly the same release) and (supports) its text states the fact or the figure itself. "
    "Reply with JSON only: "
    '{"results": [{"n": <number of the candidate>, "entity": true|false, "topic": true|false, '
    '"date": true|false, "supports": true|false}]}'
)


def _gap(v) -> dict:
    if not isinstance(v, dict) or not isinstance(v.get("missing"), bool):
        raise ValueError("missing must be true or false")
    domains = [d.strip().lower().removeprefix("www.") for d in (v.get("domains") or []) if isinstance(d, str)]
    domains = [d for d in domains if re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,60}\.[a-z]{2,24}", d)][:2]
    return {"missing": v["missing"], "datum": str(v.get("datum") or "")[:300], "entity": str(v.get("entity") or "")[:120],
            "domains": domains, "query": str(v.get("query") or "")[:120]}


def _verdicts(v) -> dict[int, bool]:
    out = {}
    for r in v.get("results", []):
        out[int(r["n"])] = all(r.get(k) is True for k in ("entity", "topic", "date", "supports"))
    return out


def _host(url: str) -> str:
    try:
        return (httpx.URL(url).host or "").lower().removeprefix("www.")
    except (httpx.InvalidURL, TypeError):
        return ""


def _calls_today(s, now: datetime) -> int:
    return s.execute(select(func.count()).select_from(m.llm_usage).where(
        m.llm_usage.c.purpose == PURPOSE, m.llm_usage.c.at >= now - timedelta(days=1))).scalar() or 0


def rank(links: list, words: set[str], domains: list[str], own: set[str]) -> list[str]:
    """Links worth reading, best first: those whose text or address share
    words with the fact, on the body's own site first. A link to another
    site needs two shared words, a link on the body's site one."""
    scored: dict[str, float] = {}
    for link in links:
        host = _host(link.url)
        if link.in_nav and not any(host.endswith(d) for d in domains):
            continue
        if not host or not link.url.startswith("https://") or host in own or any(host.endswith(x) for x in SOCIAL):
            continue
        on_entity = any(host == d or host.endswith("." + d) for d in domains)
        shared = len(words & set(tokens(f"{link.text} {httpx.URL(link.url).path.replace('-', ' ').replace('/', ' ')}")))
        if shared >= (1 if on_entity else 2):
            scored[link.url] = max(scored.get(link.url, 0), shared + (1 if on_entity else 0))
    return sorted(scored, key=lambda u: -scored[u])


def look(db, tenant_id: str, prop, main: list, existing: bool, core: CoreClient,
         client: httpx.Client | None, now: datetime | None = None) -> list[int]:
    """The primary documents found for a proposal, as ids of items the
    article can cite (empty when none, or when nothing is missing). `main`
    holds the main news as Source objects; `existing` says an original
    document was already read."""
    from .drafts import store_document
    from .normalize import canonical_url, sha256_hex

    now = now or datetime.now(UTC)
    remembered = (prop.basis or {}).get("primary") if isinstance(prop.basis, dict) else None
    if client is None or existing or not main:
        return []
    if remembered is not None:  # a rewrite does not look again: it reuses what was found
        out = []
        for url in remembered.get("found", []):
            with db.tenant(tenant_id) as s:
                known = s.execute(select(m.source_items.c.id).where(
                    m.source_items.c.url_hash == sha256_hex(canonical_url(url)))).scalar()
            if known is not None:
                out.append(known)
        return out
    with db.tenant(tenant_id) as s:
        if _calls_today(s, now) + 2 > PER_DAY:
            log.info("primary source search: the daily cap is reached")
            return []

    def remember(found: list[str], why: str) -> None:
        with db.tenant(tenant_id) as s:
            basis = dict(s.execute(select(m.proposals.c.basis).where(m.proposals.c.id == prop.id)).scalar() or {})
            basis["primary"] = {"found": found, "why": why, "at": now.isoformat()}
            s.execute(update(m.proposals).where(m.proposals.c.id == prop.id).values(basis=basis))

    from .usage import tagged

    items = "\n".join(f"[{x.id}] {x.title} ({x.name}, {x.published or 'undated'})\n{(x.summary + ' ' + x.content)[:1200]}"
                      for x in main[:3])
    try:
        with tagged(db, tenant_id, PURPOSE, site_id=prop.site_id, ref=f"proposal:{prop.id}"):
            gap, _ = core.chat_json(GAP_SYSTEM, f"Article: {prop.title}\nAngle: {prop.angle}\nItems:\n{items}", _gap)
    except (CoreError, ValueError) as e:
        log.info("primary source search skipped: %s", e)
        return []
    if not gap["missing"]:
        remember([], "nothing missing")
        return []
    step(N_("Ricerca della fonte primaria: {datum}"), datum=gap["datum"][:120])

    words = set(tokens(f"{gap['datum']} {gap['query']} {gap['entity']} {prop.title}"))
    own = {_host(x.url) for x in main}
    robots = RobotsCache(client)
    reads = 0
    pages: dict[str, tuple] = {}

    def read(url: str):
        nonlocal reads
        if url in pages:
            return pages[url]
        if reads >= MAX_PAGES:
            return None
        try:
            if not robots.allowed(url) or robots.reserved(url):
                return None
            reads += 1
            r = client.get(url, headers={"Accept": "text/html"})
        except httpx.HTTPError as e:
            log.info("cannot read %s: %s", url, e)
            return None
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html") or tdm_marked(r):
            return None
        pages[url] = (parse_html(r.text, url), r)
        return pages[url]

    # First the links of the news; then the home page of the body that
    # published the fact, when the model knows it.
    urls: list[str] = []
    for x in main[:2]:
        got = read(x.url)
        if got:
            urls += rank(got[0].links, words, gap["domains"], own)
    for d in gap["domains"]:
        got = read(f"https://{d}/")
        if got:
            urls += rank(got[0].links, words, gap["domains"], own)
    urls = [u for u in dict.fromkeys(urls) if u not in pages][:MAX_CANDIDATES]

    candidates = []
    for url in urls:
        with db.tenant(tenant_id) as s:
            known = s.execute(select(m.source_items.c.id, m.source_items.c.title, m.source_items.c.content).where(
                m.source_items.c.url_hash == sha256_hex(canonical_url(url)))).first()
        if known is not None and known.content:  # read before, for another article: reused
            candidates.append({"url": url, "title": known.title, "text": known.content, "page": None, "item": known.id})
            continue
        got = read(url)
        if got and len(got[0].text.split()) >= 80:
            page = got[0]
            candidates.append({"url": url, "title": page.title or page.h1 or url, "text": page.text, "page": page,
                               "item": None, "published": page.published})
    if not candidates:
        remember([], "no candidate")
        return []

    listing = "\n\n".join(
        f"Candidate {n}: {c['url']}\nTitle: {c['title']}\nDate: {c.get('published') or 'not stated'}\n{c['text'][:EXCERPT]}"
        for n, c in enumerate(candidates, 1))
    dates = ", ".join(sorted({x.published for x in main if x.published}))
    try:
        with tagged(db, tenant_id, PURPOSE, site_id=prop.site_id, ref=f"proposal:{prop.id}"):
            verdict, _ = core.chat_json(
                CHECK_SYSTEM,
                f"News: {prop.title}\nFact: {gap['datum']}\nBody: {gap['entity']}\nDate of the news: {dates or 'unknown'}\n\n{listing}",
                _verdicts)
    except (CoreError, ValueError, KeyError, TypeError) as e:
        log.info("primary source check failed: %s", e)
        remember([], "check failed")
        return []
    found, ids = [], []
    for n, c in enumerate(candidates, 1):
        if not verdict.get(n):
            continue
        item = c["item"] or store_document(db, tenant_id, main[0].id, c["url"], c["page"])
        if item is not None:
            ids.append(item)
            found.append(c["url"])
    remember(found, "found" if found else "no candidate confirmed")
    return ids
