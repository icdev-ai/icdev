# CUI // SP-CTI
"""Aggregator service — merges OSINT, satellite, and diplomatic feeds into GeoJSON."""

from __future__ import annotations

import json
from typing import Any

from src.clients.diplomatic_client import DiplomaticClient
from src.clients.osint_client import OSINTClient
from src.clients.satellite_client import SatelliteClient
from src.models.feed import FeedItem


class AggregatorError(Exception):
    """Raised when aggregation fails due to an upstream client error."""


class AggregatorService:
    """Combines intelligence feeds from three clients into a unified GeoJSON FeatureCollection."""

    def __init__(
        self,
        osint_client: OSINTClient,
        satellite_client: SatelliteClient,
        diplomatic_client: DiplomaticClient,
    ) -> None:
        self.osint = osint_client
        self.satellite = satellite_client
        self.diplomatic = diplomatic_client

    @staticmethod
    def _feed_to_feature(item: FeedItem) -> dict[str, Any]:
        """Convert a single FeedItem into a GeoJSON Point Feature."""
        meta = item.metadata or {}
        lon = float(meta.get("longitude", 0.0))
        lat = float(meta.get("latitude", 0.0))
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat],
            },
            "properties": {
                "id": item.id,
                "source": item.source,
                "title": item.title,
                "content": item.content,
                "timestamp": item.timestamp.isoformat(),
                "url": item.url,
                "metadata": meta,
            },
        }

    def aggregate(self) -> dict[str, Any]:
        """Fetch data from all three clients and return a GeoJSON FeatureCollection."""
        try:
            osint_items = self.osint.fetch_feeds()
        except Exception as exc:
            raise AggregatorError(f"OSINT fetch failed: {exc}") from exc

        try:
            sat_items = self.satellite.fetch_recent_imagery()
        except Exception as exc:
            raise AggregatorError(f"Satellite fetch failed: {exc}") from exc

        try:
            dip_items = self.diplomatic.fetch_summaries()
        except Exception as exc:
            raise AggregatorError(f"Diplomatic fetch failed: {exc}") from exc

        features: list[dict[str, Any]] = []
        for item in osint_items + sat_items + dip_items:
            features.append(self._feed_to_feature(item))

        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "sources": {
                    "osint": len(osint_items),
                    "satellite": len(sat_items),
                    "diplomatic": len(dip_items),
                }
            },
        }

    def to_geojson_string(self) -> str:
        """Run aggregation and return the result as a compact JSON string."""
        return json.dumps(self.aggregate(), separators=(",", ":"))
