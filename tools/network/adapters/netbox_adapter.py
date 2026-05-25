# CUI // SP-CTI
"""ICDEV™ Network Canvas — NetBox NMS Adapter.

Wraps the existing ``NetBoxClient`` to implement the ``NMSAdapter`` ABC.
No changes to ``netbox_client.py`` are required.

Usage::

    from tools.network.nms_adapter import NMSAdapterRegistry
    adapter = NMSAdapterRegistry.get("netbox", url="http://netbox:8000", token="abc")
    devices = adapter.pull_devices()
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from typing import Any, Dict, List, Optional

from tools.network.netbox_client import NetBoxClient
from tools.network.nms_adapter import NMSAdapter, NMSAdapterRegistry

logger = get_logger("icdev.network.adapters.netbox")


class NetBoxAdapter(NMSAdapter):
    """NetBox NMS adapter — delegates to NetBoxClient."""

    def __init__(
        self,
        url: str = "",
        token: str = "",
        timeout: int = 15,
        verify_ssl: bool = True,
        **kwargs: Any,
    ) -> None:
        self._client = NetBoxClient(
            url=url, token=token, timeout=timeout, verify_ssl=verify_ssl
        )

    def test_connection(self) -> Dict[str, Any]:
        return self._client.test_connection()

    def pull_devices(self, site: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._client.get_devices(site=site)

    def pull_configs(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        # NetBox does not store device configurations natively.
        # Config management requires NetBox plugins (e.g. netbox-config-backup).
        logger.info("NetBox does not store configs natively. Returning empty list.")
        return []

    def pull_interfaces(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        # NetBox stores interfaces via /dcim/interfaces/ — not yet in NetBoxClient.
        # Placeholder: return empty. Extend NetBoxClient.get_interfaces() to enable.
        return []

    def pull_stats(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        # NetBox is a source of truth, not a monitoring tool. No stats available.
        return []

    def pull_topology(self) -> Dict[str, Any]:
        result = self._client.pull_all()
        # Convert NetBox pull_all format to standard {nodes, edges}
        nodes = []
        for dev in result.get("devices", []):
            nodes.append({
                "id": dev.get("id", ""),
                "label": dev.get("name", ""),
                "type": dev.get("device_type", "unknown"),
                "vendor": dev.get("vendor", ""),
                "model": dev.get("model", ""),
                "site": dev.get("site", ""),
            })
        return {"nodes": nodes, "edges": []}


# Auto-register
NMSAdapterRegistry.register("netbox", NetBoxAdapter)
