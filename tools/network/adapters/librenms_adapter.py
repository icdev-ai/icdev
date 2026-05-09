# CUI // SP-CTI
"""ICDEV™ Network Canvas — LibreNMS NMS Adapter.

Thin shim delegating to ``LibreNMSConnector`` (DataBridge SaaSBaseConnector).
All HTTP logic lives in tools/databridge/connectors/librenms_connector.py.

Usage::

    from tools.network.nms_adapter import NMSAdapterRegistry
    adapter = NMSAdapterRegistry.get("librenms", url="http://librenms", token="your-api-token")
    devices = adapter.pull_devices()
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.databridge.connector import ConnectorRequest
from tools.databridge.connectors.librenms_connector import LibreNMSConnector
from tools.network.nms_adapter import NMSAdapter, NMSAdapterRegistry


class LibreNMSAdapter(NMSAdapter):
    """LibreNMS adapter — backed by DataBridge LibreNMSConnector."""

    def __init__(
        self,
        url: str = "",
        token: str = "",
        **kwargs: Any,
    ) -> None:
        self._connector = LibreNMSConnector()
        self._connector.connect({"base_url": url, "token": token})

    # -- NMS interface ---------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        return self._connector.health_check()

    def pull_devices(self, site: Optional[str] = None) -> List[Dict[str, Any]]:
        filters = {"location": site} if site else {}
        resp = self._connector.read(ConnectorRequest(table_name="devices", filters=filters))
        if resp.status != "ok":
            raise RuntimeError(f"LibreNMS pull_devices failed: {resp.errors}")
        return [_normalize_device(d) for d in (resp.data or [])]

    def pull_configs(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(
            table_name="configs",
            filters=device_filter or {},
        ))
        if resp.status != "ok":
            raise RuntimeError(f"LibreNMS pull_configs failed: {resp.errors}")
        return [_normalize_config(r) for r in (resp.data or [])]

    def pull_interfaces(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(
            table_name="interfaces",
            filters=device_filter or {},
        ))
        if resp.status != "ok":
            raise RuntimeError(f"LibreNMS pull_interfaces failed: {resp.errors}")
        return [_normalize_interface(r) for r in (resp.data or [])]

    def pull_stats(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(
            table_name="stats",
            filters=device_filter or {},
        ))
        if resp.status != "ok":
            raise RuntimeError(f"LibreNMS pull_stats failed: {resp.errors}")
        return [_normalize_stat(r) for r in (resp.data or [])]

    def pull_topology(self) -> Dict[str, Any]:
        resp = self._connector.read(ConnectorRequest(table_name="topology"))
        if resp.status != "ok":
            raise RuntimeError(f"LibreNMS pull_topology failed: {resp.errors}")
        rows = resp.data or []
        nodes: List[Dict] = []
        edges: List[Dict] = []
        seen: set = set()
        for link in rows:
            for side in ("local", "remote"):
                nid = str(link.get(f"{side}_device_id", link.get(f"{side}_hostname", "")))
                if nid and nid not in seen:
                    nodes.append({"id": nid, "label": link.get(f"{side}_hostname", nid)})
                    seen.add(nid)
            src = str(link.get("local_device_id", link.get("local_hostname", "")))
            tgt = str(link.get("remote_device_id", link.get("remote_hostname", "")))
            if src and tgt:
                edges.append({"source": src, "target": tgt, "local_port": link.get("local_ifname", ""), "remote_port": link.get("remote_ifname", "")})
        return {"nodes": nodes, "edges": edges}


# -- Normalization helpers ---------------------------------------------------

def _normalize_device(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": row.get("hostname", row.get("sysName", "")),
        "device_type": row.get("hardware", ""),
        "vendor": row.get("vendor", ""),
        "model": row.get("hardware", ""),
        "serial": "",
        "firmware_version": row.get("version", ""),
        "site": row.get("location", ""),
        "rack": "",
        "ip_address": row.get("ip", ""),
        "status": row.get("status", "unknown"),
        "role": row.get("type", ""),
        "_raw": row,
    }


def _normalize_config(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "hostname": row.get("hostname", row.get("name", "")),
        "device_ip": "",
        "config_type": "running",
        "config_text": row.get("config", str(row)),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "_raw": row,
    }


def _normalize_interface(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "device_name": str(row.get("device_id", "")),
        "name": row.get("ifName", row.get("ifDescr", "")),
        "type": row.get("ifType", ""),
        "speed": row.get("ifSpeed", 0),
        "mtu": row.get("ifMtu", 0),
        "enabled": row.get("ifAdminStatus") == "up",
        "ip_address": row.get("ipv4_addresses", [{}])[0].get("ipv4_address", "") if row.get("ipv4_addresses") else "",
        "mac_address": row.get("ifPhysAddress", ""),
        "description": row.get("ifAlias", ""),
        "vlan_id": None,
        "_raw": row,
    }


def _normalize_stat(row: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "device_name": str(row.get("device_id", "")),
        "metric_name": row.get("metric_type", "bandwidth_in"),
        "metric_value": row.get("rate_in", row.get("average", None)),
        "unit": "bps",
        "timestamp": now,
        "_raw": row,
    }


NMSAdapterRegistry.register("librenms", LibreNMSAdapter)
