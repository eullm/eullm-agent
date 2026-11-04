"""Article drafts where every claim is tied to stored sources.

The model writes the article as a list of claims, each with the ids of the
items that support it; there is no free text outside claims. Before a draft
is saved it is checked:

- every claim cites at least one item, and only items given to the model;
- every number in a claim appears in one of its cited items;
- no claim copies a long run of words from a source (originality).

A draft that still fails after a retry is saved as `needs_review` with the
problems listed next to the claims, never silently published.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from . import models as m
from . import credits, quotas, repo, seo, usage
from .text import humanize
from .core_client import CoreClient, CoreError
from .html_extract import parse_html
from .i18n import N_, t
from .progress import step
from .site import RobotsCache
from .normalize import normalize_text
from .text import item_terms, tokens

log = logging.getLogger(__name__)

MAX_CONTENT = 20_000
RELATED = 8  # other outlets' reports of the same event
# An article tells one event. Another item is about the same event when it
# shares at least half of the content words of the main news title (and at
# least three of them), and came at most EVENT_DAYS before it: two stories
# of the same sector or period share one or two words ("Pil Usa nel terzo
# trimestre" and "Aramco nel terzo trimestre"), not most of a title.
EVENT_SHARE = 0.5
EVENT_MIN = 3
EVENT_DAYS = 3
# Context: a few earlier items on the same subject (two shared words, the
# last 30 days), given apart from the event. The model uses one only for an
# explicit purpose (a precedent, a comparison, the rule that applies) and
# never as a second story; they do not count as sources of the event.
CONTEXT = 4
CONTEXT_DAYS = 30
TARGET_WORDS = 1000  # when the site's own length is unknown
# The longest an article may be: it is as long as its sources allow, never
# padded to reach a length (an external audit found padded articles
# repeating themselves and stretching thin sources).
MIN_WORDS, MAX_WORDS = 600, 2000


def target_words(profile: dict) -> int:
    return max(MIN_WORDS, min((profile.get("style") or {}).get("typical_words") or TARGET_WORDS, MAX_WORDS))
COPY_RUN = 12  # words in a row taken from a source count as copying
_NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?:%|‰)?")


@dataclass
class Source:
    id: int
    title: str
    url: str
    name: str
    summary: str
    content: str
    published: str | None
    fetched: str | None = None  # when it was read, UTC, to the minute
    live: bool = False  # the same page came with different titles: a page updated over time

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.summary}\n{self.content}"


@dataclass
class Claim:
    text: str
    sources: list[int]
    section: str | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class Article:
    title: str
    subtitle: str
    claims: list[Claim]
    faq: list[str] = field(default_factory=list)  # sections that are a reader's question and its answer
    seo: dict = field(default_factory=dict)  # the model's search fields, see seo.parse

    @property
    def flags(self) -> list[dict]:
        return [{"claim": i, "text": c.text[:200], "problems": c.flags} for i, c in enumerate(self.claims) if c.flags]


def numbers(text: str) -> set[str]:
    """Numbers as written, normalised so 1.200 / 1,200 / 1200 compare equal."""
    out = set()
    for n in _NUMBER.findall(text):
        digits = re.sub(r"[.,](?=\d{3}\b)", "", n.rstrip("%‰"))
        out.add(digits.replace(",", "."))
    return out


def copied_run(claim: str, source: str, run: int = COPY_RUN) -> bool:
    words = normalize_text(claim).split()
    if len(words) < run:
        return False
    src = " " + normalize_text(source) + " "
    return any(" " + " ".join(words[i : i + run]) + " " in src for i in range(len(words) - run + 1))


# The sentence checks' findings, stored in English (the models read them too).
NO_SOURCE = "no valid source"
NUMBERS_MISSING = "numbers not found in the cited sources: "
COPIED = "copies a passage of a source word for word"
ABOUT_SOURCES = "talks about its sources instead of the event"

# Sentences and headings about the article's own sources or checking
# ("what is confirmed", "the sources consulted do not say"): the AI
# editor-in-chief refused articles for them, after they were paid for
# (2026-11-04). Caught here, they are fixed or cut before that.
_ABOUT = (
    r"cosa (?:è|e') (?:confermato|chiaro|noto)|cosa non (?:è|e') (?:chiaro|confermato|noto)|"
    r"\b(?:le|dalle|nelle) fonti (?:consultate|disponibili|citate|a disposizione)|"
    r"\b(?:i|dai|nei) testi (?:disponibili|consultati)|(?:non )?(?:è|e') possibile (?:dire|stabilire|verificare) qui|"
    r"what is (?:confirmed|known|not (?:yet )?(?:clear|known|confirmed))|(?:the )?(?:available|consulted|cited) sources|"
    r"the sources (?:do not|don't) (?:say|indicate|specify)|"
    r"was (?:ist|bekannt ist) bestätigt|die (?:verfügbaren|vorliegenden) quellen|"
    r"ce qui est (?:confirmé|établi)|les sources (?:disponibles|consultées)|"
    r"lo que (?:está|se ha) confirmado|las fuentes (?:disponibles|consultadas)"
)
ABOUT = re.compile(_ABOUT, re.IGNORECASE)


def about_sources(text: str | None) -> bool:
    """Does this sentence or heading talk about the sources, not the event?"""
    return bool(text and ABOUT.search(text))


def check(article: Article, sources: dict[int, Source]) -> Article:
    for c in article.claims:
        c.flags = []
        cited = [sources[i] for i in c.sources if i in sources]
        if not cited:
            c.flags.append(NO_SOURCE)
            continue
        support = numbers(" ".join(s.text for s in cited))
        missing = sorted(n for n in numbers(c.text) if n not in support)
        if missing:
            c.flags.append(NUMBERS_MISSING + ", ".join(missing))
        if any(copied_run(c.text, s.text) for s in cited):
            c.flags.append(COPIED)
        if about_sources(c.text):
            c.flags.append(ABOUT_SOURCES)
    return article


def flag_text(flag, lang: str | None = None) -> str:
    """A sentence check's finding in the reader's language."""
    flag = str(flag)
    if flag == NO_SOURCE:
        return t("nessuna fonte valida", lang)
    if flag.startswith(NUMBERS_MISSING):
        return t("numeri che non si trovano nelle fonti citate: {numbers}", lang, numbers=flag[len(NUMBERS_MISSING):])
    if flag == COPIED:
        return t("copia parola per parola un passaggio di una fonte", lang)
    if flag == ABOUT_SOURCES:
        return t("parla delle fonti invece che della notizia", lang)
    return flag


def parse_article(value, allowed: set[int]) -> Article:
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    title = value.get("title")
    if not isinstance(title, str) or not 5 <= len(title.strip()) <= 160:
        raise ValueError("title must be 5 to 160 characters")
    sections = value.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("sections must be a non-empty list")
    claims = []
    for sec in sections:
        if not isinstance(sec, dict) or not isinstance(sec.get("claims"), list):
            raise ValueError("each section needs a claims list")
        heading = humanize(sec["heading"]) if isinstance(sec.get("heading"), str) else None
        for c in sec["claims"]:
            if not isinstance(c, dict) or not isinstance(c.get("text"), str) or not c["text"].strip():
                raise ValueError("each claim needs a text")
            try:
                ids = [int(x) for x in c.get("sources", [])]
            except (TypeError, ValueError):
                raise ValueError("sources must be item ids") from None
            if not ids:
                raise ValueError(f"claim without sources: {c['text'][:80]!r}")
            unknown = [i for i in ids if i not in allowed]
            if unknown:
                raise ValueError(f"claim cites ids not in the list: {unknown}")
            claims.append(Claim(humanize(c["text"].strip()), list(dict.fromkeys(ids)), heading))
    if len(claims) < 3:
        raise ValueError("an article needs at least 3 claims")
    # Questions readers ask, each answered with sourced claims like the rest.
    faq = []
    for q in value.get("faq") or []:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str) or not q["question"].strip():
            continue
        question = humanize(" ".join(q["question"].split()))[:200]
        answer = [c for c in q.get("claims") or [] if isinstance(c, dict) and isinstance(c.get("text"), str)
                  and c["text"].strip()]
        if not answer:
            continue
        for c in answer:
            try:
                ids = [int(x) for x in c.get("sources", [])]
            except (TypeError, ValueError):
                raise ValueError("sources must be item ids") from None
            if not ids or any(i not in allowed for i in ids):
                raise ValueError(f"FAQ answer without valid sources: {c['text'][:80]!r}")
            claims.append(Claim(humanize(c["text"].strip()), list(dict.fromkeys(ids)), question))
        faq.append(question)
    seo = value.get("seo") if isinstance(value.get("seo"), dict) else {}
    return Article(humanize(title.strip()), humanize(str(value.get("subtitle") or "").strip())[:300], claims, faq, seo)


READ_MORE = {"it": "Leggi anche", "de": "Weiterlesen", "fr": "À lire aussi", "es": "Lee también"}


def render(article: Article, sources: dict[int, Source], language: str | None, links: list[dict] | None = None) -> str:
    order: list[int] = []
    for c in article.claims:
        for i in c.sources:
            if i not in order:
                order.append(i)
    ref = {i: n + 1 for n, i in enumerate(order)}
    lines = [f"# {article.title}"]
    if article.subtitle:
        lines.append(f"\n_{article.subtitle}_")
    section, para = object(), []

    def flush():
        if para:
            lines.append("\n" + " ".join(para))
            para.clear()

    for c in article.claims:
        if c.section != section:
            flush()
            section = c.section
            if c.section:
                lines.append(f"\n## {c.section}")
        marks = "".join(f"[^{ref[i]}]" for i in c.sources)
        para.append(f"{c.text}{marks}")
    flush()
    if links:
        lines.append(f"\n## {READ_MORE.get((language or '')[:2], 'Read more')}\n")
        lines += [f"- [{x['title']}]({x['url']})" for x in links]
    lines.append("\n" + ("## Fonti" if (language or "").startswith("it") else "## Sources"))
    for i in order:
        s = sources[i]
        lines.append(f"\n[^{ref[i]}]: [{s.title}]({s.url}), {s.name}" + (f", {s.published}" if s.published else ""))
    return "\n".join(lines) + "\n"


SOURCES_HEADING = re.compile(r"\n## (Fonti|Sources)\n")
NOTE = re.compile(r"^\[\^(\d+)\]: \[(?P<title>[^\]]*)\]\((?P<url>[^)\s]+)\)(?:, (?P<name>[^,\n]+))?", re.M)


def discreet_sources(md: str) -> str:
    """The article for a site that shows its sources discreetly: no note
    numbers in the text, and at the end one line naming each source once,
    linked to the first of its articles. The sentences stay tied to their
    sources in the draft; only the published text changes."""
    found = list(SOURCES_HEADING.finditer(md))
    if not found:
        return md
    head, notes = md[:found[-1].start()], md[found[-1].end():]
    links: dict[str, str] = {}
    for n in NOTE.finditer(notes):
        name = (n.group("name") or n.group("title")).strip()
        links.setdefault(name, n.group("url"))
    if not links:
        return md
    text = re.sub(r"\[\^\d+\]", "", head).rstrip()
    label = found[-1].group(1)
    return f"{text}\n\n**{label}:** " + " · ".join(f"[{k}]({v})" for k, v in links.items()) + "\n"


SYSTEM = (
    "You write original news articles for a website. You receive the editorial profile, the accepted proposal and "
    "the source items, each with an id and its text. Write the article as claims: every claim is one to three "
    "sentences and lists the ids of the items that support it. Use only facts found in the cited items; do not "
    "add facts, numbers or quotes from elsewhere, and do not copy sentences from the sources: write in your own "
    "words, in the site's language and tone. "
    "The article tells ONE event: the one in the items marked MAIN. Unmarked items are other outlets' reports "
    "of that same event: use them to confirm it and to add its details. Items marked ORIGINAL are the original "
    "documents the news reports on (official data, a decision, a law, a paper): the central numbers, records "
    "and decisions follow them, and when a news item says something different, write what the document says "
    "and cite the document. Items marked CONTEXT are earlier news: use one only when it helps the reader "
    "understand the event, as a precedent, a comparison or the rule that applies, say plainly which of these it "
    "is, keep it to a sentence or two, and never tell it as a second story; leave out the others. Never join "
    "separate stories into one article. "
    "Tell the event, never your sources or your checking: no heading or sentence about what is confirmed or "
    "not, what is clear or unclear, what the sources, the texts or the outlets say or do not say; when the items "
    "do not give a detail, leave it out without saying so. "
    "Write only what the items say. Length follows the material: when the items give only a title, a summary or "
    "an abstract, or come from one outlet, write a short news piece of 200 to 400 words, attributed to its "
    "source; write longer only when the items carry the details (who, what, "
    "numbers, dates, how it works, what changes for the reader). A short piece must still give what its title "
    "promises (the figure, the decision, what the study found): if the items do not give it, the title does not "
    "promise it. Never pad: no background the items do not give, no repeating a point in other words. "
    "Be exact: give a number with what it is compared to, as the items do (the rate and the period, not "
    "'strong growth'); write a superlative or a first (record, fastest, biggest, first, only) or a cause "
    "(because, thanks to, after) only when an item says it, and say who says it; an allegation, a forecast or a "
    "study's result stays one, attributed to its source. The date of the event is the one the items give for "
    "it, not the day an outlet republished it. The title, the subtitle and the first sentences are not more "
    "certain, larger or newer than the body and the items. "
    "Write like a journalist of the site, not like an assistant: vary the length "
    "of sentences, prefer concrete nouns and verbs to adjectives, no stock openings or closings (\"in today's "
    "world\", \"it is worth noting\", \"in conclusion\", \"game changer\", their equivalents in any language), no "
    "em dashes, en dashes or double hyphens as pauses, no ellipses, no triplets of adjectives, no rhetorical "
    "questions in the text, no summary of what was just said, no colon-split titles: it must read as written by a "
    "person. "
    "Write it so search engines and AI answer engines pick it up: the first section has no heading and answers "
    "the reader's main question in two or three sentences; in a longer article the other headings are the "
    "questions people search for, in plain words. Add frequently asked questions (one to three, each answered "
    "with sourced claims like the rest) only when readers would ask something the items answer and the article "
    "does not already say; otherwise leave the list empty: never repeat the article as questions. Choose the key "
    "phrase readers would type to find this article and use it naturally in the title and the first sentence, "
    "never stuffed. Reply with JSON only: "
    '{"title": "...", "subtitle": "...", "sections": [{"heading": "<or null>", "claims": '
    '[{"text": "...", "sources": [<ids>]}]}], '
    '"faq": [{"question": "...", "claims": [{"text": "...", "sources": [<ids>]}]}], '
    '"seo": {"keyphrase": "<2-4 words>", "title": "<search title, max 60 chars>", '
    '"description": "<meta description, 120-155 chars>", "slug": "<url-words-with-hyphens>"}}'
)


_ARXIV = re.compile(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:\.pdf)?/?$")


def reading_url(url: str) -> str:
    """Where the full text of an item is: an arXiv abstract page links the
    paper, which arXiv also publishes as HTML; an article written from the
    abstract alone stretched it (external audit, 2026-11-03)."""
    found = _ARXIV.match(url or "")
    return f"https://arxiv.org/html/{found.group(1)}" if found else url


# Hosts of original documents: official statistics, laws and decisions,
# authorities, papers. A news story that links one is checked against it:
# the outlet may have the figure or the record wrong (the US GDP article of
# 2026-11-03 repeated an outlet's "fastest growth in five years").
OFFICIAL = (".gov", ".gov.uk", ".gov.it", ".gouv.fr", ".gob.es", ".bund.de", ".admin.ch", ".gv.at", "europa.eu",
            ".int", "arxiv.org", "doi.org", "gazzettaufficiale.it", "garanteprivacy.it", "istat.it", "bancaditalia.it",
            "consob.it", "agcm.it", "agcom.it", "governo.it", "camera.it", "senato.it", "normattiva.it",
            "bundesbank.de", "destatis.de", "banque-france.fr", "insee.fr", "bde.es", "ine.es", "bankofengland.co.uk",
            "ons.gov.uk", "imf.org", "worldbank.org", "oecd.org", "un.org", "who.int")
DOCUMENTS = 2  # original documents read for each main news item
DOCUMENTS_SOURCE = "https://documents.redazia.invalid/"  # the newsroom's source row for them, never collected


def _host(url: str) -> str:
    try:
        return (httpx.URL(url).host or "").lower().removeprefix("www.")
    except (httpx.InvalidURL, TypeError):
        return ""


def document_links(page, url: str) -> list[str]:
    """Links of an article to original documents on official hosts, in the
    text and not in menus; at most DOCUMENTS."""
    own = _host(url)
    out = []
    for link in page.links:
        host = _host(link.url)
        if link.in_nav or not host or host == own or not link.url.startswith("https://"):
            continue
        if any(host == x.lstrip(".") or host.endswith(x if x.startswith(".") else "." + x) for x in OFFICIAL):
            if link.url not in out:
                out.append(link.url)
    return out[:DOCUMENTS]


def fetch_contents(db, tenant_id: str, item_ids: list[int], client: httpx.Client | None) -> dict[int, list[str]]:
    """Read the full text of cited items once, through the Core. Returns,
    for each item read, the original documents its text links."""
    found: dict[int, list[str]] = {}
    if client is None:
        return found
    with db.tenant(tenant_id) as s:
        todo = s.execute(select(m.source_items.c.id, m.source_items.c.url).where(
            m.source_items.c.id.in_(item_ids), m.source_items.c.content_fetched_at.is_(None))).all()
    robots = RobotsCache(client)
    for row in todo:
        content, url = None, reading_url(row.url)
        try:
            if robots.allowed(url):
                r = client.get(url, headers={"Accept": "text/html"})
                if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
                    page = parse_html(r.text, url)
                    content = page.text[:MAX_CONTENT] or None
                    found[row.id] = document_links(page, url)
            else:
                log.info("robots.txt forbids reading %s: drafting from its summary", row.url)
        except httpx.HTTPError as e:
            log.info("cannot read %s: %s", row.url, e)
        # Read once for everyone: the item is in the archive, or (collected
        # before it) the newsroom's own.
        for tenant in dict.fromkeys((repo.archive_of(db, tenant_id), tenant_id)):
            with db.tenant(tenant) as s:
                s.execute(update(m.source_items).where(m.source_items.c.id == row.id).values(
                    content=content, content_fetched_at=datetime.now(UTC)))
    return found


def _documents_source(s, tenant_id: str) -> int:
    """The newsroom's source row for original documents: rejected, so it is
    never collected nor followed, and with no site, so no list shows it."""
    src = m.sources.c
    found = s.execute(select(src.id).where(src.url == DOCUMENTS_SOURCE, repo.own())).scalar()
    if found is not None:
        return found
    return s.execute(insert(m.sources).values(
        tenant_id=tenant_id, kind="rss", name="Documenti originali", url=DOCUMENTS_SOURCE, config={}, weight=1.0,
        site_id=None, status="rejected", origin="manual", evidence={}, shared=False,
    ).returning(m.sources.c.id)).scalar_one()


def store_document(db, tenant_id: str, item_id: int, url: str, page) -> int | None:
    """Keep a document the article can cite, as a copy of the news that led
    to it (never news of its own); None for a landing page."""
    from .normalize import canonical_url, content_hash, sha256_hex

    text = page.text[:MAX_CONTENT]
    if len(text.split()) < 80:  # a landing page, not a document
        return None
    title = " ".join((page.title or page.h1 or url).split())[:300]
    with db.tenant(tenant_id) as s:
        return s.execute(insert(m.source_items).values(
            tenant_id=tenant_id, source_id=_documents_source(s, tenant_id), url=url,
            canonical_url=canonical_url(url), url_hash=sha256_hex(canonical_url(url)),
            content_hash=content_hash(title, text[:500]), title=title, summary="", metrics={},
            minhash=[], duplicate_of=item_id, content=text, content_fetched_at=datetime.now(UTC),
        ).on_conflict_do_nothing().returning(m.source_items.c.id)).scalar()


def read_documents(db, tenant_id: str, links: dict[int, list[str]], client: httpx.Client | None) -> list[int]:
    """Read the original documents the main news link and store them as
    items the article can cite: marked as copies of the news that linked
    them, so they never become news of their own."""
    from .normalize import canonical_url, sha256_hex

    out: list[int] = []
    if client is None:
        return out
    robots = RobotsCache(client)
    for item_id, urls in links.items():
        for url in urls:
            with db.tenant(tenant_id) as s:
                known = s.execute(select(m.source_items.c.id).where(
                    m.source_items.c.url_hash == sha256_hex(canonical_url(url)))).scalar()
            if known is not None:
                out.append(known)
                continue
            try:
                if not robots.allowed(url):
                    continue
                r = client.get(url, headers={"Accept": "text/html"})
            except httpx.HTTPError as e:
                log.info("cannot read the document %s: %s", url, e)
                continue
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                continue
            stored = store_document(db, tenant_id, item_id, url, parse_html(r.text, url))
            if stored is not None:
                out.append(stored)
    return list(dict.fromkeys(out))


def _sources(s, ids: list[int]) -> dict[int, Source]:
    rows = s.execute(
        select(m.source_items, m.sources.c.name.label("source_name"))
        .join(m.sources, m.sources.c.id == m.source_items.c.source_id)
        .where(m.source_items.c.id.in_(ids))).all()
    pages = Counter(r.canonical_url for r in rows if r.canonical_url)
    return {r.id: Source(r.id, r.title, r.url, _host(r.url) if r.source_name == "Documenti originali" else r.source_name,
                         r.summary or "", r.content or "",
                         r.published_at.date().isoformat() if r.published_at else None,
                         r.fetched_at.strftime("%Y-%m-%d %H:%M UTC") if r.fetched_at else None,
                         bool(r.canonical_url and pages[r.canonical_url] > 1)) for r in rows}


def _prompt(profile: dict, proposal, sources: dict[int, Source], roles: dict[int, str] | None = None,
            posts: list[dict] | None = None) -> str:
    """`roles`: MAIN, ORIGINAL or CONTEXT for each item that has one; the
    others are other outlets' reports of the same event."""
    from .autopilot import brief_lines

    style = profile.get("style", {})
    target = target_words(profile)
    lines = [
        f"Site: {profile.get('name')} ({profile.get('domain')}), language: {profile.get('language')}",
        f"Audience: {(profile.get('audience') or {}).get('value') or 'not determined'}",
        f"Tone: {style.get('tone') or 'not determined'}; length: as the items allow, at most about {target} words",
        f"Proposal: {proposal.title}",
        f"Angle: {proposal.angle}",
        f"Format: {proposal.format}",
        *brief_lines(profile),
        *_post_lines(posts or []),
        ("Items (MAIN: the news the article is about; ORIGINAL: the original document it reports on; CONTEXT: "
         "earlier news for context only; unmarked: other outlets' reports of the same event):"),
    ]
    roles = roles or {}
    for src in sources.values():
        role = roles.get(src.id, "")
        when = f", read {src.fetched}" if src.fetched else ""
        live = "; the same page seen with different titles: a page updated over time, tell what it said when read" if src.live else ""
        lines.append(f"[{src.id}]{' ' + role if role else ''} {src.title} ({src.name}, {src.published or 'undated'}{when}{live})\n"
                     f"{(src.summary + ' ' + src.content)[:6000 if role in ('MAIN', 'ORIGINAL') else 2500]}")
    return "\n".join(lines)


def _post_lines(posts: list[dict]) -> list[str]:
    """What the site already published on the subject: an article this close
    is updated, not retold; the others are only completed."""
    from .opportunity import UPDATE_SIMILARITY

    if not posts:
        return []
    top = posts[0]
    if top["score"] >= UPDATE_SIMILARITY:
        return [f"The site already published «{top['title']}» ({top['url']}) on this subject: write the updated "
                "version, leading with what is new since then; do not retell it as a fresh story."]
    return ["The site already published these related articles; complete them, do not repeat them: "
            + "; ".join(f"«{p['title']}»" for p in posts)]


def context_items(s, main: list[int], skip: list[int], limit: int = CONTEXT) -> list[int]:
    """Earlier news on the subject of the main news, for context."""
    si = m.source_items
    base = s.execute(select(si.c.title, si.c.summary).where(si.c.id.in_(main))).all()
    terms = set().union(*(item_terms(r.title, r.summary or "") for r in base)) if base else set()
    if not terms:
        return []
    rows = s.execute(select(si.c.id, si.c.title, si.c.summary).where(
        si.c.duplicate_of.is_(None), si.c.fetched_at > datetime.now(UTC) - timedelta(days=CONTEXT_DAYS),
        si.c.id.not_in(main + skip)).order_by(si.c.id.desc()).limit(3000)).all()
    scored = []
    for r in rows:
        shared = len(terms & (set(tokens(r.title)) | set(tokens(r.summary or ""))))
        if shared >= 2:
            scored.append((shared, r.id))
    return [i for _, i in sorted(scored, reverse=True)[:limit]]


def _same_event(anchor: set[str], terms: set[str]) -> bool:
    return len(anchor & terms) >= min(len(anchor), max(EVENT_MIN, math.ceil(len(anchor) * EVENT_SHARE)))


def event_items(s, cited: list[int], title: str = "", limit: int = RELATED) -> tuple[list[int], list[int]]:
    """The news an article is about and other outlets' reports of the same
    event. The main news is the cited item closest to the proposal's title;
    the other cited items stay only if they are about the same event, so a
    proposal built on a sector (two companies, two attacks, a figure and an
    unrelated deal) is written about one story. Items from the same sector
    that are not the same event are left out: they made articles that joined
    separate news (found by an external audit, 2026-11-03)."""
    si = m.source_items
    rows = s.execute(select(si.c.id, si.c.title, si.c.summary, si.c.fetched_at).where(si.c.id.in_(cited))).all()
    if not rows:
        return [], []
    order = {i: n for n, i in enumerate(cited)}
    want = set(tokens(title))
    lead = max(rows, key=lambda r: (len(want & item_terms(r.title, r.summary or "")), -order.get(r.id, 0)))
    anchor = set(tokens(lead.title))
    if len(anchor) < 3:  # a title too short to tell an event: its summary helps
        anchor = item_terms(lead.title, lead.summary or "")
    main = [r.id for r in sorted(rows, key=lambda r: order.get(r.id, 0))
            if r.id == lead.id or _same_event(anchor, item_terms(r.title, r.summary or ""))]
    since = min((r.fetched_at for r in rows if r.id in main and r.fetched_at), default=datetime.now(UTC))
    rows = s.execute(select(si.c.id, si.c.title, si.c.summary).where(
        si.c.duplicate_of.is_(None), si.c.fetched_at > since - timedelta(days=EVENT_DAYS), si.c.id.not_in(main))
        .order_by(si.c.id.desc()).limit(3000)).all()
    scored = []
    for r in rows:
        terms = item_terms(r.title, r.summary or "")
        if _same_event(anchor, terms):
            scored.append((len(anchor & terms), r.id))
    return main, [i for _, i in sorted(scored, reverse=True)[:limit]]


def write_draft(db, tenant_id: str, proposal_id: int, core: CoreClient, client: httpx.Client | None = None,
                pay: bool = True, purpose: str = "draft", notes: str | None = None, outlets: int = 0) -> int:
    """Write and store a draft for an accepted proposal; returns its id.
    Past the plan's articles of the month it is paid with credits, unless
    `pay` is False (the automatic newsroom stays within the plan)."""
    with db.tenant(tenant_id) as s:
        prop = s.execute(select(m.proposals).where(m.proposals.c.id == proposal_id)).first()
        if prop is None or prop.status not in ("accepted", "drafted"):
            raise ValueError("drafts are written for accepted proposals only")
        quotas.check(s, "drafts", pay="article" if pay else None)
        cited = list(s.execute(select(m.proposal_citations.c.item_id).where(
            m.proposal_citations.c.proposal_id == proposal_id)).scalars())
        cited, same = event_items(s, cited, prop.title)
        context = context_items(s, cited, same)
        ids = cited + same + context
        profile = s.execute(select(m.editorial_profiles.c.body).where(m.editorial_profiles.c.id == prop.profile_id)).scalar()
        from .opportunity import related_posts

        news = list(s.execute(select(m.source_items.c.title).where(m.source_items.c.id.in_(cited))).scalars())
        seo.remember_published(s, tenant_id, prop.site_id)
        posts = related_posts(s, prop.site_id, [prop.title, *news])
    step(N_("Lettura degli articoli di {n} notizie sullo stesso fatto"), n=len(ids))
    links = fetch_contents(db, tenant_id, ids, client)
    documents = read_documents(db, tenant_id, {i: u for i, u in links.items() if i in cited}, client)
    if documents:
        step(N_("Lettura di {n} documenti originali citati dalla notizia"), n=len(documents))
    ids = list(dict.fromkeys(ids + documents))
    with db.tenant(tenant_id) as s:
        sources = _sources(s, ids)
    if client is not None:
        # The central fact told by outlets only: look for the document it comes from.
        from . import primary

        try:
            found = primary.look(db, tenant_id, prop, [sources[i] for i in cited if i in sources], bool(documents),
                                 core, client)
        except Exception:  # never stops the article: it is written from what it has
            log.exception("primary source search failed")
            found = []
        if found:
            documents = list(dict.fromkeys(documents + found))
            ids = list(dict.fromkeys(ids + found))
            with db.tenant(tenant_id) as s:
                sources = _sources(s, ids)

    allowed = set(sources)
    roles = {**{i: "CONTEXT" for i in context}, **{i: "MAIN" for i in cited}, **{i: "ORIGINAL" for i in documents}}
    prompt = _prompt(profile, prop, sources, roles, posts)
    # The automatic newsroom publishes only articles citing this many outlets: asked
    # from the first writing, not found missing after it was paid for.
    told_by = {sources[i].name for i in sources if roles.get(i) not in ("CONTEXT", "ORIGINAL")}
    need = outlets if outlets and len(told_by) >= outlets else 0
    if need:
        prompt += (f"\n\nCite items of at least {need} different outlets (the name in brackets after each item's "
                   "title), each for what it adds about the event.")
    if notes:  # a version written again: what was wrong with the last one
        prompt += f"\n\nA previous version of this article was refused. Write it again and fix this: {notes}"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    article = None
    from .core_client import extract_json

    for attempt in range(3):
        step(N_("Il modello scrive la bozza") if attempt == 0
             else N_("Il modello corregge la bozza: frasi senza fonte o numeri che le fonti non riportano"))
        with usage.tagged(db, tenant_id, purpose, site_id=prop.site_id, ref=f"proposal:{proposal_id}"):
            res = core.chat(messages)
        try:
            article = check(parse_article(extract_json(res.content), allowed), sources)
        except ValueError as e:
            problem = str(e)
        else:
            short = need and len({sources[i].name for c in article.claims for i in c.sources if i in sources}) < need
            if not article.flags and not short:
                break
            problem = "; ".join(f"claim {f['claim']}: {', '.join(f['problems'])}" for f in article.flags)
            if short:
                problem = "; ".join(x for x in (problem, f"cite items of at least {need} different outlets") if x)
        messages += [{"role": "assistant", "content": res.content},
                     {"role": "user", "content": f"Fix these problems and reply with the whole JSON again: {problem}"}]
    if article is None:
        raise CoreError("the model did not produce a valid article")

    sd = seo.parse(article.seo, article.title)
    sd.faq = article.faq
    with db.tenant(tenant_id) as s:
        sd.links = seo.merge_links(posts, seo.site_links(s, prop.site_id, article.title, sd.keyphrase))
    body = render(article, sources, profile.get("language"), sd.links)
    first = next((c.section for c in article.claims), None)
    lead = " ".join(c.text for c in article.claims if c.section == first)
    sd.checks = seo.checks(sd, article.title, lead, list(dict.fromkeys(c.section for c in article.claims)),
                           len({i for c in article.claims for i in c.sources}))
    with db.tenant(tenant_id) as s:
        # Checked again with the insert: other drafts may have been written
        # while this one was with the model.
        over = quotas.check(s, "drafts", pay="article" if pay else None)
        quotas.lock(s, f"editor-draft-version:{proposal_id}")
        version = (s.execute(select(func.max(m.drafts.c.version)).where(m.drafts.c.proposal_id == proposal_id)).scalar() or 0) + 1
        did = s.execute(insert(m.drafts).values(
            tenant_id=tenant_id, proposal_id=proposal_id, site_id=prop.site_id, version=version,
            title=article.title, subtitle=article.subtitle, body_md=body, language=profile.get("language"),
            status="needs_review" if article.flags else "draft", flags=article.flags, generated_by="model",
            seo=sd.as_dict(),
        ).returning(m.drafts.c.id)).scalar_one()
        if over:  # beyond the plan's articles of the month: paid with credits
            credits.spend(s, "article", f"draft:{did}")
        for n, c in enumerate(article.claims):
            cid = s.execute(insert(m.draft_claims).values(
                tenant_id=tenant_id, draft_id=did, ordinal=n, section=c.section, text=c.text,
            ).returning(m.draft_claims.c.id)).scalar_one()
            s.execute(insert(m.draft_claim_sources).values(
                [{"tenant_id": tenant_id, "claim_id": cid, "item_id": i} for i in c.sources]))
        s.execute(update(m.proposals).where(m.proposals.c.id == proposal_id).values(status="drafted"))
    return did


def decide(db, tenant_id: str, draft_id: int, approve: bool, by: str) -> bool:
    with db.tenant(tenant_id) as s:
        row = s.execute(select(m.drafts.c.status).where(m.drafts.c.id == draft_id)).first()
        if row is None or row.status in ("approved", "rejected"):
            return False
        s.execute(update(m.drafts).where(m.drafts.c.id == draft_id).values(
            status="approved" if approve else "rejected", decided_by=by, decided_at=datetime.now(UTC)))
    return True


def stored_claims(s, draft_id: int) -> list[dict]:
    """The claims of a draft in order, each with the ids of its sources."""
    c, cs = m.draft_claims, m.draft_claim_sources
    sources: dict[int, list[int]] = {}
    for r in s.execute(select(cs.c.claim_id, cs.c.item_id).join(c, c.c.id == cs.c.claim_id)
                       .where(c.c.draft_id == draft_id).order_by(cs.c.item_id)):
        sources.setdefault(r.claim_id, []).append(r.item_id)
    rows = s.execute(select(c.c.id, c.c.ordinal, c.c.section, c.c.text)
                     .where(c.c.draft_id == draft_id).order_by(c.c.ordinal))
    return [dict(r._mapping, sources=sources.get(r.id, [])) for r in rows]


# --- the director's free editor ---------------------------------------------------------
# The whole article as text: one sentence per line, "## " for a heading,
# [1] [2] after a sentence for its sources (numbered as on the draft page).
REF = re.compile(r"\s*\[(\d+(?:\s*,\s*\d+)*)\]")


def source_order(claims: list[dict]) -> list[int]:
    order: list[int] = []
    for c in claims:
        order += [i for i in c["sources"] if i not in order]
    return order


def as_text(claims: list[dict]) -> str:
    """The article in the free editor's form."""
    ref = {i: n + 1 for n, i in enumerate(source_order(claims))}
    out, section = [], object()
    for c in claims:
        if c["section"] != section:
            section = c["section"]
            if c["section"]:
                out += ["", f"## {c['section']}"] if out else [f"## {c['section']}"]
        marks = " ".join(f"[{ref[i]}]" for i in c["sources"] if i in ref)
        out.append(f"{c['text']} {marks}".rstrip())
    return "\n".join(out).strip() + "\n"


def parse_text(text: str, order: list[int]) -> list[Claim]:
    """The free editor's text back into sentences with their sources. A
    number that is not on the list of sources is an error."""
    claims, section = [], None
    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            section = line.lstrip("#").strip() or None
            continue
        refs: list[int] = []
        for mark in REF.finditer(line):
            for n in mark.group(1).split(","):
                k = int(n)
                if not 1 <= k <= len(order):
                    raise ValueError(f"source [{k}] is not on the list")
                if order[k - 1] not in refs:
                    refs.append(order[k - 1])
        sentence = " ".join(REF.sub("", line).split())
        if sentence.startswith(("- ", "* ")):
            sentence = sentence[2:]
        if sentence:
            claims.append(Claim(sentence, refs, section))
    if not claims:
        raise ValueError("the article is empty")
    return claims


def edit_text(db, tenant_id: str, draft_id: int, title: str, subtitle: str, text: str,
              search: dict | None = None) -> dict:
    """The director's free edit of the whole article: sentences added,
    removed or rewritten, headings changed. A sentence without sources is
    the director's own and is published as written; a cited sentence is
    checked against its sources as usual (flags, never a refusal). An
    approved draft goes back to be approved."""
    title = " ".join((title or "").split())
    if not 5 <= len(title) <= 160:
        raise ValueError("title must be 5 to 160 characters")
    with db.tenant(tenant_id) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == draft_id)).first()
        if d is None:
            raise LookupError(draft_id)
        order = source_order(stored_claims(s, draft_id))
        sources = _sources(s, order)
    claims = parse_text(text, order)
    old = d.seo or {}
    sd = seo.parse({**{k: old.get(k) for k in ("keyphrase", "title", "description", "slug")}, **(search or {})}, title)
    sd.links = old.get("links") or []
    sections = list(dict.fromkeys(c.section for c in claims))
    sd.faq = [q for q in old.get("faq") or [] if q in sections]
    article = check(Article(title, " ".join((subtitle or "").split())[:300], claims, sd.faq, {}), sources)
    for c in article.claims:  # the director's own sentence: no source asked
        if not c.sources:
            c.flags = []
    first = next((c.section for c in claims), None)
    lead = " ".join(c.text for c in claims if c.section == first)
    sd.checks = seo.checks(sd, title, lead, sections, len({i for c in claims for i in c.sources}))
    status = "needs_review" if article.flags else "draft"
    keep = {k: old[k] for k in ("section", "replaces") if k in old}
    with db.tenant(tenant_id) as s:
        ids = list(s.execute(select(m.draft_claims.c.id).where(m.draft_claims.c.draft_id == draft_id)).scalars())
        if ids:
            s.execute(delete(m.draft_claim_sources).where(m.draft_claim_sources.c.claim_id.in_(ids)))
            s.execute(delete(m.draft_claims).where(m.draft_claims.c.id.in_(ids)))
        for n, c in enumerate(article.claims):
            cid = s.execute(insert(m.draft_claims).values(
                tenant_id=tenant_id, draft_id=draft_id, ordinal=n, section=c.section, text=c.text,
            ).returning(m.draft_claims.c.id)).scalar_one()
            for i in c.sources:
                s.execute(insert(m.draft_claim_sources).values(tenant_id=tenant_id, claim_id=cid, item_id=i))
        s.execute(update(m.drafts).where(m.drafts.c.id == draft_id).values(
            title=title, subtitle=article.subtitle, body_md=render(article, sources, d.language, sd.links),
            flags=article.flags, seo={**sd.as_dict(), **keep}, status=status, decided_by=None, decided_at=None))
    return {"status": status, "flags": article.flags, "was_approved": d.status == "approved"}


def mark_replacement(db, tenant_id: str, draft_id: int, replaces: int) -> None:
    """A version written again at the director's request: once approved, it
    can go online in place of `replaces`, at the same address."""
    with db.tenant(tenant_id) as s:
        sd = s.execute(select(m.drafts.c.seo).where(m.drafts.c.id == draft_id)).scalar() or {}
        s.execute(update(m.drafts).where(m.drafts.c.id == draft_id).values(seo={**sd, "replaces": replaces}))


def edit(db, tenant_id: str, draft_id: int, title: str, subtitle: str, texts: dict[int, str],
         search: dict | None = None) -> dict:
    """A person's corrections to a draft: title, subtitle, the text of each
    sentence and the search fields. Every sentence keeps its sources and is
    checked again like the model's (numbers in the sources, no copying); an
    approved draft goes back to be approved, because what was approved has
    changed. Returns the new status and the problems found."""
    title = " ".join((title or "").split())
    if not 5 <= len(title) <= 160:
        raise ValueError("title must be 5 to 160 characters")
    with db.tenant(tenant_id) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == draft_id)).first()
        if d is None:
            raise LookupError(draft_id)
        stored = stored_claims(s, draft_id)
        sources = _sources(s, list({i for c in stored for i in c["sources"]}))
    claims = []
    for c in stored:
        text = " ".join((texts.get(c["ordinal"], c["text"]) or "").split())
        if not text:
            raise ValueError("a sentence cannot be empty: delete the draft or write it again")
        claims.append(Claim(text, c["sources"], c["section"]))
    old = d.seo or {}
    sd = seo.parse({**{k: old.get(k) for k in ("keyphrase", "title", "description", "slug")}, **(search or {})}, title)
    sd.faq, sd.links = old.get("faq") or [], old.get("links") or []
    article = check(Article(title, " ".join((subtitle or "").split())[:300], claims, sd.faq, {}), sources)
    first = next((c.section for c in claims), None)
    lead = " ".join(c.text for c in claims if c.section == first)
    sd.checks = seo.checks(sd, title, lead, list(dict.fromkeys(c.section for c in claims)),
                           len({i for c in claims for i in c.sources}))
    status = "needs_review" if article.flags else "draft"
    with db.tenant(tenant_id) as s:
        for c, new in zip(stored, claims):
            if new.text != c["text"]:
                s.execute(update(m.draft_claims).where(m.draft_claims.c.id == c["id"]).values(text=new.text))
        s.execute(update(m.drafts).where(m.drafts.c.id == draft_id).values(
            title=title, subtitle=article.subtitle, body_md=render(article, sources, d.language, sd.links),
            flags=article.flags, seo=sd.as_dict(), status=status, decided_by=None, decided_at=None))
    return {"status": status, "flags": article.flags, "was_approved": d.status == "approved"}
