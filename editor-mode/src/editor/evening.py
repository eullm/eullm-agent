"""Il Punto della Sera: the director's analysis of the day.

Every evening, at the hour the owner sets (21 by default, in the
newsroom's time zone), an "editorial director" reads every article the
site published in the last 24 hours and writes one analysis: it finds the
event that weighs most (agenda setting), opens with it and builds the
piece around it, connects the other news to it where the articles allow,
and leaves sport and minor news to a short last section (Francesco
2026-11-05).

The articles are already in the database: each becomes an item the
analysis cites, linked to its page on the site, so the analysis links the
day's articles and every sentence is checked against them like any
article against its sources (numbers, copying). The AI editor-in-chief
reads it with the evening rules (autopilot.EVENING_RULES): a link between
two stories that no article states is the newsroom's reading, never news.

It is for the plans that include it (plans.evening) and for a day with
enough articles; with the automatic newsroom on it goes out by itself,
otherwise it waits in Drafts for the director.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from . import drafts, models as m, quotas, repo, seo, style, usage
from .core_client import CoreError, extract_json
from .i18n import N_
from .progress import step

log = logging.getLogger(__name__)

FORMAT = "evening"  # proposals.format of the analysis
BY = "direttore AI"  # who decides on its proposal (not the automatic newsroom: it does not take a day's article)
WINDOW = timedelta(hours=24)
ARTICLES = "https://articles.redazia.invalid/"  # the newsroom's source rows for its own articles, never collected
MAX_ARTICLES = 40  # read for one analysis, the latest first
TEXT = 2500  # characters of each article the director reads

PIECE = {"it": "Il Punto della Sera", "en": "The Evening Brief", "de": "Der Abendbericht",
         "fr": "Le Point du soir", "es": "El Punto de la Noche"}
SECTION = {"it": "Analisi e Approfondimenti", "en": "Analysis", "de": "Analysen und Hintergründe",
           "fr": "Analyses et décryptages", "es": "Análisis y profundización"}
DEFAULTS = {"enabled": False, "hour": 21, "min_articles": 6, "section": ""}
MONTHS = {
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
           "November", "Dezember"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"],
}
DATED = {"it": "{piece} del {d} {month}", "en": "{piece}, {d} {month}", "de": "{piece} vom {d}. {month}",
         "fr": "{piece} du {d} {month}", "es": "{piece} del {d} de {month}"}
PLAIN_SUBTITLE = {
    "it": "Le notizie del giorno lette insieme dal caporedattore AI.",
    "en": "The day's news read together by the AI editor-in-chief.",
    "de": "Die Nachrichten des Tages, zusammen gelesen vom KI-Chefredakteur.",
    "fr": "Les nouvelles du jour lues ensemble par le rédacteur en chef IA.",
    "es": "Las noticias del día leídas juntas por el redactor jefe IA.",
}


class _Evening(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    hour: int = Field(DEFAULTS["hour"], ge=0, le=23)
    min_articles: int = Field(DEFAULTS["min_articles"], ge=2, le=50)
    section: str = Field("", max_length=80)


def settings_of(body: dict | None) -> dict:
    return {**DEFAULTS, **(((body or {}).get("settings") or {}).get("evening") or {})}


def check_settings(values: dict) -> dict:
    try:
        cfg = _Evening.model_validate(values).model_dump()
    except ValidationError as e:
        raise ValueError("; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                                   for err in e.errors()[:3])) from None
    cfg["section"] = " ".join(cfg["section"].split())
    return cfg


def save(s, site_id: int, values: dict) -> bool:
    from .profile import _set_settings

    return _set_settings(s, site_id, evening=check_settings(values))


def section_of(cfg: dict, language: str | None) -> str:
    return cfg.get("section") or SECTION.get((language or "it")[:2], SECTION["it"])


def allowed(s) -> bool:
    """The plan includes the evening analysis and the newsroom is paid for."""
    row = s.execute(select(m.tenants.c.evening, m.tenants.c.proposals_only)).first()
    return row is None or (row.evening is not False and row.proposals_only is None)


def due(body: dict | None, local: datetime) -> bool:
    """Whether the analysis of this day is still to be written: on, past its
    hour, and not yet done today."""
    cfg = settings_of(body)
    last = ((body or {}).get("settings") or {}).get("evening_last")
    return bool(body) and cfg["enabled"] and local.hour >= cfg["hour"] and last != local.date().isoformat()


def published(s, site_id: int, now: datetime) -> list:
    """The site's articles published in the last 24 hours, in its own
    language (no translations, no earlier analyses), latest first, once each."""
    p, d, t, pr = m.publications.c, m.drafts.c, m.publish_targets.c, m.proposals.c
    rows = s.execute(
        select(d.id, d.title, d.subtitle, d.seo, d.language, p.external_url, p.published_at)
        .select_from(m.publications.join(m.drafts, d.id == p.draft_id)
                     .join(m.publish_targets, t.id == p.target_id)
                     .join(m.proposals, pr.id == d.proposal_id))
        .where(d.site_id == site_id, d.translation_of.is_(None), p.status == "published", p.mode == "publish",
               p.withdrawn_at.is_(None), p.published_at >= now - WINDOW, t.kind.in_(("github", "wordpress", "webhook")),
               pr.format != FORMAT)
        .order_by(p.published_at.desc(), p.id)).all()
    seen, out = set(), []
    for r in rows:
        if r.id not in seen:
            seen.add(r.id)
            out.append(r)
    return out[:MAX_ARTICLES]


def _article_text(s, draft_id: int) -> str:
    out, last = [], None
    for c in drafts.stored_claims(s, draft_id):
        if c["section"] and c["section"] != last:
            out.append(f"## {c['section']}")
        last = c["section"]
        out.append(c["text"])
    return "\n".join(out)


def _source(s, tenant_id: str, site_id: int, name: str) -> int:
    """The site's source row for its own articles: rejected, so it is never
    collected nor followed, and with no site, so no list shows it."""
    url = f"{ARTICLES}{site_id}"
    src = m.sources.c
    found = s.execute(select(src.id).where(src.url == url, repo.own())).scalar()
    if found is not None:
        return found
    return s.execute(insert(m.sources).values(
        tenant_id=tenant_id, kind="rss", name=name, url=url, config={}, weight=1.0, site_id=None,
        status="rejected", origin="manual", evidence={}, shared=False,
    ).returning(m.sources.c.id)).scalar_one()


def _items(s, tenant_id: str, site_id: int, domain: str, rows: list) -> list[int]:
    """Each article as an item the analysis can cite, linked to its page and
    marked as a copy of the news it told, so it never becomes news itself."""
    from .normalize import canonical_url, content_hash, sha256_hex

    source = _source(s, tenant_id, site_id, domain)
    out = []
    for r in rows:
        claims = drafts.stored_claims(s, r.id)
        told = next((i for c in claims for i in c["sources"]), None)
        if told is None:
            continue
        url = r.external_url or f"{ARTICLES}{site_id}/draft/{r.id}"
        key = sha256_hex(canonical_url(url))
        found = s.execute(select(m.source_items.c.id)
                          .join(m.sources, m.sources.c.id == m.source_items.c.source_id)
                          .where(m.source_items.c.url_hash == key, repo.own())).scalar()
        text = _article_text(s, r.id)
        if found is None:
            found = s.execute(insert(m.source_items).values(
                tenant_id=tenant_id, source_id=source, url=url, canonical_url=canonical_url(url), url_hash=key,
                content_hash=content_hash(r.title, text[:500]), title=r.title, summary=r.subtitle or "",
                metrics={"section": (r.seo or {}).get("section")}, minhash=[], duplicate_of=told, content=text,
                published_at=r.published_at, content_fetched_at=datetime.now(UTC),
            ).on_conflict_do_nothing().returning(m.source_items.c.id)).scalar()
        if found is not None:
            out.append(found)
    return out


def _topic(s, tenant_id: str) -> int:
    """The topic its proposals hang on: one per newsroom, with no news and an
    old date, so no clustering and no list ever picks it up."""
    tp = m.topics.c
    found = s.execute(select(tp.id).where(tp.label == PIECE["it"], tp.labelled_by == BY)).scalar()
    if found is not None:
        return found
    old = datetime(2000, 1, 1, tzinfo=UTC)
    return s.execute(insert(m.topics).values(tenant_id=tenant_id, label=PIECE["it"], keywords=[], labelled_by=BY,
                                             created_at=old, updated_at=old).returning(tp.id)).scalar_one()


SYSTEM = (
    "You are the editorial director of the news site named below. You receive every article the site "
    "published today, each with an id, its section and its text. Write the closing analysis of the day, the "
    "piece named below. Rules: "
    "(1) Agenda setting: find the event of the day that weighs most, by its consequences (for many people, "
    "for markets, for policy, for this site's readers), not by how many articles it had. If one global event "
    "dominates the day, the whole analysis opens with it and turns around it. "
    "(2) Connect the dots: never a list of the day's articles one after the other. Link the facts: say how the "
    "main event bears on the other news of the day (economy, technology, markets, politics) where the articles "
    "give the facts the link rests on. A link that no article states is the newsroom's reading: write it as a "
    "reading (\"suggests\", \"points to\", \"read together\"), never as news. Never write about the articles or the sources themselves: no sentence saying that a link is "
    "the newsroom's reading, that no article or source connects two facts, or what the articles do not say "
    "(the check refuses them); just write the reading. Keep every fact in the order and at the date the "
    "articles give it: a reading never moves an event before or after another. "
    "(3) Filter the noise: sport and minor news go in one short last section (a heading such as \"In breve\" in "
    "the site's language), a sentence each, or are left out when the day had major events. "
    "(4) Tone: analytical, institutional, for executives, in the manner of Il Sole 24 Ore or The Economist, in "
    "the site's language. No stock openings (\"In questa giornata\", \"Today was\") and no closings (\"In "
    "conclusione\"): straight to the facts and what they mean next. "
    "Use only facts the articles give: add no fact, number, name, quote or date from elsewhere; a forecast "
    "stays someone's and is attributed. Write in your own words, never copying the articles' sentences. "
    "No em dashes, en dashes or double hyphens as pauses, no rhetorical questions, no triplets of adjectives, "
    "none of the stock phrases of machine-written text (" + style.examples() + "). "
    "Format: the title says the day's story (not the name of the piece); the subtitle is one analytical "
    "sentence. The title and the subtitle state only what the articles say, no more certain and no larger: a "
    "link between two stories, a cause or an effect stays out of them and goes in the text as a reading; then 3 or 4 sections with headings, the first section without a heading, and the short last "
    "section if any; 600 to 1200 words. Write it as claims: every claim is one to three sentences and lists the "
    "ids of the articles it rests on; a claim that connects two stories cites both. Reply with JSON only: "
    '{"title": "...", "subtitle": "...", "sections": [{"heading": "<or null>", "claims": '
    '[{"text": "...", "sources": [<ids>]}]}], '
    '"seo": {"keyphrase": "<2-4 words>", "title": "<search title, max 60 chars>", '
    '"description": "<meta description, 120-155 chars>", "slug": "<url-words-with-hyphens>"}}'
)


def plain_title(language: str | None, day) -> str:
    """The analysis' name and date, as its title when the one it was given
    said more than the day's articles."""
    lang = (language or "en")[:2] if (language or "en")[:2] in PIECE else "en"
    return DATED[lang].format(piece=PIECE[lang], d=day.day, month=MONTHS[lang][day.month - 1])


def plain_headline(db, tenant_id: str, d) -> bool:
    """The analysis with its name and date as title and a plain subtitle, the
    text unchanged (2026-11-06: the evening analysis of aitoday.it discarded
    for a title and a subtitle more certain than the articles). False when it
    already has them."""
    from datetime import date

    with db.tenant(tenant_id) as s:
        basis = s.execute(select(m.proposals.c.basis).where(m.proposals.c.id == d.proposal_id)).scalar() or {}
        text = drafts.as_text(drafts.stored_claims(s, d.id))
    try:
        day = date.fromisoformat(str(basis.get("evening")))
    except ValueError:
        day = (d.created_at or datetime.now(UTC)).date()
    lang = (d.language or "en")[:2]
    title = plain_title(d.language, day)
    if d.title == title:
        return False
    piece = PIECE.get(lang, PIECE["en"])
    subtitle = f"{piece}. {PLAIN_SUBTITLE.get(lang, PLAIN_SUBTITLE['en'])}"
    try:
        drafts.edit_text(db, tenant_id, d.id, title, subtitle, text, search={"title": title[:60], "description": None})
    except (ValueError, LookupError):
        return False
    return True


def _prompt(profile: dict, piece: str, day: str, sources: dict, sections: dict, notes: str | None) -> str:
    from .autopilot import brief_lines

    style_ = profile.get("style") or {}
    lines = [
        f"Site: {profile.get('name')} ({profile.get('domain')}), language: {profile.get('language')}",
        f"Audience: {(profile.get('audience') or {}).get('value') or 'not determined'}",
        f"Tone of the site: {style_.get('tone') or 'not determined'}",
        f"Piece: {piece}, {day}",
        *brief_lines(profile),
        "Today's articles:",
    ]
    for src in sources.values():
        lines.append(f"[{src.id}] {src.title} ({sections.get(src.id) or 'no section'}, {src.published or ''})\n"
                     f"{src.summary}\n{src.content[:TEXT]}")
    if notes:
        lines.append(f"\nA previous version was refused. Write it again and fix this: {notes}")
    return "\n".join(lines)


def write(db, tenant_id: str, site_id: int, core, now: datetime, tz: str = "UTC", notes: str | None = None,
          proposal_id: int | None = None) -> int | None:
    """Write the evening analysis of the site's day and store it as a draft;
    None when the day has too few articles. `notes` and `proposal_id`: the
    same analysis written again, for these reasons."""
    from zoneinfo import ZoneInfo

    local = now.astimezone(ZoneInfo(tz))
    with db.tenant(tenant_id) as s:
        prof = s.execute(select(m.editorial_profiles.c.id, m.editorial_profiles.c.body).where(
            m.editorial_profiles.c.site_id == site_id, m.editorial_profiles.c.status == "approved")).first()
        if prof is None:
            return None
        profile, cfg = prof.body, settings_of(prof.body)
        lang = (profile.get("language") or "it")[:2]
        piece = PIECE.get(lang, PIECE["en"])
        domain = s.execute(select(m.sites.c.domain).where(m.sites.c.id == site_id)).scalar() or ""
        if proposal_id is None:
            rows = published(s, site_id, now)
            if len(rows) < cfg["min_articles"]:
                return None
            items = _items(s, tenant_id, site_id, domain, rows)
            if len(items) < cfg["min_articles"]:
                return None
            quotas.check(s, "drafts")
            proposal_id = s.execute(insert(m.proposals).values(
                tenant_id=tenant_id, site_id=site_id, topic_id=_topic(s, tenant_id), profile_id=prof.id,
                opportunity=100.0, title=f"{piece} · {local.date().isoformat()}", angle="", why_now="",
                format=FORMAT, generated_by="model", status="accepted", decided_by=BY,
                basis={"evening": local.date().isoformat(), "articles": len(items)},
            ).returning(m.proposals.c.id)).scalar_one()
            s.execute(insert(m.proposal_citations).values(
                [{"tenant_id": tenant_id, "proposal_id": proposal_id, "item_id": i} for i in items]))
        else:
            items = list(s.execute(select(m.proposal_citations.c.item_id).where(
                m.proposal_citations.c.proposal_id == proposal_id)).scalars())
        sources = drafts._sources(s, items)
        sections = {r.id: (r.metrics or {}).get("section") for r in s.execute(
            select(m.source_items.c.id, m.source_items.c.metrics).where(m.source_items.c.id.in_(items)))}
    for src in sources.values():
        src.name = src.title  # the notes and the list of sources name each article
    step(N_("Il direttore AI legge i {n} articoli del giorno"), n=len(sources))
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": _prompt(profile, piece, local.date().isoformat(), sources, sections, notes)}]
    article = None
    for attempt in range(3):
        step(N_("Il direttore AI scrive il Punto della Sera") if attempt == 0
             else N_("Il direttore AI corregge il Punto della Sera"))
        with usage.tagged(db, tenant_id, "evening", site_id=site_id, ref=f"proposal:{proposal_id}"):
            res = core.chat(messages)
        try:
            article = drafts.check(drafts.parse_article(extract_json(res.content), set(sources)), sources)
        except ValueError as e:
            problem = str(e)
        else:
            if not article.flags:
                break
            problem = "; ".join(drafts._problem(f, article, sources) for f in article.flags)
        messages += [{"role": "assistant", "content": res.content},
                     {"role": "user", "content": f"Fix these problems and reply with the whole JSON again: {problem}"}]
    if article is None:
        raise CoreError("the model did not produce a valid analysis")
    article.subtitle = f"{piece}. {article.subtitle}".strip()[:300] if article.subtitle else piece
    sd = seo.parse(article.seo, article.title)
    body = drafts.render(article, sources, profile.get("language"), [])
    first = next((c.section for c in article.claims), None)
    lead = " ".join(c.text for c in article.claims if c.section == first)
    sd.checks = seo.checks(sd, article.title, lead, list(dict.fromkeys(c.section for c in article.claims)),
                           len({i for c in article.claims for i in c.sources}))
    with db.tenant(tenant_id) as s:
        quotas.lock(s, f"editor-draft-version:{proposal_id}")
        version = (s.execute(select(func.max(m.drafts.c.version)).where(
            m.drafts.c.proposal_id == proposal_id)).scalar() or 0) + 1
        did = s.execute(insert(m.drafts).values(
            tenant_id=tenant_id, proposal_id=proposal_id, site_id=site_id, version=version, title=article.title,
            subtitle=article.subtitle, body_md=body, language=profile.get("language"),
            status="needs_review" if article.flags else "draft", flags=article.flags, generated_by="model",
            seo={**sd.as_dict(), "section": section_of(cfg, lang)},
        ).returning(m.drafts.c.id)).scalar_one()
        for n, c in enumerate(article.claims):
            cid = s.execute(insert(m.draft_claims).values(
                tenant_id=tenant_id, draft_id=did, ordinal=n, section=c.section, text=c.text,
            ).returning(m.draft_claims.c.id)).scalar_one()
            s.execute(insert(m.draft_claim_sources).values(
                [{"tenant_id": tenant_id, "claim_id": cid, "item_id": i} for i in c.sources]))
        s.execute(update(m.proposals).where(m.proposals.c.id == proposal_id).values(status="drafted"))
    if any(drafts.COPIED in c.flags or style.stock(c.text, lang) for c in article.claims):
        drafts.reword(db, tenant_id, did, core, "evening")
    return did
