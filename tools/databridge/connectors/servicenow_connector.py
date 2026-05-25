# CUI // SP-CTI
"""ServiceNow CMDB DataBridge Connector.

Pulls application and server inventory from ServiceNow CMDB tables
and imports them into the Migration Canvas mc_app_inventory schema.

Usage:
    from tools.databridge.connectors.servicenow_connector import ServiceNowCMDBConnector
    conn = ServiceNowCMDBConnector()
    conn.connect({
        "base_url": "https://myinstance.service-now.com",
        "username": "admin",
        "password": "...",          # or use bearer_token
    })
    apps = conn.fetch_table("applications")
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import base64
from typing import Any, Dict, List

from tools.databridge.connectors.saas_base import SaaSBaseConnector

logger = get_logger("databridge.servicenow_cmdb")

# Field map: ServiceNow cmdb_ci_appl → mc_app_inventory columns
_APP_FIELD_MAP = {
    "name": "name",
    "version": "version",
    "busines_criticality": "criticality",
    "u_owner": "owner",
    "support_group": "team",
    "install_status": "migration_status",
    "comments": "notes",
}

_CRITICALITY_MAP = {
    "1 - critical": "mission_critical",
    "2 - high": "high",
    "3 - moderate": "medium",
    "4 - low": "low",
    "": "medium",
}


class ServiceNowCMDBConnector(SaaSBaseConnector):
    """DataBridge connector for ServiceNow CMDB tables."""

    _connector_name = "servicenow_cmdb"
    _default_base_url = "https://{instance}.service-now.com"
    _endpoints = {
        "applications": "/api/now/table/cmdb_ci_appl",
        "servers": "/api/now/table/cmdb_ci_server",
        "relationships": "/api/now/table/cmdb_rel_ci",
        "databases": "/api/now/table/cmdb_ci_database",
        "health": "/api/now/table/sys_properties?sysparm_limit=1",
    }

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        if "bearer_token" in config:
            return {"Authorization": f"Bearer {config['bearer_token']}"}
        username = config.get("username", "")
        password = config.get("password", "")
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {
            "Authorization": f"Basic {creds}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def health_check(self) -> Dict[str, Any]:
        """Probe ServiceNow via sys_properties endpoint."""
        try:
            url = f"{self._base_url}{self._endpoints['health']}"
            resp = self._http_get(url)
            result = self._extract_rows(resp)
            return {
                "status": "healthy",
                "base_url": self._base_url,
                "connector": self._connector_name,
                "rows_returned": len(result),
            }
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "connector": self._connector_name}

    def _extract_rows(self, resp: Any) -> List[Dict]:
        """Unwrap ServiceNow envelope: {'result': [...]}."""
        if isinstance(resp, dict) and "result" in resp:
            result = resp["result"]
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return [result]
        if isinstance(resp, list):
            return resp
        return []

    def fetch_table(self, table_name: str, params: Dict[str, Any] | None = None) -> List[Dict]:
        """Fetch all rows from a named CMDB table, handling pagination."""
        endpoint = self._endpoints.get(table_name)
        if not endpoint:
            raise ValueError(f"Unknown table: {table_name!r}. Available: {list(self._endpoints)}")
        limit = 1000
        offset = 0
        all_rows: List[Dict] = []
        while True:
            qs = f"sysparm_limit={limit}&sysparm_offset={offset}"
            if params:
                qs += "&" + "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{self._base_url}{endpoint}?{qs}"
            raw = self._http_get(url)
            rows = self._extract_rows(raw)
            all_rows.extend(rows)
            if len(rows) < limit:
                break
            offset += limit
        return all_rows

    def map_app_to_inventory(self, snow_row: Dict) -> Dict:
        """Map a ServiceNow cmdb_ci_appl row to mc_app_inventory fields."""

        def _val(v: Any) -> str:
            if isinstance(v, dict):
                return v.get("display_value") or v.get("value") or ""
            return str(v) if v else ""

        raw_crit = _val(snow_row.get("busines_criticality", "")).lower()
        criticality = _CRITICALITY_MAP.get(raw_crit, "medium")
        return {
            "name": _val(snow_row.get("name")) or "Unknown",
            "version": _val(snow_row.get("version")),
            "owner": _val(snow_row.get("u_owner")),
            "team": _val(snow_row.get("support_group")),
            "criticality": criticality,
            "notes": _val(snow_row.get("comments")),
            "classification": "CUI",
        }
