"""Automatic newsroom: a site that writes and publishes by itself, inside
the brief, the directives and the guardrails its owner set.

Every hourly round, inside the site's hours, the best proposals that pass
the guardrails are accepted and go to an `auto` job: the draft is written
with the usual checks (every sentence sourced, numbers found in the
sources, nothing copied), it gets its cover, an AI editor-in-chief reads it
against the guardrails, and only then is it approved and sent to the
site's destination. Nobody reads it first, so it always carries the AI
label (AI Act, art. 50(4)). An article the editor-in-chief stops stays a
draft, with the reasons, for a person to look at. One switch stops all.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, case, func, select, update

from . import credits, drafts, models as m, proposals, publishing, quotas, translate as tr, usage
from .core_client import CoreClient, CoreError
from .i18n import N_
from .progress import step

log = logging.getLogger(__name__)

AUTO = "redazione automatica"  # who decides, on proposals, drafts and publications
LANGS = ("it", "en", "de", "fr", "es")

DEFAULTS = {"enabled": False, "brief": "", "sections": [], "directives": "", "avoid": [], "per_day": 6,
            "min_opportunity": 40.0, "min_sources": 2, "hours": [7, 23], "target_id": None, "credit": True,
            "on_problem": "fix", "morning_share": 50,
            # Beyond the day's articles: how many it may bring forward from tomorrow (free), and how many
            # more a day it may pay with credits, for news over `extra_min_opportunity`.
            "borrow": 2, "extra_credits": 0, "extra_min_opportunity": 65.0}
# "fix": an article with problems is written again once, by the stronger model when the console sets one,
# and discarded if it still does not pass: nothing waits for a person. "hold": it waits for a person.
ON_PROBLEM = ("fix", "hold")
FIX_ROUNDS = 1

# What the AI editor-in-chief never lets through, whatever the directives say.
RULES = [
    "accuses a named person or company of a crime or wrongdoing as a fact, when the cited sources only report "
    "it as an allegation or do not attribute it to an official source",
    "names or exposes a private individual who is not a public figure, or a minor",
    "gives the reader medical, legal or financial advice (reporting what experts or authorities say is fine)",
    "insults, mocks or spreads hate against people for their origin, religion, gender, disability or orientation",
    "describes violence, suicide or self-harm in graphic detail",
    "presents a rumour or a single unconfirmed claim as an established fact",
    "has a title that promises something the article does not say",
    "joins separate news events into one article (two companies, two incidents, a figure and an unrelated "
    "deal) instead of telling one event",
    "states a number, a record or superlative (fastest, biggest, first, only) or a cause that the cited sources "
    "do not state, or a number without what it is compared to when the sources give it",
    "has a title, subtitle or opening more certain, larger or newer than the body and the sources (a forecast, "
    "an allegation or a study's result told as a fact, an old event told as new)",
    "does not give the essential information its title promises (the figure, the decision, what the study "
    "found), or tells context (a precedent, a comparison) as a second story",
    "gives a central number or a record that differs from an original document among the sources (an "
    "official statistic, a decision, a paper): the document wins over the outlets reporting it",
]

CHECK_SYSTEM = (
    "You are the editor-in-chief of a news site whose articles are published without human review. Decide "
    "whether the article below can be published as it is. Refuse it if it breaks any of these rules: "
    + "; ".join(f"({n}) {r}" for n, r in enumerate(RULES, 1))
    + ". Refuse it also if it goes against the newsroom's directives or is about a topic it must avoid. Do not "
    "refuse for style, length or a dull title. Check every number, superlative and cause against the source "
    "excerpts given after the article; a source given only by its title cannot support details. Reply with JSON "
    "only: "
    '{"publish": true|false, "problems": ["<each rule broken, one sentence each, in English>"], '
    '"section": "<the one of the site\'s sections the article belongs to, written exactly as listed, or null>"}'
)

LABELS = {
    "it": "Articolo scritto con l'intelligenza artificiale a partire dalle fonti citate, senza revisione umana.",
    "en": "This article was written by artificial intelligence from the sources cited, without human review.",
    "de": "Dieser Artikel wurde von künstlicher Intelligenz auf Grundlage der genannten Quellen verfasst, "
          "ohne menschliche Prüfung.",
    "fr": "Cet article a été rédigé par une intelligence artificielle à partir des sources citées, "
          "sans relecture humaine.",
    "es": "Este artículo fue escrito por inteligencia artificial a partir de las fuentes citadas, "
          "sin revisión humana.",
}
CREDITS = {  # after the label, when the owner keeps the credit on
    "it": "Redazione automatica di [Redazia](https://redazia.com).",
    "en": "Automatic newsroom by [Redazia](https://redazia.com).",
    "de": "Automatische Redaktion von [Redazia](https://redazia.com).",
    "fr": "Rédaction automatique par [Redazia](https://redazia.com).",
    "es": "Redacción automática de [Redazia](https://redazia.com).",
}


class _Auto(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    brief: str = Field("", max_length=4000)
    sections: list[str] = Field(default_factory=list, max_length=30)
    directives: str = Field("", max_length=4000)
    avoid: list[str] = Field(default_factory=list, max_length=100)
    per_day: int = Field(DEFAULTS["per_day"], ge=1, le=48)
    min_opportunity: float = Field(DEFAULTS["min_opportunity"], ge=0, le=100)
    min_sources: int = Field(DEFAULTS["min_sources"], ge=1, le=3)
    hours: list[int] = Field(DEFAULTS["hours"], min_length=2, max_length=2)
    target_id: int | None = None
    credit: bool = True
    on_problem: str = Field("fix", pattern="^(fix|hold)$")
    morning_share: int = Field(DEFAULTS["morning_share"], ge=0, le=100)
    borrow: int = Field(DEFAULTS["borrow"], ge=0, le=20)
    extra_credits: int = Field(DEFAULTS["extra_credits"], ge=0, le=20)
    extra_min_opportunity: float = Field(DEFAULTS["extra_min_opportunity"], ge=0, le=100)


def check_settings(values: dict) -> dict:
    """The settings as stored, or ValueError saying what is wrong."""
    try:
        cfg = _Auto.model_validate(values).model_dump()
    except ValidationError as e:
        raise ValueError("; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                                   for err in e.errors()[:3])) from None
    start, end = cfg["hours"]
    if not (0 <= start <= 23 and 1 <= end <= 24 and start != end):
        raise ValueError("hours: from 0-23 to 1-24")
    cfg["sections"] = [x.strip()[:80] for x in cfg["sections"] if x.strip()]
    cfg["avoid"] = list(dict.fromkeys(x.strip().lower()[:80] for x in cfg["avoid"] if x.strip()))
    return cfg


def settings_of(body: dict | None) -> dict:
    return {**DEFAULTS, **(((body or {}).get("settings") or {}).get("auto") or {})}


def save(s, site_id: int, values: dict) -> bool:
    """Store the automatic newsroom's settings on the profile in use and on
    the version waiting for approval; False when the site has neither."""
    from .profile import _set_settings

    return _set_settings(s, site_id, auto=check_settings(values))


def brief_lines(body: dict | None) -> list[str]:
    """The newsroom's brief and directives, for the prompts that plan and
    write its articles; set with or without the automatic mode."""
    cfg = settings_of(body)
    out = []
    if cfg["brief"]:
        out.append(f"Newsroom brief: {cfg['brief'][:2000]}")
    if cfg["sections"]:
        out.append(f"Sections of the site: {', '.join(cfg['sections'])}")
    if cfg["directives"]:
        out.append(f"Directives of the newsroom: {cfg['directives'][:2000]}")
    if cfg["avoid"]:
        out.append(f"Never write about: {', '.join(cfg['avoid'])}")
    return out


def avoided(text: str, avoid: list[str]) -> str | None:
    """The first topic to avoid the text names, or None."""
    low = (text or "").lower()
    for word in avoid:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", low):
            return word
    return None


def in_hours(cfg: dict, local: datetime) -> bool:
    start, end = cfg["hours"]
    return start <= local.hour < end if start < end else (local.hour >= start or local.hour < end)


def hours_left(cfg: dict, local: datetime) -> int:
    """Hours from this one to the end of the window, this one included."""
    n, h = 0, local.hour
    while n < 24 and in_hours(cfg, local.replace(hour=h)):
        n, h = n + 1, (h + 1) % 24
    return max(1, n)


def due_by(cfg: dict, local: datetime) -> int:
    """Articles the day should have by the end of this hour: the morning's
    share in the first hour, then the rest evenly over the window."""
    total = hours_left(cfg, local.replace(hour=cfg["hours"][0] % 24))
    done = total - hours_left(cfg, local) + 1  # hours of the window gone, this one included
    morning = cfg["per_day"] * cfg["morning_share"] / 100
    return math.ceil(morning + (cfg["per_day"] - morning) * (done - 1) / max(1, total - 1))


def made_since(s, site_id: int, since: datetime) -> tuple[int, int]:
    """Articles of the automatic newsroom since the given time, as (kept,
    tried): kept are those not discarded (published, on their way or held
    for a person), tried every article it took on, discarded ones included.
    An article written again counts once; those accepted and still being
    written count in both."""
    pr, dr = m.proposals.c, m.drafts.c
    mine = (select(dr.proposal_id, func.max(case((dr.status != "rejected", 1), else_=0)).label("kept"))
            .join(m.proposals, pr.id == dr.proposal_id)
            .where(pr.site_id == site_id, pr.decided_by == AUTO, dr.translation_of.is_(None), dr.created_at >= since)
            .group_by(dr.proposal_id).subquery())
    tried, kept = s.execute(select(func.count(), func.coalesce(func.sum(mine.c.kept), 0)).select_from(mine)).one()
    writing = s.execute(select(func.count()).select_from(m.proposals).where(
        pr.site_id == site_id, pr.decided_by == AUTO, pr.status == "accepted",
        pr.created_at >= since - timedelta(days=1))).scalar() or 0
    return int(kept) + writing, int(tried) + writing


# How many articles a day the newsroom may try, for each one it should keep:
# discarded articles are replaced, within this limit, so the cost stays known.
TRIES_PER_ARTICLE = 2


def source_count(s, proposal_id: int, need: int = 0) -> int:
    """Distinct sources telling the event a proposal is about: the news the
    article is written from (drafts.event_items), that is its main news
    and the other outlets' reports of the same event. Items of the same
    sector that are about something else do not count: counted, they made
    articles join separate stories to reach two sources. With `need`, the
    wider search runs only when the main news alone is short of it."""
    from .drafts import event_items

    pc, si = m.proposal_citations.c, m.source_items.c
    title = s.execute(select(m.proposals.c.title).where(m.proposals.c.id == proposal_id)).scalar() or ""
    cited = list(s.execute(select(pc.item_id).where(pc.proposal_id == proposal_id)).scalars())

    def count(items: list[int]) -> int:
        return s.execute(select(func.count(func.distinct(si.source_id))).where(si.id.in_(items))).scalar() or 0 if items else 0

    main, same = event_items(s, cited, title)
    n = count(main)
    if need and n >= need:
        return n
    return count(main + same)


# Below the minimum opportunity, the best proposals still go out down to
# this share of it: the minimum says what to prefer, it must not leave the
# day empty (a site with a broad profile scores lower on each single news).
OPPORTUNITY_FLOOR = 0.5


def choose(s, site_id: int, cfg: dict, now: datetime, n: int, minimum: float | None = None, dry: bool = False) -> list:
    """The best proposals of the last day and a half that pass the
    guardrails, those over the minimum opportunity first; those on a topic
    to avoid are turned down for good."""
    pr = m.proposals.c
    rows = s.execute(select(m.proposals).where(
        pr.site_id == site_id, pr.status == "proposed", pr.created_at >= now - timedelta(hours=36),
        pr.opportunity >= (cfg["min_opportunity"] * OPPORTUNITY_FLOOR if minimum is None else minimum))
        .order_by(pr.opportunity.desc(), pr.id.desc()).limit(80)).all()
    out = []
    for r in rows:
        if len(out) >= n:
            break
        word = avoided(f"{r.title} {r.angle or ''}", cfg["avoid"])
        if word and dry:
            continue
        if word:
            s.execute(update(m.proposals).where(pr.id == r.id, pr.status == "proposed")
                      .values(status="rejected", decided_by=AUTO))
            proposals.record_feedback(s, r.tenant_id, "proposal", r.id, "down", AUTO, f"topic to avoid: {word}", site_id)
            continue
        if source_count(s, r.id, cfg["min_sources"]) >= cfg["min_sources"]:
            out.append(r)
    return out


def lapse(db, tenant_id: str, site_id: int, now: datetime) -> int:
    """The proposals the newsroom did not use: past the day and a half it
    chooses from they are closed, so nobody has to decide them one by one
    (Francesco: if it is automatic, it decides)."""
    pr = m.proposals.c
    with db.tenant(tenant_id) as s:
        res = s.execute(update(m.proposals).where(
            pr.site_id == site_id, pr.status == "proposed", pr.created_at < now - timedelta(hours=36))
            .values(status="rejected", decided_by=AUTO))
    return res.rowcount or 0


def over_today(settings: dict, local) -> dict:
    """What the newsroom brought forward from tomorrow and paid with credits
    today, and what it owes from yesterday: {day, borrowed, paid, debt}.
    The record rolls over with the day, carrying yesterday's borrowed
    articles as today's debt (tomorrow it makes fewer)."""
    rec = dict(settings.get("auto_over") or {})
    today = local.date().isoformat()
    if rec.get("day") == today:
        return {"day": today, "borrowed": int(rec.get("borrowed", 0)), "paid": int(rec.get("paid", 0)),
                "debt": int(rec.get("debt", 0))}
    yesterday = (local.date() - timedelta(days=1)).isoformat()
    return {"day": today, "borrowed": 0, "paid": 0, "debt": int(rec.get("borrowed", 0)) if rec.get("day") == yesterday else 0}


def run(db, tenant_id: str, site_id: int, core: CoreClient | None, now: datetime, tz: str, enqueue) -> dict:
    """One hour of the automatic newsroom for a site: accept the articles
    of this hour and queue them. `enqueue(proposal_id)` queues one."""
    with db.tenant(tenant_id) as s:
        body = s.execute(select(m.editorial_profiles.c.body).where(
            m.editorial_profiles.c.site_id == site_id, m.editorial_profiles.c.status == "approved")).scalar()
        allowed = quotas.autopilot_allowed(s)
        month_left = quotas.remaining(s, "drafts")
    cfg = settings_of(body)
    if body is None or not cfg["enabled"]:
        return {}
    lapse(db, tenant_id, site_id, now)
    if not allowed:
        return {"waiting": "plan"}
    local = now.astimezone(ZoneInfo(tz))
    over = over_today((body.get("settings") or {}), local)
    with db.tenant(tenant_id) as s:
        have = credits.state(s)
        price = credits.price(s, "article")
    can_pay = bool(have.get("enabled")) and cfg["extra_credits"] > over["paid"] \
        and have.get("left", 0) >= price
    # The plan's articles of the month, set by the console: the automatic
    # newsroom spends credits only on the extras it was allowed (`extra_credits`),
    # so without them it waits for the next month.
    month_over = month_left is not None and month_left <= 0
    if month_over and not can_pay:
        return {"waiting": "month"}
    if not in_hours(cfg, local):
        return {"waiting": "hours"}
    since = datetime.combine(local.date(), time(), local.tzinfo).astimezone(UTC)
    paid = False
    with db.tenant(tenant_id) as s:
        kept, tried = made_since(s, site_id, since)
        # What it brought forward yesterday it does not make today.
        target = max(1, cfg["per_day"] - over["debt"])
        extra = None
        if month_over:
            extra = "paid"
        elif kept >= target:
            if over["borrowed"] < cfg["borrow"]:
                extra = "borrowed"
            elif can_pay:
                extra = "paid"
            else:
                # The day is done and no extra is allowed: how many good news stay out, counted
                # here once an hour (the Desk only shows it; counting takes a while).
                return {"done": target, "left_out": left_out(s, site_id, cfg, now)} \
                    | ({"borrowed": over["borrowed"]} if over["borrowed"] else {})
        if extra is None and tried >= cfg["per_day"] * TRIES_PER_ARTICLE:
            return {"tried": tried, "kept": kept}
        if extra is not None:
            # One at a time, only for news that is worth it, a round after the other.
            picked = choose(s, site_id, cfg, now, 1, minimum=cfg["extra_min_opportunity"])
            if not picked:  # nothing worth more than the day's articles: the day is done
                return {"done": target} | ({"borrowed": over["borrowed"]} if over["borrowed"] else {})
            want = 1
            paid = extra == "paid"
            planned_today = proposals.made_today(s, site_id, since)
        else:
            left = min(target - kept, cfg["per_day"] * TRIES_PER_ARTICLE - tried)
            # Spread over the hours left, so the day does not go in the first hour.
            want = math.ceil(left / hours_left(cfg, local))
            # The morning goes first: by the end of the first hour the day has
            # its share of articles (half, by default), the rest follows hour by
            # hour. A newsroom behind that line (a slow night, a restart) catches up.
            want = max(want, min(left, due_by(cfg, local) - kept))
            if month_left is not None:
                want = min(want, int(month_left))
            picked = choose(s, site_id, cfg, now, want)
            planned_today = proposals.made_today(s, site_id, since)
    if extra is None and len(picked) < want and core is not None and planned_today < cfg["per_day"] * 4:
        proposals.plan(db, tenant_id, site_id, core, limit=max(3, 2 * want), now=now)
        with db.tenant(tenant_id) as s:
            picked = choose(s, site_id, cfg, now, want)
    accepted = []
    for r in picked:
        if proposals.decide(db, tenant_id, r.id, True, AUTO, "automatic newsroom" if not extra else f"automatic newsroom, {extra}"):
            enqueue(r.id, True) if paid else enqueue(r.id)
            accepted.append(r.id)
    if extra and accepted:
        from .profile import _set_settings

        key = "paid" if paid else "borrowed"
        with db.tenant(tenant_id) as s:
            _set_settings(s, site_id, auto_over={**over, key: over[key] + len(accepted)})
    elif over != dict(((body.get("settings") or {}).get("auto_over")) or {}):
        from .profile import _set_settings

        with db.tenant(tenant_id) as s:
            _set_settings(s, site_id, auto_over=over)
    out = {"accepted": accepted, "wanted": want}
    return out | ({"extra": extra} if extra else {})


def sources_due(last: str | None, now: datetime) -> bool:
    """Whether the automatic newsroom looks for new sources: once a day."""
    if not last:
        return True
    try:
        return now - datetime.fromisoformat(last) >= timedelta(hours=24)
    except ValueError:
        return True


def remember(db, tenant_id: str, site_id: int, result: dict, now: datetime) -> None:
    """The last hour of the automatic newsroom, for the site page: what it
    did or what it waits for."""
    from .profile import _set_settings

    if not result:
        return
    with db.tenant(tenant_id) as s:
        _set_settings(s, site_id, auto_last={**result, "at": now.astimezone(UTC).isoformat()})


def add_label(body_md: str, language: str | None, credit: bool = True) -> str:
    """The AI label, before the sources; once."""
    lang = (language or "")[:2]
    label = LABELS.get(lang, LABELS["en"])
    if label in body_md:
        return body_md
    line = " " + CREDITS.get(lang, CREDITS["en"]) if credit else ""
    for heading in ("\n## Fonti", "\n## Sources"):
        at = body_md.rfind(heading)
        if at >= 0:
            return f"{body_md[:at]}\n_{label}{line}_\n{body_md[at:]}"
    return f"{body_md.rstrip()}\n\n_{label}{line}_\n"


def _check_validator(v) -> dict:
    if not isinstance(v, dict) or not isinstance(v.get("publish"), bool):
        raise ValueError('expected {"publish": true|false, "problems": [...]}')
    problems = [str(x)[:300] for x in v.get("problems") or [] if str(x).strip()][:6]
    if not v["publish"] and not problems:
        problems = ["refused without a reason"]
    return {"publish": v["publish"], "problems": problems, "section": str(v.get("section") or "").strip()[:80]}


EXCERPT = 2500  # characters of each cited source the editor-in-chief reads


def _excerpts(db, tenant_id: str, draft_id: int) -> list[str]:
    """What the cited sources say, in the order of their notes, so the
    editor-in-chief checks the article against them and not against itself."""
    cs, c, si = m.draft_claim_sources.c, m.draft_claims.c, m.source_items.c
    with db.tenant(tenant_id) as s:
        rows = s.execute(select(si.id, si.title, si.summary, si.content, si.published_at, m.sources.c.url.label("feed"))
                         .join(m.sources, m.sources.c.id == si.source_id)
                         .where(si.id.in_(select(cs.item_id).join(m.draft_claims, c.id == cs.claim_id)
                                          .where(c.draft_id == draft_id)))).all()
    out = ["Cited sources (title, date and what was read of each):"]
    for r in rows:
        text = " ".join(f"{r.summary or ''} {r.content or ''}".split())[:EXCERPT]
        date = r.published_at.date().isoformat() if r.published_at else "undated"
        kind = "ORIGINAL DOCUMENT, " if r.feed == drafts.DOCUMENTS_SOURCE else ""
        out.append(f"- {r.title} ({kind}{date}): {text or 'only the title was read'}")
    return out if rows else []


def editor_check(db, tenant_id: str, d, body: dict, core: CoreClient) -> dict:
    """The AI editor-in-chief's verdict on a draft."""
    lines = brief_lines(body) or ["Newsroom brief: none given"]
    prompt = "\n".join(lines + [f"Language: {d.language}", "Article:", d.body_md[:14000], *_excerpts(db, tenant_id, d.id)])
    with usage.tagged(db, tenant_id, "editor_check", site_id=d.site_id, ref=f"draft:{d.id}"):
        verdict, _calls = core.chat_json(CHECK_SYSTEM, prompt, _check_validator)
    return verdict


def target_for(s, site_id: int, cfg: dict, language: str | None, main: bool = True):
    """Where an article in this language goes: a destination set for the
    language, else, for the site's own language, one set for none. The
    one chosen in the settings wins among them."""
    t = m.publish_targets.c
    rows = s.execute(select(m.publish_targets).where(
        t.site_id == site_id, t.enabled.is_not(False), t.kind.in_(("github", "wordpress", "webhook")))
        .order_by(t.id)).all()
    exact = [r for r in rows if (r.config or {}).get("language") == language]
    fits = exact or ([r for r in rows if not (r.config or {}).get("language")] if main else [])
    return next((r for r in fits if r.id == cfg.get("target_id")), fits[0] if fits else None)


def _send(db, tenant_id: str, d, cfg: dict, main: bool, publish) -> dict:
    """Label, approve and publish a draft that passed."""
    with db.tenant(tenant_id) as s:
        s.execute(update(m.drafts).where(m.drafts.c.id == d.id).values(
            body_md=add_label(d.body_md, d.language, cfg["credit"])))
    drafts.decide(db, tenant_id, d.id, True, AUTO)
    with db.tenant(tenant_id) as s:
        target = target_for(s, d.site_id, cfg, d.language, main)
    if target is None:
        return {"approved": True}
    step(N_("Pubblicazione su {name}"), name=target.name)
    pub_id = publishing.request(db, tenant_id, d.id, target.id, "publish", AUTO)
    publishing.decide(db, tenant_id, pub_id, True, AUTO)
    publish(pub_id)
    return {"approved": True, "target": target.name, "publication_id": pub_id}


def finish(db, tenant_id: str, draft_id: int, core: CoreClient, publish, translate=lambda language, draft_id: None,
           rewrite=None, cover=None, rounds: int = 0) -> dict:
    """After the draft: the guardrails, the editor-in-chief, the cover, the
    label, the approval and the publication. `publish(pub_id)` queues the
    sending, `translate(language, draft_id)` a translation into each of the site's
    languages, `rewrite(draft_id, notes)` writes the article again with the
    stronger model and returns the new draft, `cover(draft_id)` draws it.

    With on_problem "fix" nothing waits for a person: what can be fixed is
    written again once, what cannot is discarded. The cover is drawn only
    for an article that passed everything, so a bad day costs no images."""
    with db.tenant(tenant_id) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == draft_id)).first()
        body = s.execute(select(m.editorial_profiles.c.body).where(
            m.editorial_profiles.c.site_id == d.site_id, m.editorial_profiles.c.status == "approved")).scalar()
        cfg = settings_of(body)
        cited = s.execute(select(func.count(func.distinct(m.source_items.c.source_id))).select_from(
            m.draft_claim_sources.join(m.draft_claims, m.draft_claims.c.id == m.draft_claim_sources.c.claim_id)
            .join(m.source_items, m.source_items.c.id == m.draft_claim_sources.c.item_id))
            .where(m.draft_claims.c.draft_id == draft_id)).scalar() or 0
    out = {"draft_id": draft_id, "title": d.title, "site_id": d.site_id}
    if not cfg["enabled"]:
        return _stop(db, tenant_id, d, ["the automatic newsroom was stopped"], out, discard=False)
    word = avoided(f"{d.title}\n{d.body_md}", cfg["avoid"])
    if word:  # nothing to fix: the subject itself is out
        return _stop(db, tenant_id, d, [f"topic to avoid: {word}"], out, discard=cfg["on_problem"] == "fix")
    fixable = []
    if d.status == "needs_review":
        fixable.append("some sentences failed the source checks: " + "; ".join(
            f"sentence {f.get('claim')}: {', '.join(f.get('problems') or [])}" for f in (d.flags or []))[:1500])
    if cited < cfg["min_sources"]:
        fixable.append(f"{cited} different sources cited, at least {cfg['min_sources']} needed")
    if not fixable:
        step(N_("Il caporedattore AI rilegge l'articolo"))
        try:
            verdict = editor_check(db, tenant_id, d, body, core)
        except CoreError as e:
            return _stop(db, tenant_id, d, [f"the editor-in-chief did not answer: {e}"], out, discard=False)
        if not verdict["publish"]:
            fixable += verdict["problems"]
        section = next((x for x in cfg["sections"] if x.lower() == verdict["section"].lower()), None)
        if section:  # the site files the article under it
            with db.tenant(tenant_id) as s:
                s.execute(update(m.drafts).where(m.drafts.c.id == draft_id).values(
                    seo={**(d.seo or {}), "section": section}))
    if fixable:
        if cfg["on_problem"] != "fix":
            return _stop(db, tenant_id, d, fixable, out, discard=False)
        if rewrite is None or rounds >= FIX_ROUNDS:
            return _stop(db, tenant_id, d, fixable, out, discard=True)
        step(N_("Il modello più forte riscrive l'articolo per correggere: {why}"), why="; ".join(fixable)[:300])
        _stop(db, tenant_id, d, fixable, out, discard=True)
        try:
            again = rewrite(draft_id, "; ".join(fixable)[:3000])
        except (CoreError, ValueError, quotas.QuotaExceeded) as e:
            return {**out, "discarded": fixable + [f"not written again: {e}"]}
        res = finish(db, tenant_id, again, core, publish, translate, rewrite, cover, rounds + 1)
        return {**res, "rewritten_from": draft_id}
    if cover is not None:
        step(N_("Immagine di copertina"))
        try:
            cover(draft_id)
        except Exception as e:  # an article without a cover still goes out
            log.warning("cover for draft %s failed: %s", draft_id, e)
    with db.tenant(tenant_id) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == draft_id)).first()
    res = {**out, **_send(db, tenant_id, d, cfg, True, publish)}
    with db.tenant(tenant_id) as s:
        langs = tr.missing(s, draft_id)
    for language in langs:
        translate(language, draft_id)
    if langs:
        res["translations"] = langs
    return res


def _stop(db, tenant_id: str, d, why: list[str], out: dict, discard: bool) -> dict:
    """An article that does not go out: discarded, or held for a person."""
    note = "; ".join(why)[:1000]
    with db.tenant(tenant_id) as s:
        proposals.record_feedback(s, tenant_id, "draft", d.id, "down", AUTO, note, d.site_id)
    if not discard:
        return {**out, "held": why}
    drafts.decide(db, tenant_id, d.id, False, AUTO)
    return {**out, "discarded": why}


def owns(db, tenant_id: str, draft_id: int) -> bool:
    """Whether a translation belongs to the automatic newsroom: its original
    was approved by it and the newsroom is still on. A translation asked by
    hand of such an article then goes out by itself too, like the original."""
    with db.tenant(tenant_id) as s:
        d = s.execute(select(m.drafts.c.translation_of, m.drafts.c.site_id).where(m.drafts.c.id == draft_id)).first()
        if d is None or d.translation_of is None:
            return False
        by = s.execute(select(m.drafts.c.decided_by).where(m.drafts.c.id == d.translation_of)).scalar()
        body = s.execute(select(m.editorial_profiles.c.body).where(
            m.editorial_profiles.c.site_id == d.site_id, m.editorial_profiles.c.status == "approved")).scalar()
    return by == AUTO and settings_of(body)["enabled"]


def finish_translation(db, tenant_id: str, draft_id: int, publish) -> dict:
    """A translation of an article the editor-in-chief let through: the
    same sentences and sources, so it only has to pass its own checks."""
    with db.tenant(tenant_id) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == draft_id)).first()
        body = s.execute(select(m.editorial_profiles.c.body).where(
            m.editorial_profiles.c.site_id == d.site_id, m.editorial_profiles.c.status == "approved")).scalar()
    cfg = settings_of(body)
    out = {"draft_id": draft_id, "title": d.title, "site_id": d.site_id, "language": d.language}
    if not cfg["enabled"]:
        return _stop(db, tenant_id, d, ["the automatic newsroom was stopped"], out, discard=False)
    if d.status == "needs_review":  # the original stays published; this language is skipped
        return _stop(db, tenant_id, d, ["some sentences failed the source checks"], out,
                     discard=cfg["on_problem"] == "fix")
    return {**out, **_send(db, tenant_id, d, cfg, False, publish)}


def left_out(s, site_id: int, cfg: dict, now: datetime) -> int:
    """Proposals of the last day and a half that would pass the bar of the
    extras (opportunity, distinct sources, topics) and that the day's limit
    left unused."""
    return len(choose(s, site_id, cfg, now, 50, minimum=cfg["extra_min_opportunity"], dry=True))


def today(s, site_id: int, since: datetime) -> dict:
    """The automatic newsroom's day for a site, for its page."""
    pr, dr, f = m.proposals.c, m.drafts.c, m.feedback.c
    auto_drafts = (select(dr.id, dr.title, dr.status, dr.created_at)
                   .join(m.proposals, pr.id == dr.proposal_id)
                   .where(pr.site_id == site_id, pr.decided_by == AUTO, dr.translation_of.is_(None),
                          dr.created_at >= since).order_by(dr.id.desc()))
    rows = s.execute(auto_drafts).all()
    notes = dict(s.execute(select(f.target_id, f.note).where(
        f.target == "draft", f.given_by == AUTO, f.target_id.in_([r.id for r in rows] or [0]))).all())
    pubs = dict(s.execute(select(m.publications.c.draft_id, m.publications.c.status).where(
        m.publications.c.draft_id.in_([r.id for r in rows] or [0]))).all())
    items = [{"id": r.id, "title": r.title, "status": r.status,
              "held": notes.get(r.id) if r.status != "rejected" else None,
              "discarded": notes.get(r.id) or "" if r.status == "rejected" else None,
              "publication": pubs.get(r.id)} for r in rows]
    return {"items": items, "published": sum(1 for x in items if x["publication"] == "published"),
            "held": sum(1 for x in items if x["held"]),
            "discarded": sum(1 for x in items if x["discarded"] is not None)}


BRIEF_SYSTEM = (
    "You set up the editorial profile of a news site from its owner's brief. Use only what the brief and the "
    "sections say; where they say nothing, choose what a site like this would do and keep it plain. Write the "
    "values in the site's language. Reply with JSON only: "
    '{"name": "<the site\'s name>", "summary": "<two or three sentences on what it publishes, for whom and how>", '
    '"sector": "<sector>", "audience": "<who reads it>", "tone": "<tone>", '
    '"formats": ["news|guide|review|analysis|opinion|tutorial|interview|data"], '
    '"subtopics": [{"name": "<one per section, or the main subjects when no sections are given>", '
    '"keywords": ["<lowercase keywords: 4-8 in the site language, then the same ones in English, so news in '
    'both languages are found; a site in English lists them once>"]}]}'
)


def _brief_validator(v) -> dict:
    if not isinstance(v, dict):
        raise ValueError("expected an object")
    subs = []
    for x in v.get("subtopics") or []:
        if isinstance(x, dict) and str(x.get("name") or "").strip():
            kw = [str(k).strip().lower()[:60] for k in x.get("keywords") or [] if str(k).strip()][:16]
            subs.append({"name": str(x["name"]).strip()[:80], "keywords": kw})
    if not subs or not str(v.get("sector") or "").strip():
        raise ValueError("sector and at least one subtopic are needed")
    fmts = [f for f in v.get("formats") or [] if f in proposals.FORMATS] or ["news", "analysis"]
    return {k: str(v.get(k) or "").strip()[:600] for k in ("name", "summary", "sector", "audience", "tone")} | {
        "formats": fmts, "subtopics": subs[:30]}


def profile_from_brief(db, tenant_id: str, site_id: int, language: str, core: CoreClient,
                       cfg: dict | None = None) -> int:
    """A profile written from the owner's brief, for a site with nothing
    to read yet (a new site): a version to approve like any other."""
    from . import profile as prof

    with db.tenant(tenant_id) as s:
        site = s.execute(select(m.sites.c.domain, m.sites.c.name).where(m.sites.c.id == site_id)).first()
        current = prof.approved_profile(s, site_id)
        base = dict(current.body) if current else {}
    cfg = check_settings(cfg if cfg is not None else settings_of(base))
    if not cfg["brief"].strip():
        raise ValueError("the brief is empty")
    prompt = "\n".join([f"Domain: {site.domain}", f"Language: {language}", f"Brief: {cfg['brief']}",
                        f"Sections: {', '.join(cfg['sections']) or 'not given'}",
                        f"Directives: {cfg['directives'] or 'none'}"])
    with usage.tagged(db, tenant_id, "profile", site_id=site_id, ref="brief"):
        v, _calls = core.chat_json(BRIEF_SYSTEM, prompt, _brief_validator)
    share = round(1 / len(v["subtopics"]), 3)
    body = {
        **base,
        "domain": site.domain, "method": prof.METHOD, "name": v["name"] or site.name or site.domain,
        "language": language, "languages": {language: 1},
        "summary": {"value": v["summary"] or None, "evidence": [], "by": "brief"},
        "sector": {"value": v["sector"], "evidence": [], "by": "brief"},
        "audience": {"value": v["audience"] or None, "evidence": [], "by": "brief"},
        "style": {**(base.get("style") or {}), "tone": v["tone"] or None, "formats": v["formats"], "evidence": []},
        "subtopics": [{**x, "share": share, "evidence": []} for x in v["subtopics"]],
        "gaps": [],
        "settings": {**prof.DEFAULT_SETTINGS, **(base.get("settings") or {}), "auto": cfg},
    }
    with db.tenant(tenant_id) as s:
        if site.name in (None, "", site.domain) and v["name"]:
            s.execute(update(m.sites).where(m.sites.c.id == site_id).values(name=v["name"][:200]))
        if language in LANGS:
            s.execute(update(m.sites).where(m.sites.c.id == site_id, m.sites.c.language.in_(("und", "")))
                      .values(language=language))
        pid = prof.edit(s, tenant_id, site_id, body)
        return s.execute(select(m.editorial_profiles.c.version).where(m.editorial_profiles.c.id == pid)).scalar_one()
