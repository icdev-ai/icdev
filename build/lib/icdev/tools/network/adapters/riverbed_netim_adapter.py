# CUI // SP-CTI
"""ICDEV™ Network Canvas — Riverbed NetIM NMS Adapter.

Thin shim delegating to ``RiverbedNetIMConnector`` (DataBridge SaaSBaseConnector).
All HTTP logic lives in tools/databridge/connectors/riverbed_netim_connector.py.

Usage::

    from tools.network.nms_adapter import NMSAdapterRegistry
    adapter = NMSAdapterRegistry.get(
        "riverbed_netim",
        url="https://netim-host",
        api_token="your-token",
    )
    devices = adapter.pull_devices()
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.databridge.connector import ConnectorRequest
from tools.databridge.connectors.riverbed_netim_connector import RiverbedNetIMConnector
from tools.network.nms_adapter import NMSAdapter, NMSAdapterRegistry


class RiverbedNetIMAdapter(NMSAdapter):
    """Riverbed NetIM adapter — backed by DataBridge RiverbedNetIMConnector."""

    def __init__(
        self,
        url: str = "",
        api_token: str = "",
        username: str = "",
        password: str = "",
        **kwargs: Any,
    ) -> None:
        self._connector = RiverbedNetIMConnector()
        self._connector.connect({
            "base_url": url,
            "api_token": api_token,
            "username": username,
            "password": password,
        })

    # -- NMS interface ---------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        return self._connector.health_check()

    def pull_devices(self, site: Optional[str] = None) -> List[Dict[str, Any]]:
        filters = {"site": site} if site else {}
        resp = self._connector.read(ConnectorRequest(table_name="devices", filters=filters))
        if resp.status != "ok":
            raise RuntimeError(f"Riverbed NetIM pull_devices failed: {resp.errors}")
        return [_normalize_device(d) for d in (resp.data or [])]

    def pull_configs(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(
            table_name="configs",
            filters=device_filter or {},
        ))
        if resp.status != "ok":
            raise RuntimeError(f"Riverbed NetIM pull_configs failed: {resp.errors}")
        return [_normalize_config(r) for r in (resp.data or [])]

    def pull_interfaces(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(
            table_name="interfaces",
            filters=device_filter or {},
        ))
        if resp.status != "ok":
            raise RuntimeError(f"Riverbed NetIM pull_interfaces failed: {resp.errors}")
        return [_normalize_interface(r) for r in (resp.data or [])]

    def pull_stats(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(
            table_name="stats",
            filters=device_filter or {},
        ))
        if resp.status != "ok":
            raise RuntimeError(f"Riverbed NetIM pull_stats failed: {resp.errors}")
        return [_normalize_stat(r) for r in (resp.data or [])]

    def pull_topology(self) -> Dict[str, Any]:
        resp = self._connector.read(ConnectorRequest(table_name="topology"))
        if resp.status != "ok":
            raise RuntimeError(f"Riverbed NetIM pull_topology failed: {resp.errors}")
        rows = resp.data or []
        nodes: List[Dict] = []
        edges: List[Dict] = []
        seen: set = set()
        for link in rows:
            for side, id_key, label_key in [
                ("src", "sourceDeviceId", "sourceDevice"),
                ("dst", "destDeviceId", "destDevice"),
            ]:
                nid = str(link.get(id_key, link.get(label_key, "")))
                if nid and nid not in seen:
                    nodes.append({"id": nid, "label": link.get(label_key, nid)})
                    seen.add(nid)
            src = str(link.get("sourceDeviceId", link.get("sourceDevice", "")))
            tgt = str(link.get("destDeviceId", link.get("destDevice", "")))
            if src and tgt:
                edges.append({"source": src, "target": tgt})
        return {"nodes": nodes, "edges": edges}


# -- Normalization helpers ---------------------------------------------------

def _normalize_device(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": row.get("name", row.get("displayName", "")),
        "device_type": row.get("deviceType", ""),
        "vendor": row.get("manufacturer", ""),
        "model": row.get("model", ""),
        "serial": row.get("serialNumber", ""),
        "firmware_version": row.get("softwareVersion", ""),
        "site": row.get("site", row.get("location", "")),
        "rack": "",
        "ip_address": row.get("ipAddress", row.get("managementIp", "")),
        "status": "active" if row.get("status", "").lower() in ("up", "active", "ok") else "inactive",
        "role": row.get("role", ""),
        "_raw": row,
    }


def _normalize_config(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "hostname": row.get("deviceName", row.get("name", "")),
        "device_ip": row.get("ipAddress", ""),
        "config_type": row.get("configType", "running"),
        "config_text": row.get("configText", row.get("content", str(row))),
        "collected_at": row.get("collectedAt", datetime.now(timezone.utc).isoformat()),
        "_raw": row,
    }


def _normalize_interface(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "device_name": row.get("deviceName", str(row.get("deviceId", ""))),
        "name": row.get("interfaceName", row.get("name", "")),
        "type": row.get("interfaceType", ""),
        "speed": row.get("speed", 0),
        "mtu": row.get("mtu", 0),
        "enabled": row.get("adminStatus", "up").lower() == "up",
        "ip_address": row.get("ipAddress", ""),
        "mac_address": row.get("macAddress", ""),
        "description": row.get("description", ""),
        "vlan_id": row.get("vlanId"),
        "_raw": row,
    }


def _normalize_stat(row: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "device_name": row.get("deviceName", str(row.get("deviceId", ""))),
        "metric_name": row.get("metricName", row.get("name", "response_time")),
        "metric_value": row.get("value", row.get("currentValue")),
        "unit": row.get("unit", "ms"),
        "timestamp": row.get("timestamp", now),
        "_raw": row,
    }


NMSAdapterRegistry.register("riverbed_netim", RiverbedNetIMAdapter)
