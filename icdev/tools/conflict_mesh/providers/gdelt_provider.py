# CUI // SP-CTI
"""GDELT MeshProvider — wraps the DataBridge GDELT connector.

Maps GDELT event fields to canonical conflict mesh schema.
Filters to conflict-relevant CAMEO codes (EventCode >= 140).

CAMEO codes:
  14x — Protest (140-145)
  15x — Exhibit military posture
  17x — Coerce
  18x — Assault
  19x — Fight / Use conventional military force
  20x — Engage in unconventional mass violence
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

log = get_logger("conflict_mesh.gdelt")

# Minimum CAMEO EventCode integer prefix to be considered conflict-relevant
_MIN_CAMEO_CODE = 140


def _gdelt_date_to_iso(sqldate: Any) -> Optional[str]:
    """Convert GDELT SQLDATE (YYYYMMDD int/str) to ISO YYYY-MM-DD string."""
    s = str(sqldate).strip()
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _cameo_int(code: Any) -> int:
    try:
        return int(str(code)[:3])
    except (TypeError, ValueError):
        return 0


class GDELTProvider(MeshProvider):
    """Conflict mesh provider backed by the GDELT DataBridge connector."""

    def __init__(self) -> None:
        self._connector = get_connector_instance("gdelt")
        if self._connector is None:
            log.warning("GDELT connector not available; provider will return empty results")

    @property
    def provider_name(self) -> str:
        return "gdelt"

    def normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        event_id = str(raw.get("GLOBALEVENTID") or raw.get("id") or "")
        headline = (
            raw.get("MentionSummary")
            or raw.get("Actor1Name", "")
            or "GDELT event"
        )
        geo = raw.get("ActionGeo_FullName", "")
        event_date = _gdelt_date_to_iso(raw.get("SQLDATE", ""))
        tone = raw.get("GoldsteinScale") or raw.get("AvgTone") or 0.0
        cameo_code = str(raw.get("EventCode", "0"))

        metadata: Dict[str, Any] = {
            "geo_hint": geo,
            "cameo_code": cameo_code,
            "tone": float(tone),
            "actor1": raw.get("Actor1Name"),
            "actor2": raw.get("Actor2Name"),
            "source_url": raw.get("MentionSourceName"),
        }
        return {
            "id": f"gdelt-{event_id}",
            "source": "gdelt",
            "event_type": "narrative_event",
            "headline": headline,
            "event_date": event_date,
            "geometry": None,
            "metadata": metadata,
        }

    def fetch(self, since_date: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if self._connector is None:
            return []
        try:
            self._connector.connect({})
            resp = self._connector.read(ConnectorRequest(
                table_name="events",
                filters={"query": "conflict"},
                incremental_key="SQLDATE",
                incremental_value=(since_date or "").replace("-", "") if since_date else "",
                limit=limit,
            ))
            if resp.status != "ok" or not resp.data:
                return []
            # Filter to conflict-relevant CAMEO codes
            results = []
            for raw in resp.data:
                if _cameo_int(raw.get("EventCode", "0")) >= _MIN_CAMEO_CODE:
                    results.append(self.normalize(raw))
            return results
        except Exception as exc:
            log.warning("GDELT fetch failed: %s", exc)
            return []
