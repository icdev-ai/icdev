# CUI // SP-CTI
"""ICDEV™ Network Canvas — LibreNMS NMS Adapter (stub).

Reference implementation showing how to build a custom NMS adapter.
Replace ``NotImplementedError`` calls with your LibreNMS API logic.

LibreNMS API docs: https://docs.librenms.org/API/

Usage::

    from tools.network.nms_adapter import NMSAdapterRegistry
    adapter = NMSAdapterRegistry.get("librenms", url="http://librenms", token="abc")
    devices = adapter.pull_devices()

Implementation guide:
    1. Copy this file as a starting point
    2. Replace NotImplementedError with API calls using urllib.request
    3. Normalize response data to match the return type docstrings
    4. Register your adapter: NMSAdapterRegistry.register("my_nms", MyAdapter)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools.network.nms_adapter import NMSAdapter, NMSAdapterRegistry


class LibreNMSAdapter(NMSAdapter):
    """LibreNMS adapter — stub for reference.

    LibreNMS REST API endpoints used:
        GET /api/v0/devices          -> pull_devices
        GET /api/v0/devices/{id}/..  -> pull_configs, pull_interfaces
        GET /api/v0/ports            -> pull_interfaces
        GET /api/v0/bills            -> pull_stats (bandwidth billing)

    Auth: X-Auth-Token header with API token.
    """

    def __init__(
        self,
        url: str = "",
        token: str = "",
        **kwargs: Any,
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token

    def test_connection(self) -> Dict[str, Any]:
        # GET /api/v0/system
        raise NotImplementedError(
            "LibreNMS adapter: implement test_connection() — "
            "GET /api/v0/system with X-Auth-Token header"
        )

    def pull_devices(self, site: Optional[str] = None) -> List[Dict[str, Any]]:
        # GET /api/v0/devices
        # Normalize: hostname, sysName, hardware, os, version, location
        raise NotImplementedError(
            "LibreNMS adapter: implement pull_devices() — "
            "GET /api/v0/devices, normalize to {name, device_type, vendor, ...}"
        )

    def pull_configs(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        # GET /api/v0/oxidized/config/{hostname}
        # Requires Oxidized integration enabled in LibreNMS
        raise NotImplementedError(
            "LibreNMS adapter: implement pull_configs() — "
            "GET /api/v0/oxidized/config/{hostname}"
        )

    def pull_interfaces(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        # GET /api/v0/ports
        raise NotImplementedError(
            "LibreNMS adapter: implement pull_interfaces() — "
            "GET /api/v0/ports"
        )

    def pull_stats(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        # GET /api/v0/devices/{id}/health
        raise NotImplementedError(
            "LibreNMS adapter: implement pull_stats() — "
            "GET /api/v0/devices/{id}/health"
        )

    def pull_topology(self) -> Dict[str, Any]:
        # LibreNMS discovers links via CDP/LLDP: GET /api/v0/devices/{id}/links
        raise NotImplementedError(
            "LibreNMS adapter: implement pull_topology() — "
            "GET /api/v0/devices/{id}/links for CDP/LLDP neighbor data"
        )


NMSAdapterRegistry.register("librenms", LibreNMSAdapter)
