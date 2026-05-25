# CUI // SP-CTI
"""OSINT REST client — fetch open-source intelligence feeds and indicators."""

from __future__ import annotations

import requests
from typing import Any

from src.clients.http_client import BaseHTTPClient
from src.models.feed import FeedItem


class OSINTError(Exception):
    """Raised on unrecoverable OSINT API errors or unexpected response formats."""


class OSINTClient(BaseHTTPClient):
    """Client for the OSINT feed API."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: tuple[int, int] | int | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        kwargs.setdefault("timeout", self.timeout)
        try:
            resp = requests.request(method, url, headers=headers, **kwargs)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise OSINTError(f"OSINT request failed: {exc}") from exc
        data = resp.json()
        if not isinstance(data, dict):
            raise OSINTError("OSINT response is not a JSON object")
        return data

    def fetch_feeds(self, limit: int = 10) -> list[FeedItem]:
        data = self._request("GET", "/feeds", params={"limit": limit})
        items = data.get("items")
        if not isinstance(items, list):
            raise OSINTError("OSINT response missing 'items' array")
        return [FeedItem.from_json(i) for i in items]

    def fetch_indicator(self, indicator_id: str) -> FeedItem:
        data = self._request("GET", f"/indicators/{indicator_id}")
        if not isinstance(data, dict) or "id" not in data:
            raise OSINTError("OSINT indicator response missing expected fields")
        return FeedItem.from_json(data)
