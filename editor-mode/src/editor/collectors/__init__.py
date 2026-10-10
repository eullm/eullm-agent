"""Collectors turn one source into a list of items. They do HTTP and parsing
only: storage, dedup and scoring happen in editor.ingest."""

from __future__ import annotations

from .arxiv import ArxivCollector
from .base import Collector, FetchResult, Item, SourceSpec
from .github import GitHubCollector
from .hackernews import HackerNewsCollector
from .huggingface import HuggingFaceCollector
from .rss import RssCollector

COLLECTORS: dict[str, Collector] = {
    "rss": RssCollector(),
    "hackernews": HackerNewsCollector(),
    "github": GitHubCollector(),
    "huggingface": HuggingFaceCollector(),
    "arxiv": ArxivCollector(),
}

__all__ = ["COLLECTORS", "Collector", "FetchResult", "Item", "SourceSpec"]
