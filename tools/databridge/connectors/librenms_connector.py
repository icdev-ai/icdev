# CUI // SP-CTI
"""DataBridge connector for LibreNMS.

Uses the LibreNMS REST API v0.  All requests are authenticated via the
``X-Auth-Token`` header.

LibreNMS REST API docs: https://docs.librenms.org/API/
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.databridge.connector import ConnectorRequest, ConnectorResponse
from tools.databridge.connectors.saas_base import SaaSBaseConnector

# Map logical table names to LibreNMS API paths
_ENDPOINTS: Dict[str, str] = {
    "devices": "/api/v0/devices",
    "interfaces": "/api/v0/ports",
    "stats": "/api/v0/bills",
    "topology": "/api/v0/links",
    "configs": "/api/v0/oxidized",  # requires Oxidized integration
    "alerts": "/api/v0/alerts",
}

# Keys inside each LibreNMS response envelope
_DATA_KEY: Dict[str, str] = {
    "devices": "devices",
    "interfaces": "ports",
    "stats": "bills",
    "topology": "links",
    "configs": "nodes",
    "alerts": "alerts",
}



from tools.databridge.registry import register_connector

@register_connector
class LibreNMSConnector(SaaSBaseConnector):
    """LibreNMS REST API connector (DataBridge)."""

    _connector_name = "librenms"
    _default_base_url = "http://librenms.local"
    _endpoints = _ENDPOINTS

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        token = config.get("token", config.get("api_token", ""))
        return {"X-Auth-Token": token}

    def _extract_rows(self, data: Any, table: str) -> List[Any]:
        """LibreNMS wraps records in an envelope: {"status": "ok", "<key>": [...]}."""
        key = _DATA_KEY.get(table, table)
        if isinstance(data, dict):
            return data.get(key, data.get("data", [data]))
        return data if isinstance(data, list) else [data]

    def health_check(self) -> Dict[str, Any]:
        try:
            resp = self._http_get(f"{self._base_url}/api/v0/system")
            return {
                "status": "healthy",
                "connector": self.connector_name,
                "base_url": self._base_url,
                "librenms_version": resp.get("api", {}).get("version", "unknown"),
            }
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "connector": self.connector_name}

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Override to add per-device sub-queries when device_filter is set."""
        device_id = (request.filters or {}).get("device_id")
        if device_id and request.table_name in ("interfaces", "configs"):
            # Per-device endpoints
            path_map = {
                "interfaces": f"/api/v0/devices/{device_id}/ports",
                "configs": f"/api/v0/devices/{device_id}/bgp",
            }
            if request.table_name in path_map:
                modified = ConnectorRequest(
                    table_name=request.table_name,
                    connection_id=request.connection_id,
                    limit=request.limit,
                    filters={k: v for k, v in (request.filters or {}).items() if k != "device_id"},
                )
                # Temporarily override endpoint for this request
                old = self._endpoints.get(request.table_name)
                self._endpoints[request.table_name] = path_map[request.table_name]
                result = super().read(modified)
                if old is not None:
                    self._endpoints[request.table_name] = old
                return result
        return super().read(request)
