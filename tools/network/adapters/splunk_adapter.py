# CUI // SP-CTI
"""ICDEV™ Network Canvas — Splunk NMS Adapter.

Thin shim delegating to ``SplunkConnector`` (DataBridge).
All HTTP/SPL logic lives in tools/databridge/connectors/splunk_connector.py.

Splunk is treated as a network data source: device inventory from asset
lookups, interface data from SNMP/MIB indices, and performance stats from
index=netops or equivalent.

Usage::

    from tools.network.nms_adapter import NMSAdapterRegistry
    adapter = NMSAdapterRegistry.get(
        "splunk",
        url="https://splunk-host:8089",
        username="admin",
        password="secret",
    )
    devices = adapter.pull_devices()
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.databridge.connector import ConnectorRequest
from tools.databridge.connectors.splunk_connector import SplunkConnector
from tools.network.nms_adapter import NMSAdapter, NMSAdapterRegistry


class SplunkAdapter(NMSAdapter):
    """Splunk adapter — backed by DataBridge SplunkConnector."""

    def __init__(
        self,
        url: str = "",
        username: str = "admin",
        password: str = "",
        splunk_token: str = "",
        **kwargs: Any,
    ) -> None:
        self._connector = SplunkConnector()
        self._connector.connect({
            "base_url": url,
            "username": username,
            "password": password,
            "splunk_token": splunk_token,
        })

    # -- NMS interface ---------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        return self._connector.health_check()

    def pull_devices(self, site: Optional[str] = None) -> List[Dict[str, Any]]:
        spl = (
            "| inputlookup asset_lookup "
            + (f"| where location=\"{site}\" " if site else "")
            + "| table hostname ip os vendor category location"
        )
        resp = self._connector.read(ConnectorRequest(query=spl))
        if resp.status != "ok":
            raise RuntimeError(f"Splunk pull_devices failed: {resp.errors}")
        return [_normalize_device(r) for r in (resp.data or [])]

    def pull_configs(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        host_filter = ""
        if device_filter and device_filter.get("hostname"):
            host_filter = f"| where host=\"{device_filter['hostname']}\" "
        spl = (
            "index=configs sourcetype=device_config "
            + host_filter
            + "| stats latest(_raw) as config_text by host "
            + "| rename host as hostname"
        )
        resp = self._connector.read(ConnectorRequest(query=spl))
        if resp.status != "ok":
            raise RuntimeError(f"Splunk pull_configs failed: {resp.errors}")
        return [_normalize_config(r) for r in (resp.data or [])]

    def pull_interfaces(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(table_name="interfaces"))
        if resp.status != "ok":
            raise RuntimeError(f"Splunk pull_interfaces failed: {resp.errors}")
        return [_normalize_interface(r) for r in (resp.data or [])]

    def pull_stats(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        resp = self._connector.read(ConnectorRequest(table_name="stats"))
        if resp.status != "ok":
            raise RuntimeError(f"Splunk pull_stats failed: {resp.errors}")
        return [_normalize_stat(r) for r in (resp.data or [])]

    def pull_topology(self) -> Dict[str, Any]:
        resp = self._connector.read(ConnectorRequest(table_name="topology"))
        if resp.status != "ok":
            raise RuntimeError(f"Splunk pull_topology failed: {resp.errors}")
        rows = resp.data or []
        nodes: List[Dict] = []
        edges: List[Dict] = []
        seen: set = set()
        for link in rows:
            for host_key in ("local_host", "remote_host"):
                h = link.get(host_key, "")
                if h and h not in seen:
                    nodes.append({"id": h, "label": h})
                    seen.add(h)
            src = link.get("local_host", "")
            tgt = link.get("remote_host", "")
            if src and tgt:
                edges.append({
                    "source": src,
                    "target": tgt,
                    "local_port": link.get("local_port", ""),
                    "remote_port": link.get("remote_port", ""),
                })
        return {"nodes": nodes, "edges": edges}


# -- Normalization helpers ---------------------------------------------------

def _normalize_device(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": row.get("hostname", ""),
        "device_type": row.get("category", ""),
        "vendor": row.get("vendor", ""),
        "model": "",
        "serial": "",
        "firmware_version": "",
        "site": row.get("location", ""),
        "rack": "",
        "ip_address": row.get("ip", ""),
        "status": "active",
        "role": row.get("category", ""),
        "_raw": row,
    }


def _normalize_config(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "hostname": row.get("hostname", ""),
        "device_ip": "",
        "config_type": "running",
        "config_text": row.get("config_text", ""),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "_raw": row,
    }


def _normalize_interface(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "device_name": row.get("host", ""),
        "name": row.get("interface_name", ""),
        "type": "",
        "speed": 0,
        "mtu": 0,
        "enabled": True,
        "ip_address": "",
        "mac_address": "",
        "description": "",
        "vlan_id": None,
        "_raw": row,
    }


def _normalize_stat(row: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "device_name": row.get("device_name", row.get("host", "")),
        "metric_name": "cpu_pct",
        "metric_value": row.get("avg_cpu", row.get("cpu_pct")),
        "unit": "%",
        "timestamp": now,
        "_raw": row,
    }


NMSAdapterRegistry.register("splunk", SplunkAdapter)
