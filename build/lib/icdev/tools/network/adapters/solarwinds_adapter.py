# CUI // SP-CTI
"""ICDEV™ Network Canvas — SolarWinds Orion NMS Adapter.

Thin shim delegating to ``SolarWindsConnector`` (DataBridge SaaSBaseConnector).
All HTTP/SWQL logic lives in tools/databridge/connectors/solarwinds_connector.py.

Usage::

    from tools.network.nms_adapter import NMSAdapterRegistry
    adapter = NMSAdapterRegistry.get(
        "solarwinds",
        url="https://orion-host:17778",
        username="admin",
        password="secret",
    )
    devices = adapter.pull_devices()
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.databridge.connector import ConnectorRequest
from tools.databridge.connectors.solarwinds_connector import SolarWindsConnector
from tools.network.nms_adapter import NMSAdapter, NMSAdapterRegistry


class SolarWindsAdapter(NMSAdapter):
    """SolarWinds Orion adapter — backed by DataBridge SolarWindsConnector."""

    def __init__(
        self,
        url: str = "",
        username: str = "",
        password: str = "",
        verify_ssl: bool = True,
        **kwargs: Any,
    ) -> None:
        self._connector = SolarWindsConnector()
        self._connector.connect({
            "base_url": url,
            "username": username,
            "password": password,
            "verify_ssl": verify_ssl,
        })

    # -- NMS interface ---------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        return self._connector.health_check()

    def pull_devices(self, site: Optional[str] = None) -> List[Dict[str, Any]]:
        filters = {"Location": site} if site else {}
        resp = self._connector.read(ConnectorRequest(table_name="devices", filters=filters))
        if resp.status != "ok":
            raise RuntimeError(f"SolarWinds pull_devices failed: {resp.errors}")
        return [_normalize_device(row) for row in (resp.data or [])]

    def pull_configs(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(table_name="configs", filters=device_filter or {}))
        if resp.status != "ok":
            raise RuntimeError(f"SolarWinds pull_configs failed: {resp.errors}")
        return [_normalize_config(row) for row in (resp.data or [])]

    def pull_interfaces(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(table_name="interfaces", filters=device_filter or {}))
        if resp.status != "ok":
            raise RuntimeError(f"SolarWinds pull_interfaces failed: {resp.errors}")
        return [_normalize_interface(row) for row in (resp.data or [])]

    def pull_stats(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(table_name="stats", filters=device_filter or {}))
        if resp.status != "ok":
            raise RuntimeError(f"SolarWinds pull_stats failed: {resp.errors}")
        return [_normalize_stat(row) for row in (resp.data or [])]

    def pull_topology(self) -> Dict[str, Any]:
        resp = self._connector.read(ConnectorRequest(table_name="topology"))
        if resp.status != "ok":
            raise RuntimeError(f"SolarWinds pull_topology failed: {resp.errors}")
        rows = resp.data or []
        nodes = [{"id": str(r.get("NodeID", "")), "label": r.get("Caption", "")} for r in rows]
        edges = [
            {"source": str(r.get("NodeID", "")), "target": str(r.get("Layer2NeighborID", ""))}
            for r in rows if r.get("Layer2NeighborID")
        ]
        return {"nodes": nodes, "edges": edges}


# -- Normalization helpers ---------------------------------------------------

def _normalize_device(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": row.get("Caption", row.get("SysName", "")),
        "device_type": row.get("MachineType", ""),
        "vendor": row.get("Vendor", row.get("HardwareManufacturer", "")),
        "model": row.get("MachineType", ""),
        "serial": "",
        "firmware_version": "",
        "site": row.get("Location", ""),
        "rack": "",
        "ip_address": row.get("IPAddress", ""),
        "status": "active" if row.get("Status") == 1 else "inactive",
        "role": "",
        "_raw": row,
    }


def _normalize_config(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "hostname": row.get("Caption", ""),
        "device_ip": "",
        "config_type": row.get("ConfigType", "running"),
        "config_text": row.get("Config", ""),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "_raw": row,
    }


def _normalize_interface(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "device_name": str(row.get("NodeID", "")),
        "name": row.get("Caption", ""),
        "type": "",
        "speed": row.get("Speed", row.get("IfSpeed", 0)),
        "mtu": 0,
        "enabled": row.get("AdminStatus", 1) == 1,
        "ip_address": row.get("IPAddress", ""),
        "mac_address": "",
        "description": "",
        "vlan_id": None,
        "_raw": row,
    }


def _normalize_stat(row: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    stats = []
    for metric_name, metric_key, unit in [
        ("response_time_ms", "AvgResponseTime", "ms"),
        ("cpu_load_pct", "CPULoad", "%"),
        ("mem_used_pct", "PercentMemoryUsed", "%"),
        ("packet_loss_pct", "PercentLoss", "%"),
    ]:
        if metric_key in row:
            stats.append({
                "device_name": row.get("Caption", str(row.get("NodeID", ""))),
                "metric_name": metric_name,
                "metric_value": row.get(metric_key),
                "unit": unit,
                "timestamp": row.get("DateTime", now),
            })
    return stats[0] if len(stats) == 1 else (stats or {"device_name": "", "metric_name": "", "metric_value": None, "unit": "", "timestamp": now})


NMSAdapterRegistry.register("solarwinds", SolarWindsAdapter)
