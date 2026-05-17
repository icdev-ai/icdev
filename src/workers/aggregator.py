# CUI // SP-CTI
"""Core aggregation worker — fetches OSINT, satellite, and diplomatic feeds.

Usage::

    python -m src.workers.aggregator --mock          # run with mock data
    python -m src.workers.aggregator                 # run with live clients (requires env vars)

Output:
    JSON payload written to stdout and optionally to a file.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from src.clients.diplomatic_client import DiplomaticClient
from src.clients.osint_client import OSINTClient
from src.clients.satellite_client import SatelliteClient
from src.models.feed import FeedItem
from src.services.aggregator import AggregatorService

log = logging.getLogger(__name__)


class MockOSINTClient(OSINTClient):
    """OSINT client that returns synthetic feed items for testing."""

    def __init__(self) -> None:  # noqa: D107
        super().__init__("http://mock.osint")

    def fetch_feeds(self, limit: int = 10) -> list[FeedItem]:
        now = datetime.now(timezone.utc)
        return [
            FeedItem(
                id="osint-001",
                source="osint",
                title="Suspicious network activity detected in Region A",
                content="Anomalous traffic patterns observed on public infrastructure.",
                timestamp=now,
                url="http://mock.osint/feeds/osint-001",
                metadata={"longitude": 12.5, "latitude": 41.9, "severity": "high"},
            ),
            FeedItem(
                id="osint-002",
                source="osint",
                title="New CVE published for critical service",
                content="CVE-2026-9999 details remote code execution vector.",
                timestamp=now,
                url="http://mock.osint/feeds/osint-002",
                metadata={"longitude": -77.0, "latitude": 38.9, "severity": "critical"},
            ),
        ]


class MockSatelliteClient(SatelliteClient):
    """Satellite client that returns synthetic scene items for testing."""

    def __init__(self) -> None:  # noqa: D107
        super().__init__("http://mock.satellite")

    def fetch_recent_imagery(self, region: str | None = None, limit: int = 10) -> list[FeedItem]:
        now = datetime.now(timezone.utc)
        return [
            FeedItem(
                id="sat-001",
                source="satellite",
                title="EO imagery: Coastal changes",
                content="Significant shoreline displacement observed in latest pass.",
                timestamp=now,
                url="http://mock.satellite/scenes/sat-001",
                metadata={"longitude": 34.3, "latitude": 31.2, "resolution": "0.5m"},
            ),
        ]


class MockDiplomaticClient(DiplomaticClient):
    """Diplomatic client that returns synthetic cable summaries for testing."""

    def __init__(self) -> None:  # noqa: D107
        super().__init__("http://mock.diplomatic")

    def fetch_summaries(self, limit: int = 10) -> list[FeedItem]:
        now = datetime.now(timezone.utc)
        return [
            FeedItem(
                id="dip-001",
                source="diplomatic",
                title="Diplomatic cable: trade negotiations",
                content="Bilateral talks advanced on customs harmonization.",
                timestamp=now,
                url="http://mock.diplomatic/summaries/dip-001",
                metadata={"longitude": 139.7, "latitude": 35.7, "classification": "CUI"},
            ),
        ]


def build_payload(features: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a flat JSON payload of aggregated event objects."""
    events: list[dict[str, Any]] = []
    for feature in features:
        props = feature.get("properties", {})
        events.append(
            {
                "id": props.get("id"),
                "source": props.get("source"),
                "title": props.get("title"),
                "content": props.get("content"),
                "timestamp": props.get("timestamp"),
                "url": props.get("url"),
                "metadata": props.get("metadata"),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "events": events,
    }


def run_worker(*, mock: bool = False, output_path: str | None = None) -> dict[str, Any]:
    """Execute the aggregation worker and return the JSON payload.

    Args:
        mock: Use synthetic data instead of live APIs.
        output_path: Optional file path to write JSON output.

    Returns:
        Dict payload with ``generated_at``, ``event_count``, and ``events``.
    """
    if mock:
        log.info("[worker] starting in MOCK mode")
        osint = MockOSINTClient()
        satellite = MockSatelliteClient()
        diplomatic = MockDiplomaticClient()
    else:
        log.info("[worker] starting in LIVE mode")
        osint_url = os.getenv("OSINT_BASE_URL", "http://localhost:8001")
        sat_url = os.getenv("SATELLITE_BASE_URL", "http://localhost:8002")
        dip_url = os.getenv("DIPLOMATIC_BASE_URL", "http://localhost:8003")
        osint_key = os.getenv("OSINT_API_KEY")
        sat_key = os.getenv("SATELLITE_API_KEY")
        dip_key = os.getenv("DIPLOMATIC_API_KEY")
        osint = OSINTClient(osint_url, api_key=osint_key)
        satellite = SatelliteClient(sat_url, api_key=sat_key)
        diplomatic = DiplomaticClient(dip_url, api_key=dip_key)

    service = AggregatorService(osint, satellite, diplomatic)
    geojson = service.aggregate()
    payload = build_payload(geojson.get("features", []))

    # Merge source counts from GeoJSON metadata
    payload["source_counts"] = geojson.get("properties", {}).get("sources", {})

    if output_path:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        log.info("[worker] wrote output to %s", output_path)

    return payload


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the aggregation worker."""
    parser = argparse.ArgumentParser(description="Core aggregation worker")
    parser.add_argument("--mock", action="store_true", help="Use synthetic mock data")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output JSON file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    payload = run_worker(mock=args.mock, output_path=args.output)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
