#!/usr/bin/env python3
"""AIS DataBridge Connector — REST (VesselsValue/MarineTraffic) and file-import modes.

Auto-detects mode from connection params:
  - REST mode:  config["api_key"] is set → queries VesselsValue or MarineTraffic API
  - File mode:  no api_key → wraps tools/ais/ais_importer.py for local NMEA file ingestion

Normalized fields (all modes):
  headline    — "<vessel_name> (<mmsi>)"
  geo_hint    — "<lat>,<lon>"
  signal_date — ISO 8601 timestamp
  source      — "ais"

Usage (via DataBridge registry):
    from tools.databridge.registry import get_connector_instance
    conn = get_connector_instance("ais")

    # REST mode (VesselsValue)
    conn.connect({"api_key": "your-key", "provider": "vesselsvalue"})
    result = conn.read(ConnectorRequest(table_name="vessels", limit=50))

    # REST mode (MarineTraffic)
    conn.connect({"api_key": "your-key", "provider": "marinetraffic"})
    result = conn.read(ConnectorRequest(table_name="vessels", limit=50))

    # File mode
    conn.connect({})
    result = conn.read(ConnectorRequest(
        table_name="vessels",
        filters={"file_paths": ["/data/vessels.nmea"]},
    ))

Usage (CLI):
    python tools/databridge/connectors/ais_connector.py --health --json
    python tools/databridge/connectors/ais_connector.py --table vessels --limit 10 --json
    python tools/databridge/connectors/ais_connector.py --file data.nmea --json
    python tools/databridge/connectors/ais_connector.py --dir /path/to/nmea/ --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.databridge.connector import (  # noqa: E402
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
)
from tools.databridge.connectors.saas_base import SaaSBaseConnector  # noqa: E402
from tools.databridge.registry import register_connector  # noqa: E402

logger = get_logger("databridge.ais")

_VESSELS_VALUE_BASE = "https://api.vesselsvalue.com"
_VESSELS_VALUE_ENDPOINTS: Dict[str, str] = {
    "vessels": "/v1/vessels",
    "vessel": "/v1/vessel",
}

_MARINE_TRAFFIC_BASE = "https://services.marinetraffic.com"
# {api_key} is substituted at connect() time
_MARINE_TRAFFIC_ENDPOINTS: Dict[str, str] = {
    "vessels": "/api/exportvessels/v:8/{api_key}/protocol:jsono",
    "vessel": "/api/vesseldetails/v:4/{api_key}/protocol:jsono",
}

AIS_MAX_RECORDS = 1_000


def _normalize_ais_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Apply canonical signal fields to an AIS vessel record.

    Handles both VesselsValue (lowercase keys) and MarineTraffic (uppercase keys).
    Also handles rows from tools/ais/ais_importer.py (mmsi, lat, lon, timestamp).
    """
    normalized = dict(record)

    vessel_name = (
        record.get("vessel_name")
        or record.get("SHIPNAME")
        or record.get("name")
        or record.get("NAME")
        or ""
    )
    mmsi = str(record.get("mmsi") or record.get("MMSI") or "")
    lat = record.get("lat") or record.get("LAT") or ""
    lon = record.get("lon") or record.get("LON") or ""
    timestamp = (
        record.get("timestamp")
        or record.get("DATETIME")
        or record.get("LAST_POS")
        or record.get("signal_date")
        or ""
    )

    name_parts = [str(p) for p in (vessel_name, mmsi) if p]
    normalized["headline"] = " ".join(name_parts) if name_parts else "Unknown Vessel"
    normalized["geo_hint"] = f"{lat},{lon}" if (lat != "" and lon != "") else ""
    normalized["signal_date"] = str(timestamp)
    normalized["source"] = "ais"
    return normalized


@register_connector
class AISConnector(SaaSBaseConnector):
    """AIS DataBridge Connector with dual REST/file-import modes.

    REST mode (api_key present in config):
      Connects to VesselsValue (default) or MarineTraffic based on
      config["provider"].  Normalized with canonical headline/geo_hint/
      signal_date/source fields.

    File mode (no api_key):
      Wraps tools/ais/ais_importer._parse_file() for local NMEA ingestion.
      Requires filters["file_paths"] (list[str]) and/or filters["dir_paths"].
    """

    _connector_name = "ais"
    _default_base_url = _VESSELS_VALUE_BASE
    _endpoints: Dict[str, str] = dict(_VESSELS_VALUE_ENDPOINTS)

    def __init__(self) -> None:
        super().__init__()
        self._api_key: str = ""
        self._provider: str = "vesselsvalue"

    # -- ABC properties -------------------------------------------------------

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.SAAS_API if self._api_key else ConnectorType.FILE

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,
            supports_schema_inference=True,
            max_batch_size=AIS_MAX_RECORDS,
            supported_formats=["json", "nmea"],
        )

    # -- Auth -----------------------------------------------------------------

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """VesselsValue: X-API-Key header.  MarineTraffic: key embedded in URL path."""
        api_key = config.get("api_key", "")
        provider = config.get("provider", "vesselsvalue").lower()
        if not api_key or provider == "marinetraffic":
            return {}
        return {"X-API-Key": api_key}

    # -- Connection lifecycle -------------------------------------------------

    def connect(self, config: Dict[str, Any]) -> bool:
        self._api_key = config.get("api_key", "")
        self._provider = config.get("provider", "vesselsvalue").lower()

        if not self._api_key:
            self._config = config
            self._connected = True
            logger.info("AIS connector: file-import mode (no api_key)")
            return True

        # Configure provider-specific endpoints before calling super()
        if self._provider == "marinetraffic":
            self._default_base_url = _MARINE_TRAFFIC_BASE
            self._endpoints = {
                k: v.format(api_key=self._api_key)
                for k, v in _MARINE_TRAFFIC_ENDPOINTS.items()
            }
        else:
            self._default_base_url = _VESSELS_VALUE_BASE
            self._endpoints = dict(_VESSELS_VALUE_ENDPOINTS)

        return super().connect(config)

    # -- Health check ---------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        if not self._api_key:
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "mode": "file",
            }
        try:
            path = next(iter(self._endpoints.values()))
            base = self._base_url or self._default_base_url
            url = f"{base}{path}"
            self._http_get(url)
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "mode": "rest",
                "provider": self._provider,
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "connector": self._connector_name,
                "error": str(exc),
                "provider": self._provider,
            }

    # -- Data operations ------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        if not self._api_key:
            return self._read_files(request)
        return self._read_rest(request)

    def _read_rest(self, request: ConnectorRequest) -> ConnectorResponse:
        """Query VesselsValue or MarineTraffic REST API."""
        t0 = time.time()
        table = request.table_name or "vessels"

        if table not in self._endpoints:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown endpoint: '{table}'. Available: {list(self._endpoints.keys())}"],
            )

        path = self._endpoints[table]
        base = self._base_url or self._default_base_url
        url = f"{base}{path}"

        params: Dict[str, str] = {}
        if request.filters:
            params.update(
                {k: str(v) for k, v in request.filters.items()
                 if k not in ("file_paths", "dir_paths")}
            )
        if request.limit:
            params["limit"] = str(min(request.limit, AIS_MAX_RECORDS))
        if request.incremental_key and request.incremental_value is not None:
            params[request.incremental_key] = str(request.incremental_value)

        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"

        try:
            data = self._http_get(url)
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
                metadata={"endpoint": table, "url": url, "provider": self._provider},
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

    def _read_files(self, request: ConnectorRequest) -> ConnectorResponse:
        """Parse local NMEA files via tools/ais/ais_importer._parse_file."""
        t0 = time.time()
        filters = dict(request.filters) if request.filters else {}

        file_paths_raw = filters.get("file_paths", [])
        dir_paths_raw = filters.get("dir_paths", [])

        if isinstance(file_paths_raw, str):
            file_paths_raw = [file_paths_raw]
        if isinstance(dir_paths_raw, str):
            dir_paths_raw = [dir_paths_raw]

        paths: List[Path] = [Path(p) for p in file_paths_raw]
        for d in dir_paths_raw:
            dp = Path(d)
            paths.extend(dp.glob("*.nmea"))
            paths.extend(dp.glob("*.txt"))

        if not paths:
            return ConnectorResponse(
                status="error",
                errors=["File mode requires filters['file_paths'] or filters['dir_paths']"],
            )

        try:
            from tools.ais.ais_importer import _parse_file  # noqa: PLC0415
        except ImportError as exc:
            return ConnectorResponse(
                status="error",
                errors=[f"AIS importer unavailable: {exc}"],
            )

        all_rows: List[Dict[str, Any]] = []
        all_errors: List[str] = []
        vessel_type_cache: Dict[str, str] = {}

        for path in paths:
            rows, errs = _parse_file(path, vessel_type_cache)
            all_rows.extend(rows)
            all_errors.extend(errs)

        if request.limit:
            all_rows = all_rows[: request.limit]

        normalized = [_normalize_ais_record(r) for r in all_rows]
        duration = int((time.time() - t0) * 1000)

        return ConnectorResponse(
            status="ok",
            data=normalized,
            row_count=len(normalized),
            duration_ms=duration,
            metadata={
                "mode": "file",
                "files_scanned": len(paths),
                "errors": all_errors[:20],
            },
        )

    # -- Helpers --------------------------------------------------------------

    def _extract_rows(self, data: Any, table: str) -> Any:
        """Unwrap VesselsValue {"vessels": [...]} or MarineTraffic {"DATA": [...]}."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "vessels" in data:
                return data["vessels"]
            if "DATA" in data:
                return data["DATA"]
        return super()._extract_rows(data, table)

    def _normalize_record(self, record: Any, table: str = "") -> Any:
        if not isinstance(record, dict):
            return record
        return _normalize_ais_record(record)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="AIS DataBridge Connector CLI")
    parser.add_argument("--health", action="store_true", help="Check connectivity")
    parser.add_argument(
        "--table", default="vessels", choices=["vessels", "vessel"],
        help="Endpoint to query in REST mode (default: vessels)",
    )
    parser.add_argument("--limit", type=int, default=10, help="Max records (default: 10)")
    parser.add_argument("--api-key", dest="api_key", default="", help="API key for REST mode")
    parser.add_argument(
        "--provider", default="vesselsvalue", choices=["vesselsvalue", "marinetraffic"],
        help="REST provider (default: vesselsvalue)",
    )
    parser.add_argument(
        "--file", dest="files", metavar="PATH", action="append", default=[],
        help="NMEA file to parse (repeatable; file mode)",
    )
    parser.add_argument(
        "--dir", dest="dirs", metavar="DIR", action="append", default=[],
        help="Directory of *.nmea/*.txt files (repeatable; file mode)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    config: Dict[str, Any] = {}
    if args.api_key:
        config["api_key"] = args.api_key
        config["provider"] = args.provider

    conn = AISConnector()
    if not conn.connect(config):
        result = {"status": "error", "error": "Failed to connect"}
        print(json.dumps(result, indent=2) if args.json else f"ERROR: {result['error']}")
        sys.exit(1)

    if args.health:
        result = conn.health_check()
    else:
        filters: Dict[str, Any] = {}
        if args.files:
            filters["file_paths"] = args.files
        if args.dirs:
            filters["dir_paths"] = args.dirs

        req = ConnectorRequest(
            table_name=args.table,
            limit=args.limit,
            filters=filters,
        )
        resp = conn.read(req)
        result = {
            "status": resp.status,
            "data": resp.data,
            "row_count": resp.row_count,
            "errors": resp.errors,
            "duration_ms": resp.duration_ms,
            "metadata": resp.metadata,
        }

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
