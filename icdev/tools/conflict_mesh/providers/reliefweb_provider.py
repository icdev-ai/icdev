# CUI // SP-CTI
"""ReliefWeb MeshProvider — pulls humanitarian situation reports via REST API.

ReliefWeb API: https://api.reliefweb.int/v1
No authentication required. Rate limit: ~1 req/sec.

Maps report fields to the canonical conflict mesh schema:
  - fields.title      → headline
  - fields.date.created → event_date
  - fields.country[0].name → geo_hint (metadata)
  - fields.body       → metadata.body (used by ML engine)
  - event_type always "humanitarian"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.conflict_mesh.providers.base import MeshProvider  # noqa: E402
from tools.logging.icdev_logger import get_logger

log = get_logger("conflict_mesh.reliefweb")

RELIEFWEB_BASE_URL = "https://api.reliefweb.int/v1"
_REQUEST_TIMEOUT = 20
_USER_AGENT = "ICDEV-DataBridge/1.0"


def _parse_date(date_str: Any) -> Optional[str]:
    """Extract YYYY-MM-DD from ISO datetime string."""
    if not date_str:
        return None
    s = str(date_str)[:10]
    if len(s) == 10 and s[4] == "-":
        return s
    return None


class ReliefWebProvider(MeshProvider):
    """Conflict mesh provider that pulls humanitarian reports from ReliefWeb."""

    @property
    def provider_name(self) -> str:
        return "reliefweb"

    def _http_get(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )
        with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        fields = raw.get("fields", raw)
        event_id = str(raw.get("id", ""))
        title = fields.get("title", "")
        countries = fields.get("country", [])
        country_name = countries[0].get("name", "") if countries else ""
        date_info = fields.get("date", {})
        if isinstance(date_info, dict):
            raw_date = date_info.get("created") or date_info.get("changed")
        else:
            raw_date = str(date_info)
        event_date = _parse_date(raw_date)
        body = fields.get("body", "")

        metadata: Dict[str, Any] = {
            "geo_hint": country_name,
            "body": body[:2000] if body else "",  # truncate for storage
            "url": fields.get("url", ""),
        }
        return {
            "id": f"reliefweb-{event_id}",
            "source": "reliefweb",
            "event_type": "humanitarian",
            "headline": title,
            "event_date": event_date,
            "geometry": None,
            "metadata": metadata,
        }

    def fetch(self, since_date: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "fields": {"include": ["title", "date", "country", "body", "url"]},
            "filter": {
                "operator": "AND",
                "conditions": [
                    {"field": "type.name", "value": "Situation Report"},
                ],
            },
            "sort": ["date.created:desc"],
            "limit": min(limit, 100),
        }
        if since_date:
            payload["filter"]["conditions"].append(
                {"field": "date.created", "value": {"from": f"{since_date}T00:00:00+00:00"}}
            )
        try:
            data = self._http_get(f"{RELIEFWEB_BASE_URL}/reports", payload)
            items = data.get("data", [])
            return [self.normalize(item) for item in items]
        except (HTTPError, URLError, OSError, json.JSONDecodeError, Exception) as exc:
            log.warning("ReliefWeb fetch failed: %s", exc)
            return []
