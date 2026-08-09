#!/usr/bin/env python3
"""ACLED DataBridge Connector — Armed Conflict Location & Event Data.

Connects to the ACLED API for conflict event data:
  - Armed conflict events by date, country, and event type
  - Actor and location details
  - Incremental ingestion via event_date

Auth: ACLED_API_KEY environment variable (Authorization header).
Base URL: https://api.acleddata.com

Usage (via DataBridge registry):
    from tools.databridge.registry import get_connector_instance
    conn = get_connector_instance("acled")
    conn.connect({"api_key": "..."})
    result = conn.read(ConnectorRequest(
        table_name="events",
        incremental_key="event_date",
        incremental_value="2024-01-01",
        limit=100,
    ))

Usage (standalone CLI):
    python tools/databridge/connectors/acled_connector.py --health --json
    python tools/databridge/connectors/acled_connector.py --table events --limit 10 --json
    python tools/databridge/connectors/acled_connector.py --table events --since 2024-01-01 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.databridge.connector import (  # noqa: E402
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
)
from tools.databridge.connectors.saas_base import SaaSBaseConnector  # noqa: E402
from tools.databridge.registry import register_connector  # noqa: E402

logger = get_logger("databridge.acled")

ACLED_DEFAULT_BASE_URL = "https://api.acleddata.com"


@register_connector
class ACLEDConnector(SaaSBaseConnector):
    """ACLED API connector for armed conflict event data.

    Normalizes raw ACLED records with:
      - headline = notes
      - geo_hint  = country + " — " + location
      - signal_date = event_date
    """

    _connector_name = "acled"
    _default_base_url = ACLED_DEFAULT_BASE_URL
    _endpoints = {
        "events": "/api/data/acled-v1",
    }

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,
            supports_schema_inference=True,
            max_batch_size=500,
            supported_formats=["json"],
        )

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """ACLED uses an Authorization header with the API key."""
        api_key = config.get("api_key", os.environ.get("ACLED_API_KEY", ""))
        if not api_key:
            return {}
        return {"Authorization": f"ApiKey {api_key}"}

    def connect(self, config: Dict[str, Any]) -> bool:
        """Connect with .env fallback for API key."""
        if "api_key" not in config:
            config["api_key"] = os.environ.get("ACLED_API_KEY", "")
        return super().connect(config)

    def health_check(self) -> Dict[str, Any]:
        """Probe ACLED connectivity with a minimal query."""
        try:
            url = f"{self._base_url}/api/data/acled-v1?limit=1"
            self._http_get(url)
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "base_url": self._base_url,
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "error": str(exc),
                "connector": self._connector_name,
            }

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read ACLED events with optional incremental ingestion and normalization."""
        t0 = time.time()
        table = request.table_name or request.query

        if table not in self._endpoints:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown endpoint: '{table}'. Available: {list(self._endpoints.keys())}"],
            )

        url = f"{self._base_url}{self._endpoints[table]}"
        params: Dict[str, str] = {}

        if request.limit:
            params["limit"] = str(request.limit)

        # Incremental ingestion: filter by event_date >= incremental_value
        if request.incremental_key and request.incremental_value is not None:
            params[request.incremental_key] = str(request.incremental_value)

        if request.filters:
            for k, v in request.filters.items():
                params[k] = str(v)

        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"

        try:
            data = self._http_get(url)
            rows = self._extract_rows(data, table)
            normalized = (
                [self._normalize_event(r) for r in rows]
                if isinstance(rows, list)
                else rows
            )
            duration = int((time.time() - t0) * 1000)
            return ConnectorResponse(
                status="ok",
                data=normalized,
                row_count=len(normalized) if isinstance(normalized, list) else 1,
                duration_ms=duration,
                metadata={"endpoint": self._endpoints[table], "url": url},
            )
        except HTTPError as exc:
            return ConnectorResponse(
                status="error",
                errors=[f"HTTP {exc.code}: {exc.reason}"],
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    def _extract_rows(self, data: Any, table: str) -> Any:
        """ACLED responses wrap records under a 'data' key."""
        if isinstance(data, dict) and "data" in data:
            return data["data"] or []
        return super()._extract_rows(data, table)

    def _normalize_event(self, record: Any) -> Any:
        """Enrich an ACLED event record with canonical signal fields.

        Adds:
          headline    — raw notes text
          geo_hint    — "Country — Location"
          signal_date — ISO event_date string
        """
        if not isinstance(record, dict):
            return record
        normalized = dict(record)
        normalized["headline"] = record.get("notes", "")
        country = record.get("country", "")
        location = record.get("location", "")
        parts = [p for p in (country, location) if p]
        normalized["geo_hint"] = " — ".join(parts)
        normalized["signal_date"] = record.get("event_date", "")
        return normalized


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        env_path = BASE_DIR / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(description="ACLED DataBridge Connector CLI")
    parser.add_argument("--health", action="store_true", help="Check API connectivity")
    parser.add_argument(
        "--table",
        default="events",
        help="Endpoint to read (default: events)",
    )
    parser.add_argument("--since", metavar="DATE", help="Incremental since date (event_date)")
    parser.add_argument("--limit", type=int, default=10, help="Max rows (default: 10)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    conn = ACLEDConnector()
    if not conn.connect({}):
        result = {"status": "error", "error": "Failed to connect to ACLED API"}
        print(json.dumps(result, indent=2) if args.json else f"ERROR: {result['error']}")
        sys.exit(1)

    if args.health:
        result = conn.health_check()
    else:
        req = ConnectorRequest(
            table_name=args.table,
            limit=args.limit,
        )
        if args.since:
            req.incremental_key = "event_date"
            req.incremental_value = args.since

        resp = conn.read(req)
        result = {
            "status": resp.status,
            "data": resp.data,
            "row_count": resp.row_count,
            "errors": resp.errors,
            "duration_ms": resp.duration_ms,
        }

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
