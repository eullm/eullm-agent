"""Articles the newsroom asks for in its own words.

Francesco, 2026-11-05: "c'è un form dove posso richiedere un articolo con un
mio prompt personale?". The request says what to write (subject, angle,
readers) and may bring links. The links are read and kept as items; the
archive is searched for news on the subject the site follows; the article is
written from them with the usual checks, every sentence citing an item. With
nothing to cite the newsroom says so, and writes nothing.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from . import drafts, models as m, repo
from .html_extract import parse_html
from .i18n import N_
from .progress import step
from .site import RobotsCache
from .text import item_terms, tokens

log = logging.getLogger(__name__)

FORMAT = "request"  # proposals.format of an article asked for
REQUESTS = "https://requests.redazia.invalid/"  # the newsroom's source row for the links it gives, never collected
TOPIC = "Richieste della redazione"
MAX_LINKS = 5
MAX_TEXT = 2000  # characters of a request
FOUND = 6  # archive news cited, the closest first
DAYS = 14  # how far back the archive is searched
MIN_WORDS = 60  # a page with fewer is a landing page, a paywall or a login: not read

# Words of the request that say it is a request, not what it is about.
ASKING = set(tokens(
    "scrivi scrivere scrivimi vorrei voglio articolo articoli pezzo notizia notizie approfondimento parla parlare "
    "spiega spiegare racconta raccontare tema argomento lettori pubblico tono taglio breve lungo "
    "write article piece story news about explain readers audience tone angle short long please "
    "schreibe artikel über leser erkläre écris article sur lecteurs explique escribe artículo sobre lectores explica"))

_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def links_of(text: str | None) -> list[str]:
    """The http(s) addresses in the text, in order, at most MAX_LINKS."""
    return list(dict.fromkeys(u.rstrip(".,;:)]}") for u in _URL.findall(text or "")))[:MAX_LINKS]


def terms_of(request: str) -> set[str]:
    """What the request is about: its content words, without the words of
    asking and without its links."""
    return set(tokens(_URL.sub(" ", request))) - ASKING


def _source(s, tenant_id: str) -> int:
    """The newsroom's source row for the links of its requests: rejected, so
    it is never collected nor followed, and with no site, so no list shows it."""
    src = m.sources.c
    found = s.execute(select(src.id).where(src.url == REQUESTS, repo.own())).scalar()
    if found is not None:
        return found
    return s.execute(insert(m.sources).values(
        tenant_id=tenant_id, kind="rss", name="Link della redazione", url=REQUESTS, config={}, weight=1.0,
        site_id=None, status="rejected", origin="manual", evidence={}, shared=False,
    ).returning(m.sources.c.id)).scalar_one()


def _topic(s, tenant_id: str) -> int:
    """The topic the requests hang on: one per newsroom, with an old date, so
    no clustering and no list picks it up."""
    tp = m.topics.c
    found = s.execute(select(tp.id).where(tp.label == TOPIC, tp.labelled_by == FORMAT)).scalar()
    if found is not None:
        return found
    old = datetime(2000, 1, 1, tzinfo=UTC)
    return s.execute(insert(m.topics).values(tenant_id=tenant_id, label=TOPIC, keywords=[], labelled_by=FORMAT,
                                             created_at=old, updated_at=old).returning(tp.id)).scalar_one()


def read_links(db, tenant_id: str, links: list[str], client: httpx.Client | None) -> tuple[list[int], list[str]]:
    """Each link as an item the article can cite: one already read is used
    again, the others are read now. Returns the items and the links that
    could not be read."""
    from .normalize import canonical_url, content_hash, sha256_hex

    ids, unread = [], []
    robots = RobotsCache(client) if client is not None else None
    for url in links:
        key = sha256_hex(canonical_url(url))
        with db.tenant(tenant_id) as s:
            known = s.execute(select(m.source_items.c.id).where(m.source_items.c.url_hash == key)
                              .order_by(m.source_items.c.id.desc()).limit(1)).scalar()
        if known is not None:
            ids.append(known)
            continue
        page = None
        if client is not None:
            try:
                if robots.allowed(url):
                    r = client.get(drafts.reading_url(url), headers={"Accept": "text/html"})
                    if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
                        page = parse_html(r.text, url)
            except httpx.HTTPError as e:
                log.info("cannot read %s: %s", url, e)
        text = (page.text if page else "")[:drafts.MAX_CONTENT]
        if len(text.split()) < MIN_WORDS:
            unread.append(url)
            continue
        title = " ".join((page.title or page.h1 or url).split())[:300]
        with db.tenant(tenant_id) as s:
            found = s.execute(insert(m.source_items).values(
                tenant_id=tenant_id, source_id=_source(s, tenant_id), url=url, canonical_url=canonical_url(url),
                url_hash=key, content_hash=content_hash(title, text[:500]), title=title,
                summary=" ".join(text.split()[:60]), metrics={}, minhash=[], content=text,
                content_fetched_at=datetime.now(UTC),
            ).on_conflict_do_nothing().returning(m.source_items.c.id)).scalar()
        if found is None:
            unread.append(url)
        else:
            ids.append(found)
    return ids, unread


KEYWORDS = (
    "You turn an editor's request for an article into search words for a news archive. Reply with JSON only: "
    '{"terms": ["..."]}: 4 to 12 single words or names that the news on this subject would have in its title, '
    "in the site's language and in English (the archive has news in both): the names of companies, people, "
    "products, laws and places, and the nouns of the subject. No words of asking (write, article, readers).")


def search_terms(db, tenant_id: str, site_id: int, request: str, language: str | None, core) -> set[str]:
    """The words to look for: the request's own, and those the model gives
    in the site's language and in English (an Italian request finds the
    English news on the same subject). Without the model, the request's."""
    from .core_client import extract_json
    from . import usage

    terms = terms_of(request)
    if core is None:
        return terms
    try:
        with usage.tagged(db, tenant_id, "draft", site_id=site_id, ref="request"):
            res = core.chat([{"role": "system", "content": KEYWORDS},
                             {"role": "user", "content": f"Site language: {language or 'it'}\nRequest: {request}"}])
        given = extract_json(res.content).get("terms") or []
    except Exception:  # the request's own words are enough to search
        log.info("no search words from the model for a request", exc_info=True)
        return terms
    return terms | (set(tokens(" ".join(str(x) for x in given[:20]))) - ASKING)


def search(s, site_id: int, terms: set[str], skip: list[int], limit: int = FOUND, now: datetime | None = None) -> list[int]:
    """The news the site follows on the subject of the request, the closest
    first: at least two of the words (one, when there is one), a word of the
    title counting twice."""
    if not terms:
        return []
    need = min(2, len(terms))
    si = m.source_items
    rows = s.execute(select(si.c.id, si.c.title, si.c.summary).where(
        repo.followed_by(site_id), si.c.duplicate_of.is_(None),
        si.c.fetched_at > (now or datetime.now(UTC)) - timedelta(days=DAYS),
        si.c.id.not_in(skip or [0])).order_by(si.c.id.desc()).limit(5000)).all()
    scored = []
    for r in rows:
        title = terms & set(tokens(r.title))
        shared = terms & item_terms(r.title, r.summary or "")
        if len(shared) >= need:
            scored.append((len(shared) + len(title), r.id))
    return [i for _, i in sorted(scored, reverse=True)[:limit]]


def ask(db, tenant_id: str, site_id: int, request: str, links: list[str], by: str, core,
        client: httpx.Client | None) -> dict:
    """Write the article asked for. Returns the draft, or why there is none:
    `no_profile`, or `none` when nothing could be cited."""
    request = " ".join(request.split())[:MAX_TEXT]
    links = list(dict.fromkeys(links + links_of(request)))[:MAX_LINKS]
    with db.tenant(tenant_id) as s:
        row = s.execute(select(m.editorial_profiles.c.id, m.editorial_profiles.c.body).where(
            m.editorial_profiles.c.site_id == site_id, m.editorial_profiles.c.status == "approved")).first()
    if row is None:
        return {"no_profile": True, "site_id": site_id}
    prof, language = row.id, (row.body or {}).get("language")
    if links:
        step(N_("Lettura dei {n} link della richiesta"), n=len(links))
    given, unread = read_links(db, tenant_id, links, client)
    step(N_("Ricerca nell'archivio delle notizie sull'argomento"))
    terms = search_terms(db, tenant_id, site_id, request, language, core)
    with db.tenant(tenant_id) as s:
        found = search(s, site_id, terms, given)
        items = list(dict.fromkeys(given + found))
        if not items:
            return {"none": True, "unread": unread, "site_id": site_id}
        topic = _topic(s, tenant_id)
        if given:  # read only for this request: kept out of the clustering
            s.execute(insert(m.topic_items).values(
                [{"tenant_id": tenant_id, "topic_id": topic, "item_id": i} for i in given]).on_conflict_do_nothing())
        title = request if len(request) <= 200 else request[:199].rsplit(" ", 1)[0] + "…"
        pid = s.execute(insert(m.proposals).values(
            tenant_id=tenant_id, site_id=site_id, topic_id=topic, profile_id=prof, opportunity=100.0,
            title=title, angle=request, why_now="", format=FORMAT, generated_by="person", status="accepted",
            decided_by=by, basis={"request": request, "links": links, "unread": unread},
        ).returning(m.proposals.c.id)).scalar_one()
        s.execute(insert(m.proposal_citations).values(
            [{"tenant_id": tenant_id, "proposal_id": pid, "item_id": i} for i in items]))
    did = drafts.write_draft(db, tenant_id, pid, core, client)
    with db.tenant(tenant_id) as s:
        d = s.execute(select(m.drafts.c.title, m.drafts.c.status).where(m.drafts.c.id == did)).one()
    return {"draft_id": did, "title": d.title, "status": d.status, "site_id": site_id, "proposal_id": pid,
            "links": len(given), "found": len(found), "unread": unread}

