# CUI // SP-CTI
"""ICDEV™ Network Design Canvas — GNS3 Server REST API Adapter.

Production adapter for GNS3 server (https://docs.gns3.com/docs/api/). Wraps the
/v2 REST API using stdlib urllib.request (no external deps). Designed for the
NDC (Network Design Canvas) lab backend registry pattern.

All methods fail gracefully — connection/HTTP errors return a structured
``{"status": "unreachable"|"error", "error": "..."}`` dict rather than raising,
so the NDC orchestrator can degrade cleanly when a lab backend is offline.

Usage::

    from tools.network.adapters.gns3_adapter import GNS3Adapter
    adapter = GNS3Adapter("http://gns3:3080", username="admin", password="s3cr3t")
    print(adapter.health())
    proj = adapter.create_project("test-topology")
"""

from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT = 10.0


class GNS3Adapter:
    """GNS3 Server REST API wrapper (v2).

    Auth: HTTP Basic (username/password) OR bearer token if the deployment
    front-ends GNS3 behind an auth proxy. GNS3 core server ships Basic auth.
    """

    def __init__(
        self,
        url: str,
        username: str = "",
        password: str = "",
        token: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._token = token
        self._timeout = timeout

    # ------------------------------------------------------------------ #
    # Internal HTTP helpers
    # ------------------------------------------------------------------ #

    def _auth_header(self) -> Dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        if self._username or self._password:
            raw = f"{self._username}:{self._password}".encode("utf-8")
            b64 = base64.b64encode(raw).decode("ascii")
            return {"Authorization": f"Basic {b64}"}
        return {}

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute an HTTP request; return parsed JSON or structured error."""
        full_url = f"{self._url}{path}"
        data: Optional[bytes] = None
        headers: Dict[str, str] = {"Accept": "application/json"}
        headers.update(self._auth_header())
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(  # noqa: S310 — internal mgmt VLAN only
            full_url, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(  # noqa: S310
                req, timeout=self._timeout
            ) as resp:
                raw = resp.read()
                if not raw:
                    return {"_ok": True, "_status": resp.status}
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    return {
                        "_ok": True,
                        "_status": resp.status,
                        "_raw": raw.decode("utf-8", errors="replace"),
                    }
                if isinstance(parsed, dict):
                    parsed.setdefault("_ok", True)
                    parsed.setdefault("_status", resp.status)
                    return parsed
                return {"_ok": True, "_status": resp.status, "_data": parsed}
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            return {
                "status": "error",
                "error": f"HTTP {exc.code}: {exc.reason}",
                "body": body_text,
                "_status": exc.code,
            }
        except urllib.error.URLError as exc:
            return {"status": "unreachable", "error": str(exc.reason)}
        except socket.timeout:
            return {"status": "unreachable", "error": "timeout"}
        except (OSError, ConnectionError) as exc:
            return {"status": "unreachable", "error": str(exc)}

    @staticmethod
    def _is_error(resp: Dict[str, Any]) -> bool:
        return resp.get("status") in ("error", "unreachable")

    def _get_list(self, path: str) -> List[Dict[str, Any]]:
        resp = self._request("GET", path)
        if self._is_error(resp):
            return []
        if isinstance(resp, dict) and "_data" in resp and isinstance(
            resp["_data"], list
        ):
            return resp["_data"]
        # GNS3 returns JSON arrays; urlopen path wraps non-dict responses.
        if isinstance(resp, list):
            return resp
        return []

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def health(self) -> Dict[str, Any]:
        """GET /v2/version — report reachability + version string."""
        resp = self._request("GET", "/v2/version")
        if self._is_error(resp):
            return {
                "version": None,
                "status": "unreachable",
                "error": resp.get("error", "unknown"),
            }
        return {
            "version": resp.get("version"),
            "local": resp.get("local"),
            "status": "ok",
        }

    def create_project(
        self, name: str, path: Optional[str] = None
    ) -> Dict[str, Any]:
        """POST /v2/projects."""
        body: Dict[str, Any] = {"name": name}
        if path:
            body["path"] = path
        resp = self._request("POST", "/v2/projects", body=body)
        if self._is_error(resp):
            return resp
        return {
            "project_id": resp.get("project_id"),
            "name": resp.get("name"),
            "path": resp.get("path"),
            "status": resp.get("status", "opened"),
        }

    def list_projects(self) -> List[Dict[str, Any]]:
        """GET /v2/projects."""
        return self._get_list("/v2/projects")

    def list_nodes(self, project_id: str) -> List[Dict[str, Any]]:
        """GET /v2/projects/{project_id}/nodes.

        The read counterpart of :meth:`add_node`. This adapter could build a
        topology and never enumerate one, so asset discovery had no way to see
        what a lab actually contains (rmf-disc-01). Purely additive.
        """
        return self._get_list(
            f"/v2/projects/{urllib.parse.quote(project_id)}/nodes"
        )

    def delete_project(self, project_id: str) -> bool:
        """DELETE /v2/projects/{project_id}."""
        resp = self._request(
            "DELETE", f"/v2/projects/{urllib.parse.quote(project_id)}"
        )
        return not self._is_error(resp)

    def add_node(
        self,
        project_id: str,
        name: str,
        node_type: str,
        compute_id: str = "local",
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """POST /v2/projects/{project_id}/nodes.

        node_type: one of qemu, docker, vpcs, cloud, nat, dynamips (router),
        iou, ethernet_hub, ethernet_switch, frame_relay_switch, atm_switch.
        """
        body: Dict[str, Any] = {
            "name": name,
            "node_type": node_type,
            "compute_id": compute_id,
        }
        if properties:
            body["properties"] = properties
        path = f"/v2/projects/{urllib.parse.quote(project_id)}/nodes"
        resp = self._request("POST", path, body=body)
        return resp

    def link_nodes(
        self,
        project_id: str,
        a_node_id: str,
        a_port: int,
        b_node_id: str,
        b_port: int,
    ) -> Dict[str, Any]:
        """POST /v2/projects/{project_id}/links.

        GNS3 links require (node_id, adapter_number, port_number) tuples. We
        treat the caller's ``port`` as ``port_number`` with adapter 0, which
        is the convention for most single-adapter node types (vpcs, switch).
        Callers needing multi-adapter routing should extend the call site.
        """
        body = {
            "nodes": [
                {
                    "node_id": a_node_id,
                    "adapter_number": 0,
                    "port_number": a_port,
                },
                {
                    "node_id": b_node_id,
                    "adapter_number": 0,
                    "port_number": b_port,
                },
            ]
        }
        path = f"/v2/projects/{urllib.parse.quote(project_id)}/links"
        return self._request("POST", path, body=body)

    def start_topology(self, project_id: str) -> Dict[str, Any]:
        """POST /v2/projects/{project_id}/nodes/start — start all nodes."""
        path = (
            f"/v2/projects/{urllib.parse.quote(project_id)}/nodes/start"
        )
        resp = self._request("POST", path)
        if self._is_error(resp):
            return resp
        return {"status": "started", "project_id": project_id}

    def stop_topology(self, project_id: str) -> Dict[str, Any]:
        """POST /v2/projects/{project_id}/nodes/stop — stop all nodes."""
        path = f"/v2/projects/{urllib.parse.quote(project_id)}/nodes/stop"
        resp = self._request("POST", path)
        if self._is_error(resp):
            return resp
        return {"status": "stopped", "project_id": project_id}

    def snapshot(self, project_id: str, name: str) -> Dict[str, Any]:
        """POST /v2/projects/{project_id}/snapshots."""
        path = f"/v2/projects/{urllib.parse.quote(project_id)}/snapshots"
        return self._request("POST", path, body={"name": name})

    def capture(
        self,
        project_id: str,
        node_id: str,
        port: int,
        file: str = "capture.pcap",
        adapter: int = 0,
    ) -> Dict[str, Any]:
        """Start packet capture on a node port.

        POST /v2/projects/{project_id}/nodes/{node_id}/adapters/{adapter}
             /ports/{port}/start_capture
        """
        path = (
            f"/v2/projects/{urllib.parse.quote(project_id)}"
            f"/nodes/{urllib.parse.quote(node_id)}"
            f"/adapters/{adapter}/ports/{port}/start_capture"
        )
        body = {"capture_file_name": file, "data_link_type": "DLT_EN10MB"}
        return self._request("POST", path, body=body)


# Registration for the NDC adapter registry pattern
def get_adapter(url: str, **kwargs: Any) -> GNS3Adapter:
    return GNS3Adapter(url, **kwargs)
