"""What runs when. The worker calls `Runner.tick` once an hour (procrastinate
periodic task, see tasks.py); every decision about local time is taken here
with zoneinfo, so 08:00 Europe/Rome stays 08:00 across daylight saving
changes whatever the timezone of the server."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text

from . import briefing, models as m, opportunity, proposals, sources
from .ingest import collect_tenant
from .profile import analyse_site
from .scoring import score_topics
from .topics import detect_topics

log = logging.getLogger(__name__)

BRIEFING_WINDOW_HOURS = 3  # a worker that was down at 08:00 still sends until 11:00
MAINTENANCE_HOUR = 3
REVIEW_WEEKDAY, REVIEW_HOUR = 0, 4  # Monday 04:00: re-read the sites, look for new sources


def briefing_due(now_utc: datetime, tz: str, hour: int, already_sent: set[date]) -> date | None:
    """The local day whose briefing is due now, or None."""
    local = now_utc.astimezone(ZoneInfo(tz))
    if hour <= local.hour < hour + BRIEFING_WINDOW_HOURS and local.date() not in already_sent:
        return local.date()
    return None


@dataclass
class TickReport:
    tenants: dict[str, list[str]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class Runner:
    def __init__(self, db, settings, http_factory, core=None, sender=None):
        self.db = db
        self.settings = settings
        self.http_factory = http_factory  # () -> httpx.Client (through the Core in production)
        self.core = core
        self.sender = sender

    def tenants(self) -> list[str]:
        with self.db.engine.connect() as conn:
            return list(conn.execute(text("SELECT * FROM editor.active_tenants()")).scalars())

    def tick(self, now: datetime | None = None) -> TickReport:
        now = now or datetime.now(UTC)
        report = TickReport()
        for tenant in self.tenants():
            done = report.tenants.setdefault(tenant, [])
            try:
                self.tenant_tick(tenant, now, done)
            except Exception as e:  # one tenant's failure must not stop the others
                log.exception("tick failed for tenant %s", tenant)
                report.errors[tenant] = f"{type(e).__name__}: {e}"
        return report

    def tenant_settings(self, tenant: str) -> dict:
        with self.db.tenant(tenant) as s:
            row = s.execute(select(m.tenants.c.timezone, m.tenants.c.briefing_hour, m.tenants.c.language)).first()
        st = self.settings
        tz = (row.timezone if row else None) or st.timezone
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            log.warning("tenant %s has invalid timezone %r, using default %s", tenant, tz, st.timezone)
            tz = st.timezone
        return {
            "timezone": tz,
            "briefing_hour": row.briefing_hour if row and row.briefing_hour is not None else st.briefing_hour,
            "language": (row.language if row else None) or "it",
        }

    def core_for(self, tenant: str):
        return self.core.for_tenant(self.db, tenant) if self.core is not None else None

    def tenant_tick(self, tenant: str, now: datetime, done: list[str]) -> None:
        conf = self.tenant_settings(tenant)
        tz = conf["timezone"]
        local = now.astimezone(ZoneInfo(tz))
        core = self.core_for(tenant)
        with self.http_factory() as client:
            collect_tenant(self.db, tenant, client)
            detect_topics(self.db, tenant, core)
            score_topics(self.db, tenant, now=now)
            done.append("collect")
            if local.hour == MAINTENANCE_HOUR:
                sources.maintain(self.db, tenant, client, now=now)
                done.append("maintain")
            if local.weekday() == REVIEW_WEEKDAY and local.hour == REVIEW_HOUR:
                self.review(tenant, client)
                done.append("review")
            with self.db.tenant(tenant) as s:
                sent = set(s.execute(select(m.briefings.c.briefing_date).where(
                    m.briefings.c.briefing_date >= local.date().replace(day=1))).scalars())
            day = briefing_due(now, tz, conf["briefing_hour"], sent)
            if day is not None:
                self.morning(tenant, day, now, conf["language"])
                done.append(f"briefing {day.isoformat()}")

    def review(self, tenant: str, client) -> None:
        from .site import SiteCrawler

        with self.db.tenant(tenant) as s:
            sites = s.execute(select(m.sites.c.id, m.sites.c.domain)).all()
        core = self.core_for(tenant)
        for site in sites:
            analyse_site(self.db, tenant, site.domain, SiteCrawler(client), core)
            sources.discover(self.db, tenant, site.id, client, core)

    def morning(self, tenant: str, day: date, now: datetime, language: str = "it") -> int:
        core = self.core_for(tenant)
        with self.db.tenant(tenant) as s:
            site_ids = list(s.execute(select(m.sites.c.id)).scalars())
        for site_id in site_ids:
            ops = opportunity.score_site(self.db, tenant, site_id, now=now)
            proposals.propose(self.db, tenant, site_id, ops, core)
        bid = briefing.build(self.db, tenant, day, language)
        if self.sender is not None:
            self.sender.send(self.db, tenant, bid)
        return bid
