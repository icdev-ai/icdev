#!/usr/bin/env python3
# CUI // SP-CTI
"""Hacker News Firebase API adapter — zero-config, no auth required.

Uses the official public Firebase API: https://hacker-news.firebaseio.com/v0/
Supported: top/new/best stories search by keyword filtering.
Rate limit: generous public API — no key needed.
"""

import time
from typing import Any

from tools.platform_connectors.base import FetchResult, HealthResult, PlatformConnector

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

HN_API = "https://hacker-news.firebaseio.com/v0"
HN_ALGOLIA = "https://hn.algolia.com/api/v1"
DEFAULT_TIMEOUT = 15


class HackerNewsAdapter(PlatformConnector):
    """Hacker News adapter — uses Algolia HN Search API (zero-config)."""

    name = "hackernews_algolia"
    platform = "hackernews"
    priority = 0

    def _get(self, url: str, params: dict | None = None) -> tuple[Any, str | None]:
        if not _HAS_REQUESTS:
            return None, "requests_not_installed"
        try:
            resp = _requests.get(url, params=params or {}, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 429:
                return None, "rate_limited"
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
        since_days = int(kwargs.get("since_days", 7))

        params = {
            "query": query,
            "tags": "story",
            "hitsPerPage": min(max_results, 50),
            "numericFilters": f"created_at_i>{int(time.time()) - since_days * 86400}",
        }
        data, err = self._get(f"{HN_ALGOLIA}/search", params)
        if err:
            result = self._err(query, err)
            result.query = query
            return result

        items = []
        for hit in (data or {}).get("hits", [])[:max_results]:
            items.append({
                "id": str(hit.get("objectID", "")),
                "title": hit.get("title", ""),
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                "description": hit.get("story_text") or "",
                "score": hit.get("points", 0),
                "comments": hit.get("num_comments", 0),
                "author": hit.get("author", ""),
                "platform": "hackernews",
                "type": "story",
                "created_at": hit.get("created_at", ""),
            })

        return FetchResult(
            platform=self.platform,
            adapter_name=self.name,
            query=query,
            items=items,
            total=len(items),
            metadata={"nb_hits": (data or {}).get("nbHits", 0)},
        )

    def health(self) -> HealthResult:
        if not _HAS_REQUESTS:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="unreachable", message="requests_not_installed",
            )
        t0 = time.monotonic()
        data, err = self._get(f"{HN_API}/topstories.json", {"limitToFirst": "1", "orderBy": '"$key"'})
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if err:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="unreachable", latency_ms=latency_ms, message=err,
            )
        return HealthResult(
            adapter_name=self.name, platform=self.platform,
            status="ok", latency_ms=latency_ms, message="firebase_ok",
        )
