# CUI // SP-CTI
"""GitHub platform connector (adapt-conn-02)."""
from __future__ import annotations

import logging
from tools.logging.icdev_logger import get_logger
import time
from tools.platform_connectors.registry import _safe_get, register

logger = get_logger(__name__)

_REPOS_URL = "https://api.github.com/search/repositories"
_ISSUES_URL = "https://api.github.com/search/issues"
_TOPICS_URL = "https://api.github.com/search/topics"
_HEALTH_URL = "https://api.github.com/rate_limit"


class GitHubAdapter:
    name = "github"

    def fetch(self, query: str, limit: int = 30, type: str = "repos", **kwargs) -> list[dict]:
        """Fetch GitHub repos or issues matching query.

        Args:
            query: Search terms.
            limit: Max items to return (capped at 100 by GitHub).
            type: "repos" (default) or "issues".

        Returns:
            List of normalized dicts with keys: title, url, author, score, description, metadata.
        """
        url = _REPOS_URL if type != "issues" else _ISSUES_URL
        accept = "application/vnd.github.mercy-preview+json" if type == "topics" else "application/vnd.github+json"
        data, err = _safe_get(url, params={
            "q": query,
            "sort": "stars" if type == "repos" else "reactions",
            "order": "desc",
            "per_page": min(limit, 100),
        }, headers={"Accept": accept})

        if err:
            logger.debug("github connector: %s query=%r err=%s", type, query, err)
            return []

        items = (data or {}).get("items", [])
        results = []
        for item in items:
            if type == "repos":
                results.append({
                    "title": item.get("full_name") or item.get("name") or "",
                    "url": item.get("html_url") or "",
                    "author": (item.get("owner") or {}).get("login") or "",
                    "score": int(item.get("stargazers_count") or 0),
                    "description": (item.get("description") or "")[:500],
                    "metadata": {
                        "platform": "github",
                        "type": "repo",
                        "topics": item.get("topics") or [],
                        "forks": item.get("forks_count") or 0,
                        "language": item.get("language") or "",
                    },
                })
            else:
                results.append({
                    "title": item.get("title") or "",
                    "url": item.get("html_url") or "",
                    "author": (item.get("user") or {}).get("login") or "",
                    "score": int(item.get("reactions", {}).get("+1") or 0),
                    "description": (item.get("body") or "")[:500],
                    "metadata": {
                        "platform": "github",
                        "type": "issue",
                        "state": item.get("state"),
                        "labels": [l.get("name") for l in (item.get("labels") or [])],
                    },
                })
        return results[:limit]

    def health_check(self) -> dict:
        t0 = time.monotonic()
        data, err = _safe_get(_HEALTH_URL)
        latency = round((time.monotonic() - t0) * 1000, 1)
        if err:
            return {"status": "error", "latency_ms": latency, "error": err}
        remaining = (data or {}).get("rate", {}).get("remaining", -1)
        return {"status": "ok", "latency_ms": latency, "rate_remaining": remaining}


# Auto-register on import
register(GitHubAdapter())
