from __future__ import annotations

import feedparser
import httpx

from ..normalize import clean_text
from .base import FetchResult, Item, SourceSpec, from_struct_time

DEFAULT_URL = "https://export.arxiv.org/api/query"


class ArxivCollector:
    """Recent papers from the arXiv API (Atom).

    config: query (arXiv search_query, e.g. "cat:cs.CL OR cat:cs.LG"),
    max_results (default 50). arXiv asks for at most one request every
    three seconds: one source makes one request per run.
    """

    def fetch(self, client: httpx.Client, source: SourceSpec) -> FetchResult:
        cfg = source.config
        params = {
            "search_query": cfg.get("query", "cat:cs.CL"),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": cfg.get("max_results", 50),
        }
        r = client.get(source.url or DEFAULT_URL, params=params)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        items = []
        for e in feed.entries:
            link = e.get("link") or e.get("id")
            title = clean_text(e.get("title"))
            if not link or not title:
                continue
            authors = [a.get("name") for a in e.get("authors", []) if a.get("name")]
            items.append(
                Item(
                    url=link,
                    title=title,
                    summary=clean_text(e.get("summary"))[:2000],
                    external_id=f"arxiv:{e.get('id', link).rsplit('/', 1)[-1]}",
                    author=", ".join(authors[:5]) or None,
                    published_at=from_struct_time(e.get("published_parsed")),
                    metrics={"categories": [t.get("term") for t in e.get("tags", [])]},
                )
            )
        return FetchResult(items)
