# CUI // SP-CTI
"""DataBridge connector for Riverbed NetIM.

Uses the NetIM REST API v1.  Authenticated via Bearer token obtained from
a login endpoint, or passed directly as ``api_token`` in config.

Riverbed NetIM REST API base: https://{host}/api/v1
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from typing import Any, Dict, List
from urllib.request import Request, urlopen

from tools.databridge.connector import ConnectorRequest, ConnectorResponse
from tools.databridge.connectors.saas_base import REQUEST_TIMEOUT, SaaSBaseConnector

logger = get_logger("databridge.riverbed_netim")

_ENDPOINTS: Dict[str, str] = {
    "devices": "/api/v1/inventory/devices",
    "interfaces": "/api/v1/inventory/interfaces",
    "stats": "/api/v1/metrics/current",
    "topology": "/api/v1/topology/links",
    "sites": "/api/v1/inventory/sites",
    "configs": "/api/v1/inventory/device-configs",
}

# NetIM response envelope keys
_DATA_KEY: Dict[str, str] = {
    "devices": "devices",
    "interfaces": "interfaces",
    "stats": "metrics",
    "topology": "links",
    "sites": "sites",
    "configs": "configs",
}



from tools.databridge.registry import register_connector

@register_connector
class RiverbedNetIMConnector(SaaSBaseConnector):
    """Riverbed NetIM REST API connector (DataBridge)."""

    _connector_name = "riverbed_netim"
    _default_base_url = "https://netim.local"
    _endpoints = _ENDPOINTS

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        token = config.get("api_token", config.get("token", ""))
        if not token and config.get("username"):
            # Attempt token login
            token = self._login(config)
        return {"Authorization": f"Bearer {token}"}

    def _extract_rows(self, data: Any, table: str) -> List[Any]:
        """NetIM wraps records in {<key>: [...], total: N}."""
        key = _DATA_KEY.get(table, table)
        if isinstance(data, dict):
            return data.get(key, data.get("data", [data]))
        return data if isinstance(data, list) else [data]

    def health_check(self) -> Dict[str, Any]:
        try:
            resp = self._http_get(f"{self._base_url}/api/v1/system/status")
            return {
                "status": "healthy",
                "connector": self.connector_name,
                "base_url": self._base_url,
                "netim_version": resp.get("version", "unknown"),
            }
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "connector": self.connector_name}

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Override to inject pagination params."""
        if request.limit and request.filters is None:
            request = ConnectorRequest(
                table_name=request.table_name,
                query=request.query,
                limit=request.limit,
                filters={"pageSize": str(request.limit), "page": "1"},
            )
        return super().read(request)

    # -- Login helper ----------------------------------------------------------

    def _login(self, config: Dict[str, Any]) -> str:
        """Obtain a session token from NetIM login endpoint."""
        url = f"{self._base_url}/api/v1/auth/login"
        payload = json.dumps({
            "username": config["username"],
            "password": config.get("password", ""),
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            req = Request(url, data=payload, headers=headers, method="POST")
            with urlopen(req, timeout=config.get("timeout", REQUEST_TIMEOUT)) as resp:  # noqa: S310  # nosec B310
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                return data.get("token", data.get("access_token", ""))
        except Exception as exc:
            logger.error("NetIM login failed: %s", exc)
            return ""
