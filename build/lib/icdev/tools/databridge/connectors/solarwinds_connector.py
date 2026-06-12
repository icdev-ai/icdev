# CUI // SP-CTI
"""DataBridge connector for SolarWinds Orion Platform.

Uses the SolarWinds Information Service (SWIS) REST API with SWQL.
All queries go via POST to /Query — SolarWinds does not expose plain GET
list endpoints like a conventional REST API.

Auth: HTTP Basic (username + password).
Default port: 17778 (HTTPS).

Endpoint table names map to SWQL SELECT statements executed against the
Orion schema.  Callers can override by setting ``ConnectorRequest.query``
to a raw SWQL string.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import base64
import json
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
    SchemaDefinition,
    SchemaField,
)

logger = get_logger("databridge.solarwinds")

REQUEST_TIMEOUT = 30
USER_AGENT = "ICDEV-DataBridge/1.0"

# Default SWQL queries per logical table
_SWQL: Dict[str, str] = {
    "devices": (
        "SELECT NodeID, Caption, Vendor, MachineType, IPAddress, "
        "SysName, HardwareManufacturer, Location, Status "
        "FROM Orion.Nodes"
    ),
    "configs": (
        "SELECT NodeID, Caption, ConfigType, Config "
        "FROM NCM.ConfigArchive"
    ),
    "interfaces": (
        "SELECT InterfaceID, NodeID, Caption, Speed, "
        "IfSpeed, Duplex, AdminStatus, OperStatus, IPAddress "
        "FROM Orion.NPM.Interfaces"
    ),
    "stats": (
        "SELECT NodeID, Caption, AvgResponseTime, AvgLoad, "
        "PercentLoss, CPULoad, PercentMemoryUsed, DateTime "
        "FROM Orion.CPULoad"
    ),
    "topology": (
        "SELECT NodeID, Caption, Layer2NeighborID "
        "FROM Orion.NPM.Topology"
    ),
}



from tools.databridge.registry import register_connector

@register_connector
class SolarWindsConnector(DataConnector):
    """SolarWinds Orion SWIS connector (DataBridge)."""

    @property
    def connector_name(self) -> str:
        return "solarwinds_orion"

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.SAAS_API

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,
            supports_schema_inference=True,
            max_batch_size=500,
            supported_formats=["json"],
        )

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._base_url: str = ""
        self._auth_header: str = ""
        self._connected: bool = False

    def connect(self, config: Dict[str, Any]) -> bool:
        self._config = config
        host = config.get("host", "")
        port = config.get("port", 17778)
        self._base_url = config.get(
            "base_url",
            f"https://{host}:{port}/SolarWinds/InformationService/v3/Json",
        ).rstrip("/")
        creds = f"{config.get('username', '')}:{config.get('password', '')}"
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
            rows = self._swql("SELECT TOP 1 NodeID FROM Orion.Nodes")
            return {
                "status": "healthy",
                "connector": self.connector_name,
                "base_url": self._base_url,
                "node_count_sample": len(rows),
            }
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "connector": self.connector_name}

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name or request.query
        # Allow raw SWQL via request.query
        if request.query and request.query.upper().startswith("SELECT"):
            swql = request.query
        elif table in _SWQL:
            swql = _SWQL[table]
            if request.limit:
                # Inject TOP N
                swql = swql.replace("SELECT ", f"SELECT TOP {request.limit} ", 1)
        else:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown table '{table}'. Available: {list(_SWQL.keys())}"],
            )
        try:
            rows = self._swql(swql)
            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"swql": swql[:100]},
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
                fields.append(SchemaField(name=key, data_type=dt))
        return SchemaDefinition(
            fields=fields,
            metadata={"source": self.connector_name, "table": table_name},
        )

    def list_tables(self) -> List[str]:
        return list(_SWQL.keys())

    # -- Internal SWQL execution -----------------------------------------------

    def _swql(self, query: str) -> List[Dict[str, Any]]:
        """POST a SWQL query and return the results list."""
        url = f"{self._base_url}/Query"
        payload = json.dumps({"query": query}).encode("utf-8")
        headers = {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        req = Request(url, data=payload, headers=headers, method="POST")
        timeout = self._config.get("timeout", REQUEST_TIMEOUT)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            body = resp.read().decode("utf-8")
            data = json.loads(body) if body.strip() else {}
        # SWIS wraps rows in {"results": [...]}
        return data.get("results", data) if isinstance(data, dict) else data
