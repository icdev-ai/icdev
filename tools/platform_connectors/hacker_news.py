# CUI // SP-CTI
"""Hacker News platform connector via Algolia API (adapt-conn-04)."""
from __future__ import annotations

import logging
from tools.logging.icdev_logger import get_logger
import time
from tools.platform_connectors.registry import _safe_get, register

logger = get_logger(__name__)

_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
_HEALTH_URL = "https://hn.algolia.com/api/v1/search?query=test&hitsPerPage=1"


class HackerNewsAdapter:
    name = "hacker_news"

    def fetch(self, query: str, limit: int = 30, tags: str = "story", **kwargs) -> list[dict]:
        """Search HN stories matching query via Algolia.

        Args:
            query: Search terms.
            limit: Max items (capped at 1000 by Algolia).
            tags: Algolia tag filter, e.g. "story", "comment", "ask_hn", "show_hn".

        Returns:
            List of normalized dicts.
        """
        data, err = _safe_get(_SEARCH_URL, params={
            "query": query,
            "tags": tags,
            "hitsPerPage": min(limit, 100),
        })
        if err:
            logger.debug("hacker_news connector: query=%r err=%s", query, err)
            return []

        hits = (data or {}).get("hits", [])
        results = []
        for hit in hits:
            obj_id = hit.get("objectID") or ""
            results.append({
                "title": (hit.get("title") or "")[:200],
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={obj_id}",
                "author": hit.get("author") or "",
                "score": int(hit.get("points") or 0),
                "description": (hit.get("story_text") or "")[:500],
                "metadata": {
                    "platform": "hacker_news",
                    "object_id": obj_id,
                    "num_comments": int(hit.get("num_comments") or 0),
                    "created_at": hit.get("created_at") or "",
                },
            })
        return results[:limit]

    def health_check(self) -> dict:
        t0 = time.monotonic()
        _, err = _safe_get(_HEALTH_URL)
        latency = round((time.monotonic() - t0) * 1000, 1)
        if err:
            return {"status": "error", "latency_ms": latency, "error": err}
        return {"status": "ok", "latency_ms": latency}


register(HackerNewsAdapter())
