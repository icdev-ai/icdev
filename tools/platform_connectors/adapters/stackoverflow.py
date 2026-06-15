#!/usr/bin/env python3
# CUI // SP-CTI
"""Stack Overflow API v2.3 adapter — zero-config (key optional, higher quota with key).

Searches questions by keyword with answer/vote filters.
Rate limit: 300 req/day without key, 10,000/day with key.
"""

import os
import time
from typing import Any

from tools.platform_connectors.base import FetchResult, HealthResult, PlatformConnector

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

SO_API = "https://api.stackexchange.com/2.3"
DEFAULT_TIMEOUT = 15


class StackOverflowAdapter(PlatformConnector):
    """Stack Overflow API v2.3 adapter."""

    name = "stackoverflow_v2"
    platform = "stackoverflow"
    priority = 0

    def _get(self, path: str, params: dict | None = None) -> tuple[Any, str | None]:
        if not _HAS_REQUESTS:
            return None, "requests_not_installed"
        p = params or {}
        key = os.environ.get("STACKOVERFLOW_KEY") or os.environ.get("SO_API_KEY")
        if key:
            p["key"] = key
        try:
            resp = _requests.get(f"{SO_API}{path}", params=p, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 429:
                return None, "rate_limited"
            if resp.status_code == 400:
                body = resp.json()
                return None, body.get("error_message", "bad_request")
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
        since_days = int(kwargs.get("since_days", 30))
        tags = kwargs.get("tags", "")

        params: dict = {
            "intitle": query,
            "order": "desc",
            "sort": "votes",
            "site": "stackoverflow",
            "pagesize": min(max_results, 100),
            "fromdate": int(time.time()) - since_days * 86400,
            "filter": "!9_bDE(fI5",
        }
        if tags:
            params["tagged"] = tags if isinstance(tags, str) else ";".join(tags)

        data, err = self._get("/questions", params)
        if err:
            result = self._err(query, err)
            result.query = query
            return result

        items = []
        for q in (data or {}).get("items", [])[:max_results]:
            items.append({
                "id": str(q.get("question_id", "")),
                "title": q.get("title", ""),
                "url": q.get("link", ""),
                "description": "",
                "score": q.get("score", 0),
                "answers": q.get("answer_count", 0),
                "tags": q.get("tags", []),
                "is_answered": q.get("is_answered", False),
                "platform": "stackoverflow",
                "type": "question",
                "created_at": str(q.get("creation_date", "")),
            })

        return FetchResult(
            platform=self.platform,
            adapter_name=self.name,
            query=query,
            items=items,
            total=len(items),
            metadata={"quota_remaining": (data or {}).get("quota_remaining", -1)},
        )

    def health(self) -> HealthResult:
        if not _HAS_REQUESTS:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="unreachable", message="requests_not_installed",
            )
        t0 = time.monotonic()
        data, err = self._get("/info", {"site": "stackoverflow"})
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if err:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="unreachable", latency_ms=latency_ms, message=err,
            )
        quota = (data or {}).get("quota_remaining", -1)
        status = "degraded" if quota == 0 else "ok"
        return HealthResult(
            adapter_name=self.name, platform=self.platform,
            status=status, latency_ms=latency_ms,
            message=f"quota_remaining={quota}",
        )
