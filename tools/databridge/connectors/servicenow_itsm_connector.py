# CUI // SP-CTI
"""ServiceNow ITSM DataBridge Connector.

Provides REST API integration for ServiceNow ITSM tables including
change_request, incident, problem, sc_request, and change_task.

Supports both Basic and Bearer token authentication.
No hardcoded secrets — all credentials are passed via the config dict.

Usage:
    from tools.databridge.connectors.servicenow_itsm_connector import ServiceNowITSMConnector
    conn = ServiceNowITSMConnector()
    conn.connect({
        "base_url": "https://myinstance.service-now.com",
        "username": "admin",
        "password": "...",
        # or "bearer_token": "...",
    })
    crs = conn.fetch_table("change_request", params={"state": "2"})
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import base64
import logging
from typing import Any, Dict, List, Optional

from tools.databridge.connectors.saas_base import SaaSBaseConnector

logger = get_logger("databridge.servicenow_itsm")

# ServiceNow change_request state → human-readable
_CHANGE_REQUEST_STATE_MAP = {
    "-5": "new",
    "-4": "assess",
    "-3": "authorize",
    "-2": "scheduled",
    "-1": "implement",
    "0": "review",
    "3": "closed",
    "4": "canceled",
}

# ServiceNow incident state → human-readable
_INCIDENT_STATE_MAP = {
    "1": "new",
    "2": "in_progress",
    "3": "on_hold",
    "6": "resolved",
    "7": "closed",
    "8": "canceled",
}


class ServiceNowITSMConnector(SaaSBaseConnector):
    """DataBridge connector for ServiceNow ITSM tables."""

    _connector_name = "servicenow_itsm"
    _default_base_url = "https://{instance}.service-now.com"
    _endpoints = {
        "change_request": "/api/now/table/change_request",
        "incident": "/api/now/table/incident",
        "problem": "/api/now/table/problem",
        "sc_request": "/api/now/table/sc_request",
        "change_task": "/api/now/table/change_task",
        "health": "/api/now/table/sys_properties?sysparm_limit=1",
    }

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Build authentication headers from config.

        Supports Bearer token (OAuth) or Basic username/password.
        No credentials are hardcoded; everything is sourced from *config*.
        """
        if "bearer_token" in config:
            return {
                "Authorization": f"Bearer {config['bearer_token']}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        username = config.get("username", "")
        password = config.get("password", "")
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {
            "Authorization": f"Basic {creds}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def health_check(self) -> Dict[str, Any]:
        """Probe ServiceNow ITSM via a lightweight sys_properties call."""
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
            logger.error("ITSM health check failed: %s", exc)
            return {
                "status": "unhealthy",
                "error": str(exc),
                "connector": self._connector_name,
            }

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

    def fetch_table(
        self,
        table_name: str,
        params: Optional[Dict[str, Any]] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """Fetch all rows from a named ITSM table, handling pagination.

        Args:
            table_name: One of the keys in ``_endpoints`` (e.g. ``change_request``).
            params: Optional query-string parameters (e.g. ``{"state": "2"}``).
            limit: Page size (default 1000, ServiceNow maximum).

        Returns:
            List of row dicts.
        """
        endpoint = self._endpoints.get(table_name)
        if not endpoint:
            raise ValueError(
                f"Unknown table: {table_name!r}. Available: {list(self._endpoints)}"
            )
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

    def get_change_request(self, sys_id: str) -> Optional[Dict]:
        """Fetch a single change request by sys_id."""
        url = f"{self._base_url}/api/now/table/change_request/{sys_id}"
        try:
            raw = self._http_get(url)
            rows = self._extract_rows(raw)
            return rows[0] if rows else None
        except Exception as exc:
            logger.error("Failed to fetch change request %s: %s", sys_id, exc)
            return None

    def create_change_request(self, payload: Dict[str, Any]) -> Optional[Dict]:
        """Create a new change request ticket."""
        url = f"{self._base_url}/api/now/table/change_request"
        try:
            return self._http_post(url, payload)
        except Exception as exc:
            logger.error("Failed to create change request: %s", exc)
            return None

    def update_change_request(self, sys_id: str, payload: Dict[str, Any]) -> Optional[Dict]:
        """Update an existing change request ticket."""
        url = f"{self._base_url}/api/now/table/change_request/{sys_id}"
        try:
            return self._http_put(url, payload)
        except Exception as exc:
            logger.error("Failed to update change request %s: %s", sys_id, exc)
            return None

    def _http_put(self, url: str, data: Any) -> Any:
        """Execute authenticated HTTP PUT with JSON body, return parsed JSON."""
        import json
        from urllib.request import Request, urlopen

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ICDEV-DataBridge/1.0",
        }
        headers.update(self._auth_headers)
        payload = json.dumps(data).encode("utf-8") if data else None
        req = Request(url, data=payload, headers=headers, method="PUT")
        timeout = self._config.get("timeout", 30)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 — internal/configured endpoints only
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}

    def normalize_change_request(self, snow_row: Dict) -> Dict:
        """Map a raw ServiceNow change_request row to a clean dict."""

        def _val(v: Any) -> str:
            if isinstance(v, dict):
                return v.get("display_value") or v.get("value") or ""
            return str(v) if v is not None else ""

        def _raw_val(v: Any) -> str:
            if isinstance(v, dict):
                return str(v.get("value", "")) if v.get("value") is not None else ""
            return str(v) if v is not None else ""

        raw_state = _raw_val(snow_row.get("state"))
        return {
            "sys_id": _val(snow_row.get("sys_id")),
            "number": _val(snow_row.get("number")),
            "short_description": _val(snow_row.get("short_description")),
            "description": _val(snow_row.get("description")),
            "state": _CHANGE_REQUEST_STATE_MAP.get(raw_state, raw_state),
            "priority": _val(snow_row.get("priority")),
            "risk": _val(snow_row.get("risk")),
            "impact": _val(snow_row.get("impact")),
            "requested_by": _val(snow_row.get("requested_by")),
            "assignment_group": _val(snow_row.get("assignment_group")),
            "assigned_to": _val(snow_row.get("assigned_to")),
            "planned_start_date": _val(snow_row.get("start_date")),
            "planned_end_date": _val(snow_row.get("end_date")),
            "work_notes": _val(snow_row.get("work_notes")),
            "approval": _val(snow_row.get("approval")),
            "classification": "CUI",
        }
