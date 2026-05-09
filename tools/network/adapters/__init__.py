# CUI // SP-CTI
"""ICDEV™ Network Canvas — NMS Adapter Implementations.

Auto-registers all adapters on import so they are available via
``NMSAdapterRegistry.get(name)``.
"""

from __future__ import annotations

from tools.network.adapters.netbox_adapter import NetBoxAdapter  # noqa: F401
from tools.network.adapters.librenms_adapter import LibreNMSAdapter  # noqa: F401
from tools.network.adapters.solarwinds_adapter import SolarWindsAdapter  # noqa: F401
from tools.network.adapters.riverbed_netim_adapter import RiverbedNetIMAdapter  # noqa: F401
from tools.network.adapters.splunk_adapter import SplunkAdapter  # noqa: F401
