from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx


@dataclass
class SourceSpec:
    kind: str
    url: str
    config: dict = field(default_factory=dict)
    etag: str | None = None
    last_modified: str | None = None


@dataclass
class Item:
    url: str
    title: str
    summary: str = ""
    external_id: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    # Popularity counters at fetch time (points, stars, likes, downloads...).
    metrics: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    items: list[Item]
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None


class Collector(Protocol):
    def fetch(self, client: httpx.Client, source: SourceSpec) -> FetchResult: ...


def from_struct_time(t) -> datetime | None:
    """feedparser's *_parsed fields are UTC struct_time."""
    if not t:
        return None
    return datetime.fromtimestamp(calendar.timegm(t), tz=UTC)


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
