# CUI // SP-CTI
"""ACLED MeshProvider — wraps the DataBridge ACLED connector.

Maps ACLED event fields to the canonical conflict mesh schema:
  - notes       → headline
  - country + location → geo_hint (stored in metadata)
  - event_date  → event_date
  - event_type  → canonical event_type enum value
  - latitude / longitude → geometry (WKT POINT)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.conflict_mesh.providers.base import MeshProvider  # noqa: E402
from tools.databridge.registry import get_connector_instance  # noqa: E402
from tools.databridge.connector import ConnectorRequest  # noqa: E402

from tools.logging.icdev_logger import get_logger
log = get_logger("conflict_mesh.acled")

# Map ACLED event_type strings to canonical enum values
_ACLED_TYPE_MAP: Dict[str, str] = {
    "battles": "kinetic",
    "violence against civilians": "kinetic",
    "explosions/remote violence": "kinetic",
    "riots": "kinetic",
    "protests": "diplomatic",
    "strategic developments": "diplomatic",
}

_VALID_EVENT_TYPES = frozenset({
    "frontline_position", "narrative_event", "cyber_op",
    "kinetic", "humanitarian", "diplomatic", "logistics",
})


def _map_event_type(raw_type: str) -> str:
    normalized = raw_type.lower().strip()
    return _ACLED_TYPE_MAP.get(normalized, "narrative_event")


def _wkt_point(lat: Any, lon: Any) -> Optional[str]:
    try:
        return f"POINT({float(lon):.6f} {float(lat):.6f})"
    except (TypeError, ValueError):
        return None


class ACLEDProvider(MeshProvider):
    """Conflict mesh provider backed by the ACLED DataBridge connector."""

    def __init__(self) -> None:
        self._connector = get_connector_instance("acled")
        if self._connector is None:
            log.warning("ACLED connector not available; provider will return empty results")

    @property
    def provider_name(self) -> str:
        return "acled"

    def normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        event_id = str(raw.get("data_id") or raw.get("id") or "")
        headline = (
            raw.get("notes")
            or raw.get("headline")
            or raw.get("event_type", "")
            or ""
        )
        country = raw.get("country", "")
        location = raw.get("location", "")
        geo_hint = f"{country} — {location}".strip(" —") if (country or location) else ""
        geometry = _wkt_point(raw.get("latitude"), raw.get("longitude"))
        event_type = _map_event_type(raw.get("event_type", ""))

        metadata: Dict[str, Any] = {
            "geo_hint": geo_hint,
            "fatalities": raw.get("fatalities"),
            "actor1": raw.get("actor1"),
            "actor2": raw.get("actor2"),
        }
        return {
            "id": f"acled-{event_id}",
            "source": "acled",
            "event_type": event_type,
            "headline": headline,
            "event_date": raw.get("event_date"),
            "geometry": geometry,
            "metadata": metadata,
        }

    def fetch(self, since_date: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if self._connector is None:
            return []
        try:
            import os
            api_key = os.environ.get("ACLED_API_KEY", "")
            if not api_key:
                log.debug("ACLED_API_KEY not set — skipping ACLED fetch")
                return []
            self._connector.connect({"api_key": api_key})
            resp = self._connector.read(ConnectorRequest(
                table_name="events",
                incremental_key="event_date",
                incremental_value=since_date or "",
                limit=limit,
            ))
            if resp.status != "ok" or not resp.data:
                return []
            return [self.normalize(r) for r in resp.data]
        except Exception as exc:
            log.warning("ACLED fetch failed: %s", exc)
            return []
