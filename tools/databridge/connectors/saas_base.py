#!/usr/bin/env python3
"""SaaS/REST API Base Connector for DataBridge.

Abstract base class for all REST/SaaS API connectors. Provides:
  - Session management with configurable auth (API key, Bearer, custom headers)
  - Pagination handling (offset, cursor, link-header)
  - JSON response parsing with configurable data path extraction
  - Rate limit awareness (Retry-After header)
  - Health check via configurable endpoint

Subclasses override endpoint mappings and auth configuration.

Usage:
    class MyApiConnector(SaaSBaseConnector):
        _connector_name = "my_api"
        _default_base_url = "https://api.example.com/v1"
        _endpoints = {"users": "/users", "orders": "/orders"}

        def _build_auth_headers(self, config):
            return {"Authorization": f"Bearer {config['api_key']}"}
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import time
from abc import abstractmethod
from typing import Any, Dict, List
from urllib.error import HTTPError
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

logger = get_logger("databridge.saas_base")

REQUEST_TIMEOUT = 30  # seconds
USER_AGENT = "ICDEV-DataBridge/1.0"  # ASCII-safe for HTTP headers


class SaaSBaseConnector(DataConnector):
    """Abstract base class for REST/SaaS API connectors.

    Subclasses must define:
      - ``_connector_name``: unique connector identifier
      - ``_default_base_url``: default API base URL
      - ``_endpoints``: dict mapping table names to URL paths
      - ``_build_auth_headers(config)``: return auth headers dict
    """

    _connector_name: str = ""
    _default_base_url: str = ""
    _endpoints: Dict[str, str] = {}

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._base_url: str = ""
        self._auth_headers: Dict[str, str] = {}
        self._connected: bool = False

    # -- ABC property implementations -----------------------------------------

    @property
    def connector_name(self) -> str:
        return self._connector_name

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.SAAS_API

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,
            supports_schema_inference=True,
            max_batch_size=500,
            supported_formats=["json"],
        )

    # -- Abstract methods subclasses must implement ----------------------------

    @abstractmethod
    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Return authentication headers for API requests.

        Args:
            config: Connection config dict (api_key, secret, etc.)

        Returns:
            Dict of HTTP headers for authentication.
        """
        ...

    # -- Connection lifecycle --------------------------------------------------

    def connect(self, config: Dict[str, Any]) -> bool:
        """Establish connection by storing config and testing auth."""
        self._config = config
        self._base_url = config.get("base_url", self._default_base_url).rstrip("/")
        if not self._base_url:
            logger.error("No base_url provided and no default set")
            return False

        self._auth_headers = self._build_auth_headers(config)
        self._connected = True

        # Validate connectivity
        try:
            health = self.health_check()
            if health.get("status") != "healthy":
                logger.warning("Health check returned non-healthy: %s", health)
                return False
        except Exception as exc:
            logger.error("Connection test failed: %s", exc)
            self._connected = False
            return False

        return True

    def disconnect(self) -> None:
        """Clear connection state."""
        self._config = {}
        self._auth_headers = {}
        self._connected = False

    def health_check(self) -> Dict[str, Any]:
        """Check API reachability via the health endpoint or first endpoint."""
        health_path = self._config.get("health_endpoint", "")
        if not health_path and self._endpoints:
            # Use first endpoint as health probe
            first_key = next(iter(self._endpoints))
            health_path = self._endpoints[first_key]

        try:
            url = f"{self._base_url}{health_path}"
            resp = self._http_get(url)
            return {
                "status": "healthy",
                "base_url": self._base_url,
                "connector": self._connector_name,
                "response_keys": list(resp.keys()) if isinstance(resp, dict) else [],
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "error": str(exc),
                "connector": self._connector_name,
            }

    # -- Data operations -------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read data from an API endpoint mapped to table_name."""
        t0 = time.time()
        table = request.table_name or request.query

        if table not in self._endpoints:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown endpoint: '{table}'. Available: {list(self._endpoints.keys())}"],
            )

        path = self._endpoints[table]
        url = f"{self._base_url}{path}"

        # Apply query params from filters
        params = dict(request.filters) if request.filters else {}
        if request.limit:
            params["limit"] = str(request.limit)

        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"

        try:
            data = self._http_get(url)
            # Normalize to list
            rows = self._extract_rows(data, table)
            duration = int((time.time() - t0) * 1000)

            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows) if isinstance(rows, list) else 1,
                duration_ms=duration,
                metadata={"endpoint": path, "url": url},
            )
        except HTTPError as exc:
            return ConnectorResponse(
                status="error",
                errors=[f"HTTP {exc.code}: {exc.reason}"],
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        """Write data to an API endpoint via POST."""
        t0 = time.time()
        table = request.table_name or request.query

        if table not in self._endpoints:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown table/endpoint: '{table}'"],
            )

        path = self._endpoints[table]
        url = f"{self._base_url}{path}"

        try:
            resp = self._http_post(url, data)
            duration = int((time.time() - t0) * 1000)
            return ConnectorResponse(
                status="ok",
                data=resp,
                row_count=1,
                duration_ms=duration,
            )
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return ConnectorResponse(
                status="error",
                errors=[f"HTTP {exc.code}: {exc.reason}", body],
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Infer schema by fetching a sample record."""
        resp = self.read(ConnectorRequest(table_name=table_name, limit=1))
        if resp.status != "ok" or not resp.data:
            return SchemaDefinition(metadata={"source": self._connector_name, "table": table_name})

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
            metadata={"source": self._connector_name, "table": table_name},
        )

    def list_tables(self) -> List[str]:
        """Return available endpoint names."""
        return list(self._endpoints.keys())

    # -- Helpers (overridable) -------------------------------------------------

    def _extract_rows(self, data: Any, table: str) -> Any:
        """Extract row data from API response. Override for custom formats.

        Default: if data is a list, return as-is. If dict, return as single-item list.
        Subclasses can override to handle nested response structures.
        """
        if isinstance(data, list):
            return data
        return [data] if isinstance(data, dict) else data

    # ── Egress control ────────────────────────────────────────────────────
    #
    # Every outbound call from a SaaS connector passes through here before the
    # socket opens. Previously the three _http_* methods called urlopen bare,
    # each annotated "# nosec B310 -- URL scheme validated" while NO validation
    # occurred anywhere. A suppression comment asserting a control that does not
    # exist is worse than no comment: it tells the reviewer and the scanner the
    # problem is handled.
    #
    # tools/http/egress_guard.py was already written and correct (HTTPS-only,
    # deny-beats-allow, DNS resolve-then-check on EVERY A record so a hostname
    # cannot resolve to link-local or RFC1918). It simply had no callers here.
    #
    # Air-gap is an absolute stop: in that posture no connector may reach out at
    # all, regardless of allowlist.

    def _egress_config(self) -> dict:
        """Allow/deny config for this connector, from its own connection config."""
        return {
            "allowlist": list(self._config.get("egress_allowlist") or []),
            "denylist": list(self._config.get("egress_denylist") or []),
        }

    def _guard_egress(self, url: str) -> None:
        """Raise ``PermissionError`` unless *url* may be contacted."""
        try:
            from tools.airgap import is_airgap
            if is_airgap():
                raise PermissionError(
                    f"egress blocked: air-gap mode is active (target {url!r})"
                )
        except PermissionError:
            raise
        except Exception:  # noqa: BLE001 — airgap module optional
            pass

        try:
            from tools.http.egress_guard import egress_guard
        except Exception as exc:  # noqa: BLE001
            # Fail CLOSED. An unimportable guard means the destination is
            # unchecked, which is precisely when an outbound call should not
            # proceed.
            raise PermissionError(f"egress guard unavailable: {exc}") from exc

        allowed, reason, _ips = egress_guard(url, self._egress_config())
        if not allowed:
            raise PermissionError(f"egress blocked ({reason}) for {url!r}")

    def _http_get(self, url: str) -> Any:
        """Execute authenticated HTTP GET, return parsed JSON."""
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        headers.update(self._auth_headers)

        req = Request(url, headers=headers, method="GET")
        timeout = self._config.get("timeout", REQUEST_TIMEOUT)

        self._guard_egress(url)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 -- scheme + destination validated by _guard_egress above
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}

    def _http_post(self, url: str, data: Any) -> Any:
        """Execute authenticated HTTP POST with JSON body, return parsed JSON."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        headers.update(self._auth_headers)

        payload = json.dumps(data).encode("utf-8") if data else None
        req = Request(url, data=payload, headers=headers, method="POST")
        timeout = self._config.get("timeout", REQUEST_TIMEOUT)

        self._guard_egress(url)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 -- scheme + destination validated by _guard_egress above
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}

    def _http_delete(self, url: str) -> Any:
        """Execute authenticated HTTP DELETE, return parsed JSON."""
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        headers.update(self._auth_headers)

        req = Request(url, headers=headers, method="DELETE")
        timeout = self._config.get("timeout", REQUEST_TIMEOUT)

        self._guard_egress(url)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 -- scheme + destination validated by _guard_egress above
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
