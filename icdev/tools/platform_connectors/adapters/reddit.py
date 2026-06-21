#!/usr/bin/env python3
# CUI // SP-CTI
"""Reddit JSON API adapter — zero-config (public JSON endpoint, no OAuth needed).

Uses Reddit's undocumented but stable public JSON API (r/<subreddit>/search.json).
Auth: no key required for read-only public posts.
Rate limit: ~60 req/min with proper User-Agent.
"""

import time
from typing import Any

from tools.platform_connectors.base import FetchResult, HealthResult, PlatformConnector

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

REDDIT_API = "https://www.reddit.com"
USER_AGENT = "ICDEV-PlatformConnector/1.0 (research; +https://icdev.app)"
DEFAULT_TIMEOUT = 15


class RedditJsonAdapter(PlatformConnector):
    """Reddit public JSON API adapter (no auth required)."""

    name = "reddit_json"
    platform = "reddit"
    priority = 0

    def _get(self, path: str, params: dict | None = None) -> tuple[Any, str | None]:
        if not _HAS_REQUESTS:
            return None, "requests_not_installed"
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            resp = _requests.get(
                f"{REDDIT_API}{path}",
                headers=headers,
                params=params or {},
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code == 429:
                return None, "rate_limited"
            if resp.status_code == 403:
                return None, "forbidden"
            resp.raise_for_status()
            return resp.json(), None
        except _requests.exceptions.Timeout:
            return None, "timeout"
        except _requests.exceptions.ConnectionError:
            return None, "connection_error"
        except Exception as exc:
            return None, str(exc)

    def fetch(self, query: str, **kwargs: Any) -> FetchResult:
        max_results = int(kwargs.get("max_results", 20))
        subreddits = kwargs.get("subreddits", ["programming", "MachineLearning", "devops"])
        if isinstance(subreddits, str):
            subreddits = [subreddits]
        time_filter = kwargs.get("time_filter", "week")

        items = []
        for sub in subreddits:
            if len(items) >= max_results:
                break
            data, err = self._get(
                f"/r/{sub}/search.json",
                {"q": query, "restrict_sr": "true", "sort": "relevance",
                 "t": time_filter, "limit": min(25, max_results - len(items))},
            )
            if err:
                continue
            for post in (data or {}).get("data", {}).get("children", []):
                p = post.get("data", {})
                items.append({
                    "id": p.get("id", ""),
                    "title": p.get("title", ""),
                    "url": p.get("url") or f"https://reddit.com{p.get('permalink', '')}",
                    "description": (p.get("selftext") or "")[:500],
                    "score": p.get("score", 0),
                    "comments": p.get("num_comments", 0),
                    "subreddit": p.get("subreddit", sub),
                    "author": p.get("author", ""),
                    "platform": "reddit",
                    "type": "post",
                    "created_at": str(int(p.get("created_utc", 0))),
                })
                if len(items) >= max_results:
                    break

        return FetchResult(
            platform=self.platform,
            adapter_name=self.name,
            query=query,
            items=items,
            total=len(items),
            metadata={"subreddits_searched": subreddits},
        )

    def health(self) -> HealthResult:
        if not _HAS_REQUESTS:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="unreachable", message="requests_not_installed",
            )
        t0 = time.monotonic()
        data, err = self._get("/r/programming/about.json")
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if err:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="unreachable", latency_ms=latency_ms, message=err,
            )
        return HealthResult(
            adapter_name=self.name, platform=self.platform,
            status="ok", latency_ms=latency_ms, message="public_json_ok",
        )
