# CUI // SP-CTI
"""ICDEV™ IL5 Fetcher — retrieves raw IL5 publication feed records.

Fetches from the configured IL5 publication feed endpoint and returns
raw record dicts for the ingestion pipeline adapter layer. No persistence
or display logic here: this module is purely responsible for HTTP fetch
and JSON deserialization.

NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

log = logging.getLogger(__name__)

_DEFAULT_FEED_URL = "http://localhost:5050/api/il5/feed"

def _load_timeout() -> int:
    """Read timeout.read from args/http_client.yaml; fall back to 5 s."""
    cfg_path = Path(__file__).resolve().parents[3] / "args" / "http_client.yaml"
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return int(data.get("timeout", {}).get("read", 5))
    return 5

_DEFAULT_TIMEOUT_S = _load_timeout()


class IL5FetchError(Exception):
    """Raised when the IL5 feed cannot be reached or returns invalid data."""


class IL5Fetcher:
    """Fetcher for IL5 publication feed records.

    Args:
        feed_url: URL of the IL5 publication feed.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        feed_url: str = _DEFAULT_FEED_URL,
        timeout: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.feed_url = feed_url
        self.timeout = timeout

    def fetch(
        self,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Fetch raw IL5 records from the publication feed.

        Args:
            since: ISO 8601 cursor; fetch only records after this time.
            limit: Max records to request from the feed.

        Returns:
            List of raw IL5 record dicts.

        Raises:
            IL5FetchError: If the feed is unreachable or returns unparseable data.
        """
        url = self.feed_url
        params = []
        if since:
            params.append(f"since={urllib.parse.quote(since)}")
        if limit:
            params.append(f"limit={limit}")
        if params:
            url += "?" + "&".join(params)

        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            items: List[Dict[str, Any]] = json.loads(raw)
        except urllib.error.URLError as exc:
            msg = f"IL5 feed unreachable ({self.feed_url}): {exc}"
            log.warning(msg)
            raise IL5FetchError(msg) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            msg = f"IL5 feed returned unparseable response: {exc}"
            log.warning(msg)
            raise IL5FetchError(msg) from exc

        log.info("IL5 fetcher: %d raw records from %s", len(items), self.feed_url)
        return items
