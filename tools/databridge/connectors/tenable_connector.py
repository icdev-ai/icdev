# CUI // SP-CTI
"""DataBridge connector for Tenable.sc / Tenable.io.

Uses the Tenable REST API with stdlib urllib for air-gap compatibility.

Auth: API key (``access_key`` + ``secret_key``) or bearer token.

Logical table names map to pre-defined Tenable asset/vulnerability views.
Callers may pass a raw filter via ``ConnectorRequest.query``.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

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
    SchemaDefinition,
    SchemaField,
)
from tools.databridge.registry import register_connector

logger = get_logger("databridge.tenable")

REQUEST_TIMEOUT = 30
USER_AGENT = "ICDEV-DataBridge/1.0"

# Predefined Tenable.io endpoints for common "table" names
_ENDPOINTS: Dict[str, str] = {
    "scans": "/scans",
    "assets": "/assets",
    "vulnerabilities": "/workbenches/vulnerabilities",
    "workbench_assets": "/workbenches/assets",
    "plugins": "/plugins/families",
    "users": "/users",
    "groups": "/groups",
    "policies": "/policies",
    "scanner_groups": "/scanner-groups",
}


@register_connector
class TenableConnector(DataConnector):
    """Tenable.io / Tenable.sc REST API connector (DataBridge).

    Uses stdlib urllib exclusively — no pytenable dependency, safe for air-gap.

    Auth: API key pair (access_key + secret_key) or bearer token.
    """

    @property
    def connector_name(self) -> str:
        return "tenable"

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.SAAS_API

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,
            supports_schema_inference=True,
            max_batch_size=1_000,
            supported_formats=["json"],
        )

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._base_url: str = ""
        self._auth_headers: Dict[str, str] = {}
        self._connected: bool = False

    def connect(self, config: Dict[str, Any]) -> bool:
        """Establish connection to Tenable API.

        Args:
            config: Connection parameters. Expected keys:
                - base_url (default: https://cloud.tenable.com)
                - access_key / secret_key (API key auth)
                - bearer_token (alternative auth)
                - timeout (default: 30)
        """
        self._config = config
        self._base_url = config.get("base_url", "https://cloud.tenable.com").rstrip("/")

        access_key = config.get("access_key", "")
        secret_key = config.get("secret_key", "")
        if not access_key:
            access_key = os.environ.get("TENABLE_ACCESS_KEY", "")
        if not secret_key:
            secret_key = os.environ.get("TENABLE_SECRET_KEY", "")

        bearer_token = config.get("bearer_token", "")
        if not bearer_token:
            bearer_token = os.environ.get("TENABLE_BEARER_TOKEN", "")

        if bearer_token:
            self._auth_headers = {"Authorization": f"Bearer {bearer_token}"}
        elif access_key and secret_key:
            self._auth_headers = {
                "X-ApiKeys": f"accessKey={access_key}; secretKey={secret_key};"
            }
        else:
            logger.warning("No Tenable credentials provided. Set access_key/secret_key or bearer_token.")
            self._connected = False
            return False

        self._connected = True
        health = self.health_check()
        if health.get("status") != "healthy":
            logger.warning("Tenable health check failed: %s", health)
            self._connected = False
            return False
        return True

    def disconnect(self) -> None:
        """Clear connection state."""
        self._config = {}
        self._auth_headers = {}
        self._connected = False

    def health_check(self) -> Dict[str, Any]:
        """Probe Tenable API via the /scans endpoint (lightweight list)."""
        try:
            data = self._get("/scans")
            return {
                "status": "healthy",
                "connector": self.connector_name,
                "base_url": self._base_url,
                "scans_count": len(data.get("scans", [])),
            }
        except Exception as exc:  # noqa: BLE001  # any failure maps to unhealthy status
            logger.warning("Tenable health check error: %s", exc)
            return {
                "status": "unhealthy",
                "error": str(exc),
                "connector": self.connector_name,
            }

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read data from a Tenable endpoint mapped to table_name."""
        t0 = time.time()
        table = request.table_name or request.query

        if table not in _ENDPOINTS:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown table '{table}'. Available: {list(_ENDPOINTS.keys())}"],
            )

        path = _ENDPOINTS[table]
        params: Dict[str, Any] = {}
        if request.limit:
            params["limit"] = request.limit
        if request.filters:
            for key, value in request.filters.items():
                params[key] = value

        try:
            data = self._get(path, **params)
            rows = self._extract_rows(data, table)
            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows) if isinstance(rows, list) else 1,
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"endpoint": path, "table": table},
            )
        except HTTPError as exc:
            body = ""
            try:  # noqa: SIM105
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: S110, BLE001  # nosec B110 — best-effort body read; failure is benign
                pass
            logger.warning("Tenable HTTP error %s on %s: %s — %s", exc.code, path, exc.reason, body)
            return ConnectorResponse(
                status="error",
                errors=[f"HTTP {exc.code}: {exc.reason}", body],
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tenable read error on %s: %s", path, exc)
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:  # noqa: ARG002, ANN401  # interface stub — write not supported by this connector
        """Write is not supported for Tenable (read-only connector)."""
        return ConnectorResponse(
            status="error",
            errors=["Tenable connector does not support write operations."],
        )

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Infer schema by fetching a sample record from the table."""
        resp = self.read(ConnectorRequest(table_name=table_name, limit=1))
        if resp.status != "ok" or not resp.data:
            return SchemaDefinition(
                metadata={"source": self.connector_name, "table": table_name}
            )

        sample = resp.data[0] if isinstance(resp.data, list) else resp.data
        fields = []
        if isinstance(sample, dict):
            for key, val in sample.items():
                dt = "utf8"
                if isinstance(val, bool):
                    dt = "bool"
                elif isinstance(val, int):
                    dt = "int64"
                elif isinstance(val, float):
                    dt = "float64"
                elif isinstance(val, (list, dict)):
                    dt = "json"
                fields.append(SchemaField(name=key, data_type=dt))

        return SchemaDefinition(
            fields=fields,
            metadata={"source": self.connector_name, "table": table_name},
        )

    def list_tables(self) -> List[str]:
        """Return available endpoint names."""
        return list(_ENDPOINTS.keys())

    # -- HTTP helpers ----------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        """Return base headers merged with auth headers."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        headers.update(self._auth_headers)
        return headers

    def _get(self, path: str, **params: Any) -> Any:  # noqa: ANN401
        """Execute authenticated HTTP GET, return parsed JSON."""
        url = f"{self._base_url}{path}"
        if params:
            qs = urlencode({k: str(v) for k, v in params.items()})
            url = f"{url}?{qs}"

        # Intentional: urlopen with dynamic URL is safe here because `url` is built from
        # a configured base_url and a fixed _ENDPOINTS mapping, never raw user input.
        req = Request(url, headers=self._headers(), method="GET")  # noqa: S310  # nosec B310
        timeout = self._config.get("timeout", REQUEST_TIMEOUT)

        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}

    def _extract_rows(self, data: Any, table: str) -> List[Dict[str, Any]]:  # noqa: ARG002, ANN401
        """Extract row list from Tenable response envelope.

        Tenable endpoints vary in response shape:
          - /scans       -> {'scans': [...]}
          - /assets      -> {'assets': [...]}
          - /workbenches/vulnerabilities -> {'vulnerabilities': [...]}
          - /users       -> {'users': [...]}
          - /groups      -> {'groups': [...]}
          - /policies    -> {'policies': [...]}
        """
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            # Try known keys first
            for key in ("scans", "assets", "vulnerabilities", "users", "groups", "policies", "scanner_groups", "families"):
                if key in data:
                    val = data[key]
                    return val if isinstance(val, list) else [val] if val else []
            # Fallback: if dict has a single list value
            for val in data.values():
                if isinstance(val, list):
                    return val
            return [data]

        return []
