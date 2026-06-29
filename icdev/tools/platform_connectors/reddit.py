# CUI // SP-CTI
"""Reddit platform connector (adapt-conn-03)."""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
import time
from tools.platform_connectors.registry import _safe_get, register

logger = get_logger(__name__)

_SEARCH_URL = "https://www.reddit.com/search.json"
_HEALTH_URL = "https://www.reddit.com/api/v1/scopes.json"


class RedditAdapter:
    name = "reddit"

    def fetch(self, query: str, limit: int = 30, sort: str = "top", time_filter: str = "month", **kwargs) -> list[dict]:
        """Search Reddit for posts matching query.

        Args:
            query: Search terms.
            limit: Max posts (capped at 100).
            sort: "top", "new", "hot", "relevance".
            time_filter: "hour", "day", "week", "month", "year", "all".

        Returns:
            List of normalized dicts.
        """
        data, err = _safe_get(_SEARCH_URL, params={
            "q": query,
            "type": "link",
            "sort": sort,
            "t": time_filter,
            "limit": min(limit, 100),
        })
        if err:
            logger.debug("reddit connector: query=%r err=%s", query, err)
            return []

        posts = (data or {}).get("data", {}).get("children", [])
        results = []
        for post in posts:
            p = post.get("data", {})
            results.append({
                "title": (p.get("title") or "")[:200],
                "url": f"https://reddit.com{p.get('permalink', '')}",
                "author": p.get("author") or "",
                "score": int(p.get("score") or 0),
                "description": (p.get("selftext") or "")[:500],
                "metadata": {
                    "platform": "reddit",
                    "subreddit": p.get("subreddit_name_prefixed") or "",
                    "num_comments": int(p.get("num_comments") or 0),
                    "upvote_ratio": float(p.get("upvote_ratio") or 0.0),
                },
            })
        return results[:limit]

    def health_check(self) -> dict:
        t0 = time.monotonic()
        _, err = _safe_get("https://www.reddit.com/.json", params={"limit": 1})
        latency = round((time.monotonic() - t0) * 1000, 1)
        if err:
            return {"status": "error", "latency_ms": latency, "error": err}
        return {"status": "ok", "latency_ms": latency}


register(RedditAdapter())
