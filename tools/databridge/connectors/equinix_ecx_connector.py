# CUI // SP-CTI
"""DataBridge connector for Equinix ECX Fabric API v2.

Manages cross-connects, virtual connections, ports, and LOA requests
through the Equinix Fabric REST API.

Auth: OAuth2 client credentials (config keys: client_id, client_secret).
Env override: EQUINIX_ECX_CLIENT_ID, EQUINIX_ECX_CLIENT_SECRET.
Base URL: https://api.equinix.com/fabric/v4

Tables:
  connections   — Virtual connections (A-side → Z-side)
  ports         — Physical ports at Equinix facilities
  loa_requests  — Letter of Authorization requests
  metrics       — Port utilization metrics (requires resource_uuid filter)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
)

logger = logging.getLogger("databridge.equinix_ecx")

_BASE_URL = "https://api.equinix.com/fabric/v4"
_TOKEN_URL = "https://api.equinix.com/oauth2/v2/token"
_REQUEST_TIMEOUT = 30
_USER_AGENT = "ICDEV-DataBridge/1.0"

_TABLES = ["connections", "ports", "loa_requests", "metrics"]


class EquinixECXConnector(DataConnector):
    """Equinix ECX Fabric API v2 connector."""

    @property
    def connector_name(self) -> str:
        return "equinix_ecx"

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.SAAS_API

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,
            supports_schema_inference=False,
            max_batch_size=25,
            supported_formats=["json"],
        )

    def __init__(self) -> None:
        self._client_id = os.environ.get("EQUINIX_ECX_CLIENT_ID", "")
        self._client_secret = os.environ.get("EQUINIX_ECX_CLIENT_SECRET", "")
        self._token: str = ""
        self._token_expires: float = 0.0
        self._connected: bool = False

    def connect(self, config: Dict[str, Any]) -> bool:
        self._client_id = config.get("client_id") or self._client_id
        self._client_secret = config.get("client_secret") or self._client_secret
        try:
            self._get_token()
            self._connected = True
        except Exception as exc:
            logger.error("Equinix ECX connect failed: %s", exc)
            self._connected = False
        return self._connected

    def disconnect(self) -> None:
        self._token = ""
        self._connected = False

    def list_tables(self) -> List[str]:
        return _TABLES

    def infer_schema(self, table_name: str) -> Dict[str, Any]:
        schemas = {
            "connections": {
                "uuid": "string", "name": "string", "type": "string",
                "state": "string", "bandwidth": "integer",
                "a_side_port_uuid": "string", "z_side_port_uuid": "string",
                "a_side_location": "string", "z_side_location": "string",
                "created_at": "string",
            },
            "ports": {
                "uuid": "string", "name": "string", "type": "string",
                "state": "string", "bandwidth": "integer",
                "used_bandwidth": "integer", "ibx_code": "string",
                "metro_code": "string", "encapsulation": "string",
            },
            "loa_requests": {
                "uuid": "string", "port_uuid": "string", "state": "string",
                "requested_at": "string", "ibx_code": "string",
                "contact_name": "string", "contact_email": "string",
            },
            "metrics": {
                "resource_uuid": "string", "resource_type": "string",
                "interval_start": "string", "interval_end": "string",
                "ingress_mbps_avg": "float", "egress_mbps_avg": "float",
                "utilization_pct": "float",
            },
        }
        return schemas.get(table_name, {})

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token

        if not self._client_id or not self._client_secret:
            raise RuntimeError(
                "EQUINIX_ECX_CLIENT_ID and EQUINIX_ECX_CLIENT_SECRET must be set"
            )

        payload = urlencode({
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }).encode()
        req = Request(
            _TOKEN_URL,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        try:
            with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
        except HTTPError as exc:
            raise RuntimeError(f"Equinix token fetch failed HTTP {exc.code}") from exc

        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 1800))
        return self._token

    def _api(self, method: str, path: str, body: dict | None = None) -> dict:
        token = self._get_token()
        url = f"{_BASE_URL}{path}"
        data = json.dumps(body).encode() if body else None
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            body_txt = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Equinix ECX {method} {path} → HTTP {exc.code}: {body_txt}"
            ) from exc

    # ── Read ──────────────────────────────────────────────────────────────────

    def read(self, req: ConnectorRequest) -> ConnectorResponse:
        table = req.table_name
        limit = req.limit or 25
        try:
            if table == "connections":
                data = self._api("GET", f"/connections?limit={limit}")
                rows = [self._map_connection(c) for c in data.get("data", [])]
            elif table == "ports":
                data = self._api("GET", f"/ports?limit={limit}")
                rows = [self._map_port(p) for p in data.get("data", [])]
            elif table == "loa_requests":
                data = self._api("GET", f"/loas?limit={limit}")
                rows = [self._map_loa(l) for l in data.get("data", [])]
            elif table == "metrics":
                resource_uuid = (req.filters or {}).get("resource_uuid", "")
                if not resource_uuid:
                    return ConnectorResponse(status="error", errors=["resource_uuid filter required"])
                resource_type = (req.filters or {}).get("resource_type", "port").lower()
                data = self._api("GET", f"/{resource_type}s/{resource_uuid}/stats")
                rows = [self._map_metric(resource_uuid, resource_type, s) for s in data.get("data", [])]
            else:
                return ConnectorResponse(status="error", errors=[f"Unknown table '{table}'"])
            return ConnectorResponse(status="ok", data=rows, row_count=len(rows))
        except Exception as exc:
            logger.error("Equinix ECX read %s: %s", table, exc)
            return ConnectorResponse(status="error", errors=[str(exc)])

    def _map_connection(self, c: dict) -> dict:
        a = c.get("aSide", {})
        z = c.get("zSide", {})
        return {
            "uuid": c.get("uuid", ""),
            "name": c.get("name", ""),
            "type": c.get("type", ""),
            "state": c.get("state", ""),
            "bandwidth": c.get("bandwidth", 0),
            "a_side_port_uuid": a.get("accessPoint", {}).get("port", {}).get("uuid", ""),
            "z_side_port_uuid": z.get("accessPoint", {}).get("port", {}).get("uuid", ""),
            "a_side_location": a.get("accessPoint", {}).get("location", {}).get("ibxCode", ""),
            "z_side_location": z.get("accessPoint", {}).get("location", {}).get("ibxCode", ""),
            "redundancy_priority": c.get("redundancy", {}).get("priority", ""),
            "created_at": c.get("changeLog", {}).get("createdDateTime", ""),
            "updated_at": c.get("changeLog", {}).get("updatedDateTime", ""),
        }

    def _map_port(self, p: dict) -> dict:
        loc = p.get("location", {})
        settings = p.get("settings", {})
        return {
            "uuid": p.get("uuid", ""),
            "name": p.get("name", ""),
            "type": p.get("type", ""),
            "state": p.get("state", ""),
            "bandwidth": p.get("bandwidth", 0),
            "used_bandwidth": p.get("usedBandwidth", 0),
            "ibx_code": loc.get("ibxCode", ""),
            "metro_code": loc.get("metroCode", ""),
            "encapsulation": settings.get("encapsulationType", ""),
        }

    def _map_loa(self, l: dict) -> dict:
        return {
            "uuid": l.get("uuid", ""),
            "port_uuid": l.get("portUuid", ""),
            "state": l.get("state", ""),
            "requested_at": l.get("requestedAt", ""),
            "issued_at": l.get("issuedAt", ""),
            "ibx_code": l.get("ibxCode", ""),
            "contact_name": l.get("contactName", ""),
            "contact_email": l.get("contactEmail", ""),
        }

    def _map_metric(self, resource_uuid: str, resource_type: str, s: dict) -> dict:
        return {
            "resource_uuid": resource_uuid,
            "resource_type": resource_type.upper(),
            "interval_start": s.get("startDateTime", ""),
            "interval_end": s.get("endDateTime", ""),
            "ingress_mbps_avg": s.get("ingressBandwidth", {}).get("avg", 0.0),
            "egress_mbps_avg": s.get("egressBandwidth", {}).get("avg", 0.0),
            "utilization_pct": s.get("utilizationPct", 0.0),
        }

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, req: ConnectorRequest) -> ConnectorResponse:
        table = req.table_name
        body = req.filters or {}
        try:
            if table == "connections":
                result = self._api("POST", "/connections", body=body)
                return ConnectorResponse(status="ok", data=[result], row_count=1)
            elif table == "loa_requests":
                result = self._api("POST", "/loas", body=body)
                return ConnectorResponse(status="ok", data=[result], row_count=1)
        except Exception as exc:
            return ConnectorResponse(status="error", errors=[str(exc)])
        return ConnectorResponse(status="error", errors=[f"Write not supported for '{table}'"])

    def create_connection(self, data: dict) -> dict:
        """POST /fabric/v4/connections — thin wrapper for use by xc_order_manager."""
        return self._api("POST", "/connections", body=data)

    def get_connection_status(self, uuid: str) -> dict:
        """GET /fabric/v4/connections/{uuid} — returns raw API response."""
        return self._api("GET", f"/connections/{uuid}")

    def cancel_connection(self, uuid: str) -> dict:
        """DELETE /fabric/v4/connections/{uuid} — returns raw API response."""
        return self._api("DELETE", f"/connections/{uuid}")

    def health_check(self) -> Dict[str, Any]:
        try:
            self._get_token()
            return {"status": "healthy", "connector": "equinix_ecx"}
        except Exception as exc:
            return {"status": "unhealthy", "connector": "equinix_ecx", "error": str(exc)}
