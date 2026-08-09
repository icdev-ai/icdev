#!/usr/bin/env python3
"""GDELT DataBridge Connector — Global Database of Events, Language, and Tone.

Connects to the GDELT Project API v2 for global news event data:
  - Events database: conflict and cooperation events with CAMEO codes
  - GKG (Global Knowledge Graph): themes, entities, and tone from news articles

No authentication required (public API).
Base URL: https://api.gdeltproject.org
Incremental key: SQLDATE (format YYYYMMDD)

Usage (via DataBridge registry):
    from tools.databridge.registry import get_connector_instance
    conn = get_connector_instance("gdelt")
    conn.connect({})
    result = conn.read(ConnectorRequest(
        table_name="events",
        incremental_key="SQLDATE",
        incremental_value="20240101",
        filters={"query": "conflict"},
        limit=50,
    ))

Usage (standalone CLI):
    python tools/databridge/connectors/gdelt_connector.py --health --json
    python tools/databridge/connectors/gdelt_connector.py --table events --query "conflict" --json
    python tools/databridge/connectors/gdelt_connector.py --table gkg --query "war" --since 20240101 --json
"""

from __future__ import annotations

import argparse
import json
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

logger = get_logger("databridge.gdelt")

GDELT_DEFAULT_BASE_URL = "https://api.gdeltproject.org"
GDELT_MAX_RECORDS = 250  # GDELT API hard cap per request


@register_connector
class GDELTConnector(SaaSBaseConnector):
    """GDELT Project API connector for global event and knowledge graph data.

    Normalizes raw GDELT records with:
      Events table:
        - headline    = Actor1Name + " — " + EventCode
        - geo_hint    = ActionGeo_FullName
        - signal_date = SQLDATE

      GKG table:
        - headline    = DocumentIdentifier (article URL/title)
        - geo_hint    = Locations string
        - signal_date = DATE
    """

    _connector_name = "gdelt"
    _default_base_url = GDELT_DEFAULT_BASE_URL
    _endpoints = {
        "events": "/api/v2/events/query",
        "gkg": "/api/v2/gkg/query",
    }

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,
            supports_schema_inference=True,
            max_batch_size=GDELT_MAX_RECORDS,
            supported_formats=["json"],
        )

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """GDELT is a public API — no authentication headers required."""
        return {}

    def connect(self, config: Dict[str, Any]) -> bool:
        """Connect to GDELT (no credentials needed)."""
        return super().connect(config)

    def health_check(self) -> Dict[str, Any]:
        """Probe GDELT connectivity with a minimal events query."""
        try:
            url = (
                f"{self._base_url}/api/v2/events/query"
                "?query=peace&mode=eventlist&maxrecords=1&format=json"
            )
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
        """Read GDELT events or GKG records with optional incremental ingestion."""
        t0 = time.time()
        table = request.table_name or request.query

        if table not in self._endpoints:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown endpoint: '{table}'. Available: {list(self._endpoints.keys())}"],
            )

        params: Dict[str, str] = {"format": "json"}

        if table == "events":
            params["mode"] = "eventlist"
        elif table == "gkg":
            params["mode"] = "artlist"

        # Apply limit (cap at GDELT max)
        if request.limit:
            params["maxrecords"] = str(min(request.limit, GDELT_MAX_RECORDS))
        else:
            params["maxrecords"] = str(GDELT_MAX_RECORDS)

        # Pull remaining filters; extract 'query' before spreading the rest
        filters = dict(request.filters) if request.filters else {}
        params["query"] = filters.pop("query", "conflict")

        # Incremental ingestion: SQLDATE >= incremental_value (format YYYYMMDD)
        if request.incremental_key and request.incremental_value is not None:
            params["startdatetime"] = str(request.incremental_value)

        params.update({k: str(v) for k, v in filters.items()})

        path = self._endpoints[table]
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        full_url = f"{self._base_url}{path}?{qs}"

        try:
            data = self._http_get(full_url)
            rows = self._extract_rows(data, table)
            normalized = (
                [self._normalize_record(r, table) for r in rows]
                if isinstance(rows, list)
                else rows
            )
            duration = int((time.time() - t0) * 1000)
            return ConnectorResponse(
                status="ok",
                data=normalized,
                row_count=len(normalized) if isinstance(normalized, list) else 1,
                duration_ms=duration,
                metadata={"endpoint": table, "url": full_url},
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
        """Pull the record list out of GDELT's response envelope."""
        if isinstance(data, dict):
            if table == "events":
                return data.get("events") or []
            if table == "gkg":
                return data.get("articles") or []
        return super()._extract_rows(data, table)

    def _normalize_record(self, record: Any, table: str) -> Any:
        """Enrich a GDELT record with canonical signal fields.

        Events:
          headline    — "Actor1Name — EventCode"
          geo_hint    — ActionGeo_FullName
          signal_date — SQLDATE

        GKG:
          headline    — DocumentIdentifier
          geo_hint    — Locations
          signal_date — DATE
        """
        if not isinstance(record, dict):
            return record
        normalized = dict(record)
        if table == "events":
            actor1 = record.get("Actor1Name", "")
            event_code = record.get("EventCode", "")
            parts = [p for p in (actor1, event_code) if p]
            normalized["headline"] = " — ".join(parts)
            normalized["geo_hint"] = record.get("ActionGeo_FullName", "")
            normalized["signal_date"] = record.get("SQLDATE", "")
        elif table == "gkg":
            normalized["headline"] = record.get("DocumentIdentifier", "")
            normalized["geo_hint"] = record.get("Locations", "")
            normalized["signal_date"] = record.get("DATE", "")
        return normalized


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="GDELT DataBridge Connector CLI")
    parser.add_argument("--health", action="store_true", help="Check API connectivity")
    parser.add_argument(
        "--table",
        default="events",
        choices=["events", "gkg"],
        help="Endpoint to read (default: events)",
    )
    parser.add_argument(
        "--query",
        default="conflict",
        help="GDELT search query (default: conflict)",
    )
    parser.add_argument(
        "--since",
        metavar="SQLDATE",
        help="Incremental since date as YYYYMMDD (e.g. 20240101)",
    )
    parser.add_argument("--limit", type=int, default=10, help="Max rows (default: 10)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    conn = GDELTConnector()
    if not conn.connect({}):
        result = {"status": "error", "error": "Failed to connect to GDELT API"}
        print(json.dumps(result, indent=2) if args.json else f"ERROR: {result['error']}")
        sys.exit(1)

    if args.health:
        result = conn.health_check()
    else:
        req = ConnectorRequest(
            table_name=args.table,
            limit=args.limit,
            filters={"query": args.query},
        )
        if args.since:
            req.incremental_key = "SQLDATE"
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
