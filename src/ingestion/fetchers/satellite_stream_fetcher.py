# CUI // SP-CTI
"""Satellite imagery stream fetcher — polls Copernicus Data Space for recent EO scenes.

Fetches recent Sentinel-2 scene metadata and returns raw record dicts for the
stream buffer. No persistence here: purely HTTP fetch and JSON deserialization.

NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"
_DEFAULT_TIMEOUT_S = 15
_MAX_RESULTS = 50


class SatelliteStreamFetchError(Exception):
    """Raised when the satellite stream cannot be reached or returns invalid data."""


class SatelliteStreamFetcher:
    """Fetcher for satellite imagery live-stream signals (Copernicus OData).

    Args:
        base_url: Copernicus OData base URL (override for testing or proxies).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch(
        self,
        collection: str = "SENTINEL-2",
        cloud_cover_pct: int = 20,
        since: Optional[str] = None,
        max_results: int = _MAX_RESULTS,
        region: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch recent satellite scene metadata.

        Args:
            collection: Copernicus collection name (default SENTINEL-2).
            cloud_cover_pct: Max acceptable cloud cover percentage (0–100).
            since: ISO 8601 cursor; fetch only scenes after this time.
            max_results: Max scenes per request.
            region: Optional WKT geometry filter (simplified to bbox in basic mode).

        Returns:
            List of raw scene dicts with keys ``Id``, ``Name``,
            ``SensingTime``, ``CloudCoverPercentage``, etc.

        Raises:
            SatelliteStreamFetchError: If the API is unreachable or returns unparseable data.
        """
        if not since:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat().replace("+", "Z")

        # OData filter
        filters = [
            f"Collection/Name eq '{collection}'",
            f"CloudCoverPercentage le {cloud_cover_pct}",
            f"SensingTime gt {since}",
        ]
        filter_str = " and ".join(filters)

        params: Dict[str, str] = {
            "$filter": filter_str,
            "$orderby": "SensingTime desc",
            "$top": str(min(max_results, 100)),
            "$format": "application/json",
        }

        if region:
            # Simplified: use OData intersects if WKT provided, otherwise skip
            params["$spatialFilter"] = f"Intersects(Footprint,{region})"

        query_string = urllib.parse.urlencode(params)
        full_url = f"{self.base_url}/Products?{query_string}"

        try:
            req = urllib.request.Request(
                full_url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            data: Dict[str, Any] = json.loads(raw)
        except urllib.error.HTTPError as exc:
            msg = f"Copernicus API HTTP error {exc.code}: {exc.reason}"
            log.warning(msg)
            raise SatelliteStreamFetchError(msg) from exc
        except urllib.error.URLError as exc:
            msg = f"Copernicus API unreachable: {exc}"
            log.warning(msg)
            raise SatelliteStreamFetchError(msg) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            msg = f"Copernicus API returned unparseable response: {exc}"
            log.warning(msg)
            raise SatelliteStreamFetchError(msg) from exc

        items = data.get("value", [])
        if not isinstance(items, list):
            raise SatelliteStreamFetchError("Copernicus API response missing 'value' array")

        for item in items:
            item["_stream_meta"] = {
                "source": "copernicus_odata",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "collection": collection,
            }

        log.info("Satellite stream fetcher: %d scene(s) from Copernicus", len(items))
        return items
