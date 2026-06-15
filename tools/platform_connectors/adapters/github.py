#!/usr/bin/env python3
# CUI // SP-CTI
"""GitHub REST API adapter — primary backend for GitHub platform queries.

Supports: trending repos search, issue search, repository metadata.
Auth: GITHUB_TOKEN or GH_TOKEN env vars (optional — unauthenticated for public).
Rate limit: 60 req/hr unauthenticated, 5000 req/hr authenticated.
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

GITHUB_API = "https://api.github.com"
DEFAULT_TIMEOUT = 15


class GitHubRestAdapter(PlatformConnector):
    """GitHub REST API v3 adapter."""

    name = "github_rest"
    platform = "github"
    priority = 0

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _get(self, path: str, params: dict | None = None) -> tuple[Any, str | None]:
        if not _HAS_REQUESTS:
            return None, "requests_not_installed"
        try:
            resp = _requests.get(
                f"{GITHUB_API}{path}",
                headers=self._headers(),
                params=params or {},
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code == 429 or resp.headers.get("X-RateLimit-Remaining") == "0":
                return None, "rate_limited"
            if resp.status_code == 403:
                return None, "forbidden"
            if resp.status_code == 401:
                return None, "auth_error"
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
        language = kwargs.get("language", "")

        from datetime import datetime, timedelta, timezone
        date_since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d")

        search_q = f"{query} created:>{date_since}"
        if language:
            search_q += f" language:{language}"

        data, err = self._get(
            "/search/repositories",
            {"q": search_q, "sort": "stars", "order": "desc", "per_page": min(max_results, 100)},
        )
        if err:
            result = self._err(query, err)
            result.query = query
            return result

        items = []
        for repo in (data or {}).get("items", [])[:max_results]:
            items.append({
                "id": str(repo.get("id", "")),
                "title": repo.get("full_name", ""),
                "url": repo.get("html_url", ""),
                "description": repo.get("description") or "",
                "stars": repo.get("stargazers_count", 0),
                "language": repo.get("language") or "",
                "topics": repo.get("topics", []),
                "platform": "github",
                "type": "repository",
                "created_at": repo.get("created_at", ""),
                "updated_at": repo.get("updated_at", ""),
            })

        result = FetchResult(
            platform=self.platform,
            adapter_name=self.name,
            query=query,
            items=items,
            total=len(items),
            metadata={"total_count": (data or {}).get("total_count", 0)},
        )
        return result

    def health(self) -> HealthResult:
        if not _HAS_REQUESTS:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="unreachable", message="requests_not_installed",
            )
        t0 = time.monotonic()
        data, err = self._get("/rate_limit")
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if err:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="auth_error" if err == "auth_error" else "unreachable",
                latency_ms=latency_ms, message=err,
            )
        remaining = (data or {}).get("rate", {}).get("remaining", -1)
        status = "ok" if remaining != 0 else "degraded"
        return HealthResult(
            adapter_name=self.name, platform=self.platform,
            status=status, latency_ms=latency_ms,
            message=f"rate_limit_remaining={remaining}",
        )
