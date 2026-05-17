# CUI // SP-CTI
"""News stream fetcher — polls NewsAPI and GDELT for live open-source news signals.

Fetches recent news articles and returns raw record dicts for the stream buffer.
No persistence here: purely HTTP fetch and JSON deserialization.

NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_DEFAULT_NEWSAPI_URL = "https://newsapi.org/v2"
_DEFAULT_GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_DEFAULT_TIMEOUT_S = 10
_MAX_RESULTS = 100


class NewsStreamFetchError(Exception):
    """Raised when the news stream cannot be reached or returns invalid data."""


class NewsStreamFetcher:
    """Fetcher for open-source news live-stream signals.

    Primary backend: NewsAPI (``NEWSAPI_KEY`` env).
    Fallback backend: GDELT (no key required).

    Args:
        base_url: NewsAPI base URL (override for testing or proxies).
        api_key: NewsAPI key. Reads from ``NEWSAPI_KEY`` env if omitted.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_NEWSAPI_URL,
        api_key: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("NEWSAPI_KEY", "")
        self.timeout = timeout

    def fetch(
        self,
        query: Optional[str] = None,
        sources: Optional[str] = None,
        language: str = "en",
        since: Optional[str] = None,
        max_results: int = _MAX_RESULTS,
    ) -> List[Dict[str, Any]]:
        """Fetch recent news articles.

        Args:
            query: Free-text search query.
            sources: Comma-separated NewsAPI source IDs (e.g. ``"bbc-news,reuters"``).
            language: ISO 639-1 language code.
            since: ISO 8601 cursor; fetch only articles after this time.
            max_results: Max articles per request (NewsAPI hard cap 100).

        Returns:
            List of raw article dicts with keys ``title``, ``description``,
            ``url``, ``publishedAt``, ``source``, etc.

        Raises:
            NewsStreamFetchError: If the API is unreachable or returns unparseable data.
        """
        if not since:
            since = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S")

        # Try NewsAPI first if key is present
        if self.api_key:
            try:
                return self._fetch_newsapi(
                    query=query,
                    sources=sources,
                    language=language,
                    since=since,
                    max_results=max_results,
                )
            except NewsStreamFetchError:
                log.info("NewsAPI failed — falling back to GDELT")

        return self._fetch_gdelt(
            query=query,
            max_results=max_results,
        )

    def _fetch_newsapi(
        self,
        query: Optional[str],
        sources: Optional[str],
        language: str,
        since: str,
        max_results: int,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {
            "apiKey": self.api_key,
            "language": language,
            "pageSize": str(min(max_results, 100)),
            "from": since[:10],  # NewsAPI accepts YYYY-MM-DD
        }
        if query:
            params["q"] = query
        if sources:
            params["sources"] = sources

        query_string = urllib.parse.urlencode(params)
        full_url = f"{self.base_url}/everything?{query_string}"

        try:
            req = urllib.request.Request(
                full_url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            data: Dict[str, Any] = json.loads(raw)
        except urllib.error.HTTPError as exc:
            msg = f"NewsAPI HTTP error {exc.code}: {exc.reason}"
            log.warning(msg)
            raise NewsStreamFetchError(msg) from exc
        except urllib.error.URLError as exc:
            msg = f"NewsAPI unreachable: {exc}"
            log.warning(msg)
            raise NewsStreamFetchError(msg) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            msg = f"NewsAPI returned unparseable response: {exc}"
            log.warning(msg)
            raise NewsStreamFetchError(msg) from exc

        if data.get("status") != "ok":
            raise NewsStreamFetchError(f"NewsAPI error: {data.get('message', 'unknown')}")

        items = data.get("articles", [])
        if not isinstance(items, list):
            raise NewsStreamFetchError("NewsAPI response missing 'articles' array")

        for item in items:
            item["_stream_meta"] = {
                "source": "newsapi",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

        log.info("News stream fetcher: %d article(s) from NewsAPI", len(items))
        return items

    def _fetch_gdelt(
        self,
        query: Optional[str],
        max_results: int,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {
            "query": query or "",
            "mode": "ArtList",
            "maxrecords": str(min(max_results, 250)),
            "format": "json",
        }
        query_string = urllib.parse.urlencode(params)
        full_url = f"{_DEFAULT_GDELT_URL}?{query_string}"

        try:
            req = urllib.request.Request(
                full_url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            data: Dict[str, Any] = json.loads(raw)
        except urllib.error.HTTPError as exc:
            msg = f"GDELT HTTP error {exc.code}: {exc.reason}"
            log.warning(msg)
            raise NewsStreamFetchError(msg) from exc
        except urllib.error.URLError as exc:
            msg = f"GDELT unreachable: {exc}"
            log.warning(msg)
            raise NewsStreamFetchError(msg) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            msg = f"GDELT returned unparseable response: {exc}"
            log.warning(msg)
            raise NewsStreamFetchError(msg) from exc

        items = data.get("articles", [])
        if not isinstance(items, list):
            # GDELT sometimes returns a list directly
            if isinstance(data, list):
                items = data
            else:
                raise NewsStreamFetchError("GDELT response missing 'articles' array")

        for item in items:
            item["_stream_meta"] = {
                "source": "gdelt",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

        log.info("News stream fetcher: %d article(s) from GDELT", len(items))
        return items
