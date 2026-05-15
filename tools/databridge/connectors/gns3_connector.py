# CUI // SP-CTI
"""DataBridge connector for GNS3 Server REST API (v2).

All GNS3 traffic from NDC tools routes through this connector so that
secret resolution, audit logging, rate-limiting, and health probing go
through the standard DataBridge pipeline instead of raw urllib calls.

Logical table names map to GNS3 REST resource families:

  "version"    -> GET /v2/version                             (health)
  "projects"   -> GET /v2/projects
  "templates"  -> GET /v2/templates
  "computes"   -> GET /v2/computes
  "nodes"      -> GET /v2/projects/{project_id}/nodes         (filter: project_id)
  "links"      -> GET /v2/projects/{project_id}/links         (filter: project_id)
  "snapshots"  -> GET /v2/projects/{project_id}/snapshots     (filter: project_id)
  "drawings"   -> GET /v2/projects/{project_id}/drawings      (filter: project_id)
  "node_stats" -> GET /v2/projects/{project_id}/nodes/{node_id} (filter: project_id, node_id)
  "node_files" -> GET /v2/projects/{project_id}/files/{file_path} (filter: project_id, file_path)

Write operations use request.query as the HTTP method ("POST", "PUT", "DELETE",
"PATCH") and request.filters["path"] as the relative API path. The payload is
passed as the ``data`` argument.

Config keys (passed to connect()):
  base_url   - GNS3 server URL, e.g. "http://localhost:3080"  (required)
  username   - HTTP Basic auth username  (optional)
  password   - HTTP Basic auth password  (optional)
  token      - Bearer token alternative to Basic auth  (optional)
  timeout    - HTTP request timeout in seconds (default: 10)
"""

from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    SchemaDefinition,
    SchemaField,
)
from tools.databridge.connectors.saas_base import SaaSBaseConnector
from tools.databridge.registry import register_connector

_READ_ENDPOINTS: Dict[str, str] = {
    "version": "/v2/version",
    "projects": "/v2/projects",
    "templates": "/v2/templates",
    "computes": "/v2/computes",
    # Project-scoped (require filter: project_id)
    "nodes": "/v2/projects/{project_id}/nodes",
    "links": "/v2/projects/{project_id}/links",
    "snapshots": "/v2/projects/{project_id}/snapshots",
    "drawings": "/v2/projects/{project_id}/drawings",
    # Node-scoped (require filter: project_id + node_id)
    "node_stats": "/v2/projects/{project_id}/nodes/{node_id}",
    # File-scoped (require filter: project_id + file_path)
    "node_files": "/v2/projects/{project_id}/files/{file_path}",
}

_PROJECT_SCOPED = {"nodes", "links", "snapshots", "drawings"}
_NODE_SCOPED = {"node_stats"}
_FILE_SCOPED = {"node_files"}


@register_connector
class GNS3Connector(SaaSBaseConnector):
    """GNS3 Server REST API v2 — DataBridge connector.

    Single egress point for all GNS3 HTTP traffic from NDC tools.
    """

    _connector_name = "gns3"
    _default_base_url = "http://localhost:3080"
    _endpoints = _READ_ENDPOINTS

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        token = config.get("token", "")
        if token:
            return {"Authorization": f"Bearer {token}"}
        username = config.get("username", "")
        password = config.get("password", "")
        if username or password:
            raw = f"{username}:{password}".encode("utf-8")
            b64 = base64.b64encode(raw).decode("ascii")
            return {"Authorization": f"Basic {b64}"}
        return {}

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        try:
            data = self._http_get(f"{self._base_url}/v2/version")
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "base_url": self._base_url,
                "gns3_version": data.get("version", "unknown"),
                "local": data.get("local"),
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "error": str(exc),
                "connector": self._connector_name,
            }

    # ── Read ──────────────────────────────────────────────────────────────────

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """GET a GNS3 resource.

        Filtering rules by table:
          - project-scoped tables: supply filters["project_id"]
          - node_stats: supply filters["project_id"] and filters["node_id"]
          - node_files: supply filters["project_id"] and filters["file_path"]
        """
        import time

        start = time.monotonic()

        table = request.table_name
        path_tpl = _READ_ENDPOINTS.get(table)
        if not path_tpl:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown GNS3 table: {table!r}. Valid: {sorted(_READ_ENDPOINTS)}"],
            )

        filters = request.filters or {}
        project_id = filters.get("project_id", "")

        if table in _PROJECT_SCOPED and not project_id:
            return ConnectorResponse(
                status="error",
                errors=[f"Table '{table}' requires filters['project_id']"],
            )
        if table in _NODE_SCOPED and not (project_id and filters.get("node_id")):
            return ConnectorResponse(
                status="error",
                errors=[f"Table '{table}' requires filters['project_id'] and filters['node_id']"],
            )
        if table in _FILE_SCOPED and not (project_id and filters.get("file_path")):
            return ConnectorResponse(
                status="error",
                errors=[f"Table '{table}' requires filters['project_id'] and filters['file_path']"],
            )

        path = path_tpl.format(
            project_id=urllib.parse.quote(project_id, safe=""),
            node_id=urllib.parse.quote(filters.get("node_id", ""), safe=""),
            file_path=filters.get("file_path", ""),  # preserve slashes in file paths
        )
        url = f"{self._base_url}{path}"

        try:
            raw = self._http_get(url)
        except Exception as exc:
            dur = int((time.monotonic() - start) * 1000)
            return ConnectorResponse(status="error", errors=[str(exc)], duration_ms=dur)

        rows = raw if isinstance(raw, list) else [raw]
        if request.limit:
            rows = rows[: request.limit]

        dur = int((time.monotonic() - start) * 1000)
        return ConnectorResponse(
            status="ok",
            data=rows,
            row_count=len(rows),
            duration_ms=dur,
        )

    # ── Write (POST / PUT / DELETE / PATCH) ───────────────────────────────────

    def write(self, request: ConnectorRequest, data: Any = None) -> ConnectorResponse:
        """Send a mutating request to GNS3.

        request.query          : HTTP method  ("POST", "PUT", "DELETE", "PATCH")
        request.filters["path"]: relative API path, e.g. "/v2/projects/{pid}/nodes/start"
        data                   : JSON-serialisable body (omit for DELETE / parameterless POST)
        """
        import time

        start = time.monotonic()

        method = (request.query or "POST").upper()
        path = (request.filters or {}).get("path", "")
        if not path:
            return ConnectorResponse(status="error", errors=["filters['path'] required for write()"])

        url = f"{self._base_url}{path}"
        try:
            result = self._http_method(method, url, data)
        except Exception as exc:
            dur = int((time.monotonic() - start) * 1000)
            return ConnectorResponse(status="error", errors=[str(exc)], duration_ms=dur)

        dur = int((time.monotonic() - start) * 1000)
        rows = [result] if isinstance(result, dict) else (result or [])
        return ConnectorResponse(
            status="ok",
            data=rows,
            row_count=len(rows) if isinstance(rows, list) else 1,
            duration_ms=dur,
        )

    # ── Schema inference ──────────────────────────────────────────────────────

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        _schemas: Dict[str, List[SchemaField]] = {
            "projects": [SchemaField("project_id"), SchemaField("name"), SchemaField("status")],
            "nodes": [
                SchemaField("node_id"),
                SchemaField("name"),
                SchemaField("node_type"),
                SchemaField("status"),
            ],
            "links": [SchemaField("link_id"), SchemaField("nodes", "object")],
            "templates": [
                SchemaField("template_id"),
                SchemaField("name"),
                SchemaField("template_type"),
            ],
            "drawings": [SchemaField("drawing_id"), SchemaField("svg"), SchemaField("x"), SchemaField("y")],
            "node_stats": [
                SchemaField("node_id"),
                SchemaField("name"),
                SchemaField("status"),
                SchemaField("node_type"),
                SchemaField("properties", "object"),
            ],
            "node_files": [SchemaField("data", "object")],
            "version": [SchemaField("version"), SchemaField("local", "bool")],
        }
        return SchemaDefinition(
            table_name=table_name,
            fields=_schemas.get(table_name, [SchemaField("data", "object")]),
        )

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,
            supports_schema_inference=True,
            supports_incremental=False,
            max_batch_size=500,
            supported_formats=["json"],
        )

    # ── Internal HTTP helpers ─────────────────────────────────────────────────

    def _http_method(self, method: str, url: str, body: Any = None) -> Any:
        """Execute an arbitrary HTTP method with optional JSON body."""
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "ICDEV-DataBridge/1.0",
        }
        headers.update(self._auth_headers)

        payload: Optional[bytes] = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        timeout = self._config.get("timeout", 10)
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read()
                if not raw:
                    return {"_ok": True, "_status": resp.status}
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    return {"_ok": True, "_raw": raw.decode("utf-8", errors="replace")}
                return parsed
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {body_text}") from exc
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise RuntimeError(f"GNS3 unreachable: {exc}") from exc
