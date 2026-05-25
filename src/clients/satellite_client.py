# CUI // SP-CTI
"""Satellite REST client — fetch EO imagery metadata and scene details."""

from __future__ import annotations

import requests
from typing import Any

from src.clients.http_client import BaseHTTPClient
from src.models.feed import FeedItem


class SatelliteError(Exception):
    """Raised on unrecoverable satellite API errors or unexpected response formats."""


class SatelliteClient(BaseHTTPClient):
    """Client for the satellite imagery API."""

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
            raise SatelliteError(f"Satellite request failed: {exc}") from exc
        data = resp.json()
        if not isinstance(data, dict):
            raise SatelliteError("Satellite response is not a JSON object")
        return data

    def fetch_recent_imagery(self, region: str | None = None, limit: int = 10) -> list[FeedItem]:
        params: dict[str, Any] = {"limit": limit}
        if region:
            params["region"] = region
        data = self._request("GET", "/scenes", params=params)
        items = data.get("items")
        if not isinstance(items, list):
            raise SatelliteError("Satellite response missing 'items' array")
        return [FeedItem.from_json(i) for i in items]

    def fetch_scene(self, scene_id: str) -> FeedItem:
        data = self._request("GET", f"/scenes/{scene_id}")
        if not isinstance(data, dict) or "id" not in data:
            raise SatelliteError("Satellite scene response missing expected fields")
        return FeedItem.from_json(data)
