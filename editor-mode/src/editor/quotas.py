"""Per-tenant limits. The plan lives on the tenant row (set by the operator
with `editor tenant-plan`); usage is counted here, with days and months in
the tenant's time zone. A missing limit means unlimited."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, case, func, select, text

from . import models as m
from . import repo
from .db import is_sqlite


class QuotaExceeded(RuntimeError):
    pass


# After the trial (or a paid period) a newsroom keeps getting this many
# proposals a day per site, and nothing more, until it pays or stops.
PROPOSALS_ONLY_PER_DAY = 2


def proposals_only(s) -> bool:
    """The trial or the paid period is over: proposals only, no drafts."""
    return bool(s.execute(select(m.tenants.c.proposals_only)).scalar())


def autopilot_allowed(s) -> bool:
    """The plan includes the automatic newsroom and the newsroom is paid for."""
    row = s.execute(select(m.tenants.c.autopilot, m.tenants.c.proposals_only)).first()
    return row is None or (row.autopilot is not False and row.proposals_only is None)


LIMITS = {
    "sites": ("max_sites", "sites"),
    "sources": ("max_active_sources", "active_sources"),
    "llm_cost": ("max_llm_cost_month", "llm_cost_month"),
    "drafts": ("max_drafts_month", "drafts_month"),
}


@dataclass
class Usage:
    plan: dict
    used: dict

    def remaining(self, what: str) -> float | None:
        limit_col, used_col = LIMITS[what]
        limit = self.plan.get(limit_col)
        return None if limit is None else max(0, limit - (self.used.get(used_col) or 0))


def lock(s, key: str) -> None:
    """Serialise, until this transaction ends, everything that takes the same key.
    On SQLite every transaction already holds the database's write lock."""
    if not is_sqlite(s):
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


def _zone(name: str | None) -> ZoneInfo:
    """A newsroom's timezone; one the system does not know counts as UTC
    instead of breaking every page that lists newsrooms."""
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _used(s, t, now: datetime | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(_zone(t.timezone))
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    tid = t.tenant_id

    def one(q):
        return s.execute(q).scalar() or 0

    u = m.llm_usage
    return {
        "tenant_id": tid,
        "sites": one(select(func.count()).select_from(m.sites).where(m.sites.c.tenant_id == tid)),
        "active_sources": one(select(func.count()).select_from(m.sources).where(
            m.sources.c.tenant_id == tid, m.sources.c.status == "active", repo.own())),
        # News of the day from the sources the newsroom follows, in the archive: a number to read,
        # not a limit (the archive reads once for every newsroom, whatever any of them follows).
        "items_today": one(select(func.count()).select_from(m.source_items).where(
            m.source_items.c.fetched_at >= day, repo.followed_by())),
        "llm_cost_month": one(select(func.coalesce(func.sum(u.c.cost), 0)).where(
            u.c.tenant_id == tid, u.c.at >= month)),
        "llm_tokens_month": one(select(func.coalesce(func.sum(u.c.input_tokens), 0)
                                       + func.coalesce(func.sum(u.c.output_tokens), 0)).where(
            u.c.tenant_id == tid, u.c.at >= month)),
        "drafts_month": one(articles(tid, month)),
    }


# Who discards an article on its own: the automatic newsroom (autopilot.AUTO).
AUTO = "redazione automatica"


def articles(tid: str, since: datetime):
    """The plan's articles since `since` (Francesco 2026-11-07: only the
    articles that come out, each in all the languages of the plan): one per
    proposal written, whatever its versions (the editor-in-chief's
    rewrites), not its translations, and not the ones the automatic
    newsroom discarded. One a person discarded counts: it was read."""
    d = m.drafts.c
    key = func.coalesce(d.proposal_id, -d.id)
    kept = case((and_(d.status == "rejected", d.decided_by == AUTO), 0), else_=1)
    per = (select(key.label("k")).where(d.tenant_id == tid, d.translation_of.is_(None), d.created_at >= since)
           .group_by(key).having(func.max(kept) == 1).subquery())
    return select(func.count()).select_from(per)


def usage(s, hold: bool = False) -> Usage | None:
    """The tenant's plan and usage. With ``hold``, other quota checks of the
    tenant wait until this transaction ends, so a check and the insert that
    follows it are not interleaved with another worker's (read-then-act)."""
    t = s.execute(select(m.tenants)).first()
    if t is None:
        return None
    if hold:
        lock(s, f"editor-quota:{t.tenant_id}")
    return Usage(dict(t._mapping), _used(s, t))


def check(s, what: str, adding: float = 1, pay: str | None = None, again: bool = False) -> bool:
    """Raise QuotaExceeded when `adding` more would pass the tenant's limit.
    The check holds the tenant's quota lock until the transaction ends: do
    the insert it guards in the same transaction.

    With `pay` (a credits action: article, translation) a newsroom past the
    limit can still go on with its credits: the check then returns True
    and the caller pays with credits.spend after its insert, in the same
    transaction. Without enough credits it raises credits.NoCredits."""
    u = usage(s, hold=True)
    if u is None:
        return False
    if what == "drafts" and u.plan.get("proposals_only"):
        raise QuotaExceeded("the trial is over: proposals only until a plan is chosen")
    if again:  # an article written again (a rewrite, a version): it is the same article of the plan
        return False
    left = u.remaining(what)
    if left is not None and (left < adding or left <= 0):
        limit_col, used_col = LIMITS[what]
        if pay is not None and u.plan.get("credits_month") is not None:
            from . import credits

            credits.need(s, credits.price(s, pay))
            return True
        raise QuotaExceeded(f"{what} limit reached ({u.used.get(used_col)} of {u.plan[limit_col]}, plan {u.plan.get('plan')})")
    return False


def translation(s, included: bool, pay: bool = True) -> bool:
    """Before a translation: none once the trial is over. Into one of the
    site's languages it is part of the article; into another language it
    is paid with credits (when the newsroom has them). Returns whether to
    pay; holds the quota lock like check."""
    u = usage(s, hold=True)
    if u is None:
        return False
    if u.plan.get("proposals_only"):
        raise QuotaExceeded("the trial is over: proposals only until a plan is chosen")
    if included or not pay or u.plan.get("credits_month") is None:
        return False
    from . import credits

    credits.need(s, credits.price(s, "translation"))
    return True


def remaining(s, what: str) -> float | None:
    u = usage(s, hold=True)
    return None if u is None else u.remaining(what)
