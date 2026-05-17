# CUI // SP-CTI
"""Social-media stream fetcher — polls X API v2 search/recent for live signals.

Fetches recent tweets matching configured queries and returns raw record dicts
for the stream buffer. No persistence here: purely HTTP fetch and JSON
deserialization.

NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.twitter.com/2"
_DEFAULT_TIMEOUT_S = 10
_MAX_RESULTS = 100


class OSINTStreamFetchError(Exception):
    """Raised when the social-media stream cannot be reached or returns invalid data."""


class OSINTStreamFetcher:
    """Fetcher for social-media live-stream signals (X API v2).

    Args:
        base_url: X API v2 base URL (override for testing or proxies).
        bearer_token: X API Bearer token. Reads from ``X_BEARER_TOKEN`` env if omitted.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        bearer_token: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token or os.getenv("X_BEARER_TOKEN", "")
        self.timeout = timeout

    def fetch(
        self,
        query: Optional[str] = None,
        since_id: Optional[str] = None,
        max_results: int = _MAX_RESULTS,
    ) -> List[Dict[str, Any]]:
        """Fetch recent tweets matching *query*.

        Args:
            query: Search query (e.g. ``"ukraine lang:en"``).
            since_id: Tweet ID cursor; fetch only tweets newer than this.
            max_results: Max tweets per page (10–100).

        Returns:
            List of raw tweet dicts with keys ``id``, ``text``, ``author_id``,
            ``created_at``, ``geo``, etc.

        Raises:
            OSINTStreamFetchError: If the API is unreachable or returns unparseable data.
        """
        if not self.bearer_token:
            log.warning("X_BEARER_TOKEN not set — returning empty social-media stream")
            return []

        url = f"{self.base_url}/tweets/search/recent"
        params: Dict[str, str] = {
            "tweet.fields": "created_at,author_id,geo,source",
            "max_results": str(min(max(max_results, 10), 100)),
        }
        if query:
            params["query"] = query
        if since_id:
            params["since_id"] = since_id

        query_string = urllib.parse.urlencode(params)
        full_url = f"{url}?{query_string}"

        try:
            req = urllib.request.Request(
                full_url,
                headers={
                    "Authorization": f"Bearer {self.bearer_token}",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            data: Dict[str, Any] = json.loads(raw)
        except urllib.error.HTTPError as exc:
            msg = f"X API HTTP error {exc.code}: {exc.reason}"
            log.warning(msg)
            raise OSINTStreamFetchError(msg) from exc
        except urllib.error.URLError as exc:
            msg = f"X API unreachable: {exc}"
            log.warning(msg)
            raise OSINTStreamFetchError(msg) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            msg = f"X API returned unparseable response: {exc}"
            log.warning(msg)
            raise OSINTStreamFetchError(msg) from exc

        items = data.get("data", [])
        if not isinstance(items, list):
            raise OSINTStreamFetchError("X API response missing 'data' array")

        # Include meta for pagination cursor
        meta = data.get("meta", {})
        newest_id = meta.get("newest_id")
        for item in items:
            item["_stream_meta"] = {
                "source": "x_api_v2",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "newest_id": newest_id,
            }

        log.info("OSINT stream fetcher: %d tweet(s) from X API", len(items))
        return items
