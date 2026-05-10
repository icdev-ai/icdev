# CUI // SP-CTI
"""ICDEV™ Network Canvas — SolarWinds Orion NMS Adapter (stub).

Reference implementation for SolarWinds Orion Platform integration.
SolarWinds uses the SWIS (SolarWinds Information Service) REST API.

SolarWinds SWIS API docs: https://github.com/solarwinds/OrionSDK

Usage::

    from tools.network.nms_adapter import NMSAdapterRegistry
    adapter = NMSAdapterRegistry.get("solarwinds", url="https://orion", username="admin", password="...")
    devices = adapter.pull_devices()

Implementation guide:
    1. SolarWinds uses SWQL (SolarWinds Query Language) via REST
    2. Auth: Basic auth or certificate-based
    3. Endpoint: POST https://{host}:17778/SolarWinds/InformationService/v3/Json/Query
    4. Body: {"query": "SELECT ... FROM Orion.Nodes"}
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools.network.nms_adapter import NMSAdapter, NMSAdapterRegistry


class SolarWindsAdapter(NMSAdapter):
    """SolarWinds Orion adapter — stub for reference.

    SWQL queries for each pull method:
        pull_devices:    SELECT NodeID, Caption, Vendor, MachineType, IPAddress FROM Orion.Nodes
        pull_configs:    SELECT NodeID, Config FROM NCM.ConfigArchive
        pull_interfaces: SELECT InterfaceID, Caption, Speed, Status FROM Orion.NPM.Interfaces
        pull_stats:      SELECT NodeID, AvgResponseTime, AvgLoad FROM Orion.Nodes
    """

    def __init__(
        self,
        url: str = "",
        username: str = "",
        password: str = "",
        verify_ssl: bool = True,
        **kwargs: Any,
    ) -> None:
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl

    def test_connection(self) -> Dict[str, Any]:
        # POST /SolarWinds/InformationService/v3/Json/Query
        # Body: {"query": "SELECT TOP 1 NodeID FROM Orion.Nodes"}
        raise NotImplementedError(
            "SolarWinds adapter: implement test_connection() — "
            "POST SWQL query 'SELECT TOP 1 NodeID FROM Orion.Nodes'"
        )

    def pull_devices(self, site: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "SolarWinds adapter: implement pull_devices() — "
            "SWQL: SELECT NodeID, Caption, Vendor, MachineType, IPAddress "
            "FROM Orion.Nodes"
        )

    def pull_configs(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        # Requires SolarWinds NCM (Network Configuration Manager)
        raise NotImplementedError(
            "SolarWinds adapter: implement pull_configs() — "
            "SWQL: SELECT NodeID, Config FROM NCM.ConfigArchive "
            "(requires NCM license)"
        )

    def pull_interfaces(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "SolarWinds adapter: implement pull_interfaces() — "
            "SWQL: SELECT InterfaceID, Caption, Speed, Status "
            "FROM Orion.NPM.Interfaces"
        )

    def pull_stats(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "SolarWinds adapter: implement pull_stats() — "
            "SWQL: SELECT NodeID, AvgResponseTime, PercentLoss, AvgLoad "
            "FROM Orion.Nodes"
        )

    def pull_topology(self) -> Dict[str, Any]:
        # SolarWinds Network Topology Poller discovers L2/L3 links
        raise NotImplementedError(
            "SolarWinds adapter: implement pull_topology() — "
            "SWQL: SELECT ... FROM Orion.NodeConnections"
        )


NMSAdapterRegistry.register("solarwinds", SolarWindsAdapter)
