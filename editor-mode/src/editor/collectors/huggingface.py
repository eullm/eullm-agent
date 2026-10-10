from __future__ import annotations

import httpx

from .base import FetchResult, Item, SourceSpec, from_iso

DEFAULT_URL = "https://huggingface.co/api/models"


class HuggingFaceCollector:
    """Models from the Hugging Face Hub API.

    config: sort (default "trendingScore"), limit (default 30), search,
    pipeline_tag, author. The source url can point at /api/datasets or
    /api/spaces too.
    """

    def fetch(self, client: httpx.Client, source: SourceSpec) -> FetchResult:
        cfg = source.config
        url = source.url or DEFAULT_URL
        params = {"sort": cfg.get("sort", "trendingScore"), "limit": cfg.get("limit", 30), "full": "false"}
        for key in ("search", "pipeline_tag", "author"):
            if cfg.get(key):
                params[key] = cfg[key]
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError(f"unexpected Hugging Face response: {str(data)[:200]}")
        kind = url.rstrip("/").rsplit("/", 1)[-1]
        prefix = {"datasets": "datasets/", "spaces": "spaces/"}.get(kind, "")
        items = []
        for m in data:
            if not isinstance(m, dict):
                continue
            mid = m.get("id") or m.get("modelId")
            if not mid:
                continue
            tags = m.get("tags") or []
            items.append(
                Item(
                    url=f"https://huggingface.co/{prefix}{mid}",
                    title=mid,
                    summary=", ".join(t for t in [m.get("pipeline_tag")] + tags[:8] if t),
                    external_id=f"hf:{prefix}{mid}",
                    author=mid.split("/", 1)[0] if "/" in mid else None,
                    published_at=from_iso(m.get("createdAt")),
                    metrics={
                        "likes": m.get("likes", 0),
                        "downloads": m.get("downloads", 0),
                        "trending": m.get("trendingScore", 0),
                    },
                )
            )
        return FetchResult(items)
