# CUI // SP-CTI
"""DataBridge connector for Megaport API v2.

Manages services (ports, VXCs, MCRs) through the Megaport REST API.

Auth: JWT via /v2/login (username + password). Token TTL: 24h.
Config keys: username, password  (or env: MEGAPORT_USER, MEGAPORT_PASS).
Base URL: https://api.megaport.com/v2

Tables:
  ports     — Physical Megaport ports at data centers
  vxcs      — Virtual Cross Connects (A-End → B-End Layer 2 circuits)
  mcrs      — Megaport Cloud Routers (virtual BGP routers)
  services  — All services aggregated (ports + VXCs + MCRs)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
)

logger = logging.getLogger("databridge.megaport")

_BASE_URL = "https://api.megaport.com/v2"
_REQUEST_TIMEOUT = 30
_USER_AGENT = "ICDEV-DataBridge/1.0"

_TABLES = ["ports", "vxcs", "mcrs", "services"]


class MegaportConnector(DataConnector):
    """Megaport API v2 connector."""

    @property
    def connector_name(self) -> str:
        return "megaport"

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.SAAS_API

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,
            supports_schema_inference=False,
            max_batch_size=500,
            supported_formats=["json"],
        )

    def __init__(self) -> None:
        self._username = os.environ.get("MEGAPORT_USER", "")
        self._password = os.environ.get("MEGAPORT_PASS", "")
        self._token: str = ""
        self._token_expires: float = 0.0
        self._connected: bool = False

    def connect(self, config: Dict[str, Any]) -> bool:
        self._username = config.get("username") or self._username
        self._password = config.get("password") or self._password
        try:
            self._get_token()
            self._connected = True
        except Exception as exc:
            logger.error("Megaport connect failed: %s", exc)
            self._connected = False
        return self._connected

    def disconnect(self) -> None:
        self._token = ""
        self._connected = False

    def list_tables(self) -> List[str]:
        return _TABLES

    def infer_schema(self, table_name: str) -> Dict[str, Any]:
        schemas = {
            "ports": {
                "uid": "string", "name": "string", "type": "string",
                "provisioning_status": "string", "speed": "integer",
                "location_id": "integer", "location": "string",
                "country": "string", "monthly_rate": "float",
            },
            "vxcs": {
                "uid": "string", "name": "string", "provisioning_status": "string",
                "rate_limit": "integer", "a_end_uid": "string", "b_end_uid": "string",
                "a_end_location": "string", "b_end_location": "string",
                "a_end_vlan": "integer", "b_end_vlan": "integer",
                "term": "integer", "monthly_rate": "float",
            },
            "mcrs": {
                "uid": "string", "name": "string", "provisioning_status": "string",
                "speed": "integer", "asn": "integer", "location": "string",
                "monthly_rate": "float",
            },
            "services": {
                "uid": "string", "name": "string", "type": "string",
                "provisioning_status": "string", "location": "string",
                "monthly_rate": "float",
            },
        }
        return schemas.get(table_name, {})

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires - 120:
            return self._token

        if not self._username or not self._password:
            raise RuntimeError("MEGAPORT_USER and MEGAPORT_PASS must be set")

        payload = json.dumps({"username": self._username, "password": self._password}).encode()
        req = Request(
            f"{_BASE_URL}/login",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
        except HTTPError as exc:
            raise RuntimeError(f"Megaport login failed HTTP {exc.code}") from exc

        token_data = data.get("data", {})
        self._token = token_data.get("token", "")
        if not self._token:
            raise RuntimeError("Megaport login response missing token")
        self._token_expires = time.time() + 86400  # 24h TTL
        return self._token

    def _api(self, method: str, path: str, body: dict | None = None) -> dict:
        token = self._get_token()
        url = f"{_BASE_URL}{path}"
        data = json.dumps(body).encode() if body else None
        headers = {
            "X-Auth-Token": token,
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
                f"Megaport {method} {path} → HTTP {exc.code}: {body_txt}"
            ) from exc

    # ── Read ──────────────────────────────────────────────────────────────────

    def read(self, req: ConnectorRequest) -> ConnectorResponse:
        table = req.table_name
        try:
            if table == "services":
                rows = self._fetch_all_services()
            elif table == "ports":
                rows = self._fetch_by_type("MEGAPORT")
            elif table == "vxcs":
                rows = self._fetch_vxcs()
            elif table == "mcrs":
                rows = self._fetch_by_type("MCR2")
            else:
                return ConnectorResponse(status="error", errors=[f"Unknown table '{table}'"])
            return ConnectorResponse(status="ok", data=rows, row_count=len(rows))
        except Exception as exc:
            logger.error("Megaport read %s: %s", table, exc)
            return ConnectorResponse(status="error", errors=[str(exc)])

    def _fetch_all_services(self) -> list[dict]:
        data = self._api("GET", "/services")
        return [
            {
                "uid": s.get("productUid", ""),
                "name": s.get("productName", ""),
                "type": s.get("productType", ""),
                "provisioning_status": s.get("provisioningStatus", ""),
                "location": s.get("locationId", ""),
                "monthly_rate": s.get("costMonth", 0.0),
            }
            for s in data.get("data", [])
        ]

    def _fetch_by_type(self, product_type: str) -> list[dict]:
        data = self._api("GET", "/services")
        rows = []
        for s in data.get("data", []):
            if s.get("productType") != product_type:
                continue
            row: dict[str, Any] = {
                "uid": s.get("productUid", ""),
                "name": s.get("productName", ""),
                "type": s.get("productType", ""),
                "provisioning_status": s.get("provisioningStatus", ""),
                "speed": s.get("portSpeed", s.get("speed", 0)),
                "location_id": s.get("locationId", 0),
                "location": s.get("locationDetail", {}).get("name", ""),
                "country": s.get("locationDetail", {}).get("country", ""),
                "marketplace_visibility": s.get("marketplaceVisibility", False),
                "term": s.get("term", 0),
                "monthly_rate": s.get("costMonth", 0.0),
            }
            if product_type == "MCR2":
                row["asn"] = s.get("virtualRouterDetails", {}).get("mcrAsn", 0)
            rows.append(row)
        return rows

    def _fetch_vxcs(self) -> list[dict]:
        data = self._api("GET", "/services")
        rows = []
        for s in data.get("data", []):
            if s.get("productType") != "VXC":
                continue
            a = s.get("aEnd", {})
            b = s.get("bEnd", {})
            rows.append({
                "uid": s.get("productUid", ""),
                "name": s.get("productName", ""),
                "provisioning_status": s.get("provisioningStatus", ""),
                "rate_limit": s.get("rateLimit", 0),
                "a_end_uid": a.get("productUid", ""),
                "b_end_uid": b.get("productUid", ""),
                "a_end_location": a.get("locationDetail", {}).get("name", ""),
                "b_end_location": b.get("locationDetail", {}).get("name", ""),
                "a_end_vlan": a.get("vlan", 0),
                "b_end_vlan": b.get("vlan", 0),
                "term": s.get("term", 0),
                "monthly_rate": s.get("costMonth", 0.0),
                "created_at": s.get("createDate", ""),
            })
        return rows

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, req: ConnectorRequest) -> ConnectorResponse:
        if req.table_name == "vxcs":
            try:
                result = self._api("POST", "/services/vxc", body=req.filters or {})
                return ConnectorResponse(status="ok", data=[result.get("data", {})], row_count=1)
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])
        return ConnectorResponse(status="error", errors=[f"Write not supported for '{req.table_name}'"])

    def health_check(self) -> Dict[str, Any]:
        try:
            self._get_token()
            return {"status": "healthy", "connector": "megaport"}
        except Exception as exc:
            return {"status": "unhealthy", "connector": "megaport", "error": str(exc)}
