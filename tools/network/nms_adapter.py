# CUI // SP-CTI
"""ICDEV™ Network Canvas — NMS Adapter ABC and Registry.

Defines a generic interface for network management system integrations.
Any NMS (NetBox, LibreNMS, SolarWinds, etc.) implements this ABC to
enable device, config, and topology synchronization with the NDC.

Usage::

    from tools.network.nms_adapter import NMSAdapterRegistry

    adapter = NMSAdapterRegistry.get("netbox", url="...", token="...")
    devices = adapter.pull_devices()
    configs = adapter.pull_configs()
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.network.nms_adapter")


class NMSAdapter(ABC):
    """Abstract base class for network management system adapters.

    Implementations must provide all abstract methods. Each method returns
    normalized data dictionaries that can be inserted into NDC tables.
    """

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """Test connectivity to the NMS.

        Returns:
            {"ok": bool, "version": str, "message": str}
        """

    @abstractmethod
    def pull_devices(self, site: Optional[str] = None) -> List[Dict[str, Any]]:
        """Pull device inventory.

        Returns list of dicts with keys:
            name, device_type, vendor, model, serial, firmware_version,
            site, rack, ip_address, status, role
        """

    @abstractmethod
    def pull_configs(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Pull device running/startup configurations.

        Returns list of dicts with keys:
            hostname, device_ip, config_type ('running'|'startup'),
            config_text, collected_at
        """

    @abstractmethod
    def pull_interfaces(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Pull interface data.

        Returns list of dicts with keys:
            device_name, name, type, speed, mtu, enabled, ip_address,
            mac_address, description, vlan_id
        """

    @abstractmethod
    def pull_stats(
        self, device_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Pull performance/health statistics.

        Returns list of dicts with keys:
            device_name, metric_name, metric_value, unit, timestamp
        """

    @abstractmethod
    def pull_topology(self) -> Dict[str, Any]:
        """Pull topology/connectivity data.

        Returns:
            {"nodes": [...], "edges": [...]}
        """

    def pull_all(self, site: Optional[str] = None) -> Dict[str, Any]:
        """Pull all data types. Default implementation calls each method."""
        results = {}  # type: Dict[str, Any]
        for method_name in ("devices", "configs", "interfaces", "stats", "topology"):
            try:
                method = getattr(self, "pull_%s" % method_name)
                if method_name in ("devices",):
                    results[method_name] = method(site=site)
                elif method_name == "topology":
                    results[method_name] = method()
                else:
                    results[method_name] = method()
            except NotImplementedError:
                results[method_name] = []
            except Exception as exc:
                logger.warning("pull_%s failed: %s", method_name, exc)
                results[method_name] = {"error": str(exc)}
        return results


class NMSAdapterRegistry:
    """Registry for NMS adapter implementations."""

    _adapters = {}  # type: Dict[str, type]

    @classmethod
    def register(cls, name: str, adapter_cls: type) -> None:
        """Register an adapter class by name."""
        cls._adapters[name.lower()] = adapter_cls
        logger.debug("Registered NMS adapter: %s", name)

    @classmethod
    def get(cls, name: str, **kwargs: Any) -> NMSAdapter:
        """Instantiate a registered adapter by name.

        Args:
            name: Adapter name (e.g. 'netbox', 'librenms').
            **kwargs: Passed to adapter constructor.

        Returns:
            NMSAdapter instance.

        Raises:
            KeyError: If adapter name is not registered.
        """
        key = name.lower()
        if key not in cls._adapters:
            available = ", ".join(sorted(cls._adapters.keys())) or "(none)"
            raise KeyError(
                "Unknown NMS adapter '%s'. Available: %s" % (name, available)
            )
        return cls._adapters[key](**kwargs)

    @classmethod
    def list_adapters(cls) -> List[str]:
        """Return sorted list of registered adapter names."""
        return sorted(cls._adapters.keys())
