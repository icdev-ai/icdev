# CUI // SP-CTI
"""DataBridge connector for Splunk Enterprise / Splunk Cloud.

Uses the Splunk REST API (port 8089) with stdlib urllib — no splunklib
dependency for air-gap compatibility.

Auth: HTTP Basic (username + password) or Bearer token (``splunk_token``).

Logical table names map to pre-defined SPL searches.  Callers may pass a
raw SPL query via ``ConnectorRequest.query`` to execute arbitrary searches.

Search lifecycle:
  1. POST /services/search/jobs  → returns sid
  2. Poll GET /services/search/jobs/<sid>  until dispatchState == "DONE"
  3. GET /services/search/jobs/<sid>/results  → rows
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import base64
import json
import logging
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

logger = get_logger("databridge.splunk")

REQUEST_TIMEOUT = 30
POLL_INTERVAL = 1.0   # seconds between job status checks
MAX_POLL_WAIT = 60    # seconds before giving up
USER_AGENT = "ICDEV-DataBridge/1.0"

# Predefined SPL searches for common "table" names
_SPL: Dict[str, str] = {
    "devices": (
        "| inputlookup asset_lookup "
        "| table hostname ip os vendor category"
    ),
    "interfaces": (
        "index=netops sourcetype=snmp OID=*ifTable* "
        "| stats latest(ifOperStatus) as status, latest(ifSpeed) as speed by host, ifDescr "
        "| rename host as hostname, ifDescr as interface_name"
    ),
    "stats": (
        "index=netops sourcetype=performance "
        "| stats avg(cpu_pct) as avg_cpu, avg(mem_pct) as avg_mem by host "
        "| rename host as device_name"
    ),
    "topology": (
        "index=netops sourcetype=lldp "
        "| table local_host, local_port, remote_host, remote_port"
    ),
    "events": (
        "index=main earliest=-1h "
        "| stats count by source, sourcetype, host"
    ),
    "alerts": (
        "| rest /services/alerts/fired_alerts "
        "| table title, trigger_time, severity, alert_type"
    ),
}



from tools.databridge.registry import register_connector

@register_connector
class SplunkConnector(DataConnector):
    """Splunk REST API connector (DataBridge).

    Uses stdlib urllib exclusively — no splunklib, safe for air-gap.
    """

    @property
    def connector_name(self) -> str:
        return "splunk"

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.SAAS_API

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,
            supports_schema_inference=True,
            max_batch_size=10_000,
            supported_formats=["json"],
        )

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._base_url: str = ""
        self._auth_header: str = ""
        self._connected: bool = False

    def connect(self, config: Dict[str, Any]) -> bool:
        self._config = config
        host = config.get("host", "localhost")
        port = config.get("port", 8089)
        self._base_url = config.get("base_url", f"https://{host}:{port}").rstrip("/")

        splunk_token = config.get("splunk_token", "")
        if splunk_token:
            self._auth_header = f"Bearer {splunk_token}"
        else:
            creds = f"{config.get('username', 'admin')}:{config.get('password', '')}"
            self._auth_header = "Basic " + base64.b64encode(creds.encode()).decode()

        self._connected = True
        health = self.health_check()
        if health.get("status") != "healthy":
            self._connected = False
            return False
        return True

    def disconnect(self) -> None:
        self._config = {}
        self._auth_header = ""
        self._connected = False

    def health_check(self) -> Dict[str, Any]:
        try:
            data = self._get("/services/server/info", output_mode="json")
            entry = data.get("entry", [{}])[0]
            content = entry.get("content", {})
            return {
                "status": "healthy",
                "connector": self.connector_name,
                "base_url": self._base_url,
                "splunk_version": content.get("version", "unknown"),
                "build": content.get("build", "unknown"),
            }
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "connector": self.connector_name}

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name or request.query

        if request.query and not request.query.isidentifier():
            # Treat as raw SPL
            spl = request.query
        elif table in _SPL:
            spl = _SPL[table]
        else:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown table '{table}'. Available: {list(_SPL.keys())}"],
            )

        count = request.limit or 10_000
        try:
            sid = self._submit_search(spl, count)
            self._wait_for_job(sid)
            rows = self._fetch_results(sid, count)
            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"sid": sid, "spl": spl[:100]},
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

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        resp = self.read(ConnectorRequest(table_name=table_name, limit=1))
        if resp.status != "ok" or not resp.data:
            return SchemaDefinition(metadata={"source": self.connector_name, "table": table_name})
        sample = resp.data[0] if isinstance(resp.data, list) else resp.data
        fields = [SchemaField(name=k, data_type="utf8") for k in (sample or {}).keys()]
        return SchemaDefinition(
            fields=fields,
            metadata={"source": self.connector_name, "table": table_name},
        )

    def list_tables(self) -> List[str]:
        return list(_SPL.keys())

    # -- Splunk job lifecycle --------------------------------------------------

    def _submit_search(self, spl: str, count: int) -> str:
        """POST a search job and return the sid."""
        body = urlencode({
            "search": f"search {spl}" if not spl.lstrip().startswith("|") else spl,
            "output_mode": "json",
            "count": str(count),
        }).encode("utf-8")
        data = self._post("/services/search/jobs", body, content_type="application/x-www-form-urlencoded")
        sid = data.get("sid", "")
        if not sid:
            raise RuntimeError(f"Splunk did not return a search job sid: {data}")
        return sid

    def _wait_for_job(self, sid: str) -> None:
        """Poll job status until DONE or timeout."""
        deadline = time.time() + MAX_POLL_WAIT
        while time.time() < deadline:
            status = self._get(f"/services/search/jobs/{sid}", output_mode="json")
            entry = status.get("entry", [{}])[0]
            state = entry.get("content", {}).get("dispatchState", "")
            if state == "DONE":
                return
            if state in ("FAILED", "FINALIZING"):
                msgs = entry.get("content", {}).get("messages", [])
                raise RuntimeError(f"Splunk search {state}: {msgs}")
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"Splunk search job {sid} did not complete within {MAX_POLL_WAIT}s")

    def _fetch_results(self, sid: str, count: int) -> List[Dict[str, Any]]:
        """GET results for a completed search job."""
        data = self._get(
            f"/services/search/jobs/{sid}/results",
            output_mode="json",
            count=str(count),
        )
        results = data.get("results", [])
        # Strip Splunk internal fields
        return [{k: v for k, v in row.items() if not k.startswith("_")} for row in results]

    # -- HTTP helpers ----------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _get(self, path: str, **params: str) -> Dict[str, Any]:
        params.setdefault("output_mode", "json")
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self._base_url}{path}?{qs}"
        req = Request(url, headers=self._headers(), method="GET")
        timeout = self._config.get("timeout", REQUEST_TIMEOUT)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, body: bytes, content_type: str = "application/json") -> Dict[str, Any]:
        headers = dict(self._headers())
        headers["Content-Type"] = content_type
        url = f"{self._base_url}{path}"
        req = Request(url, data=body, headers=headers, method="POST")
        timeout = self._config.get("timeout", REQUEST_TIMEOUT)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            return json.loads(resp.read().decode("utf-8"))
