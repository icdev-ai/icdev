# CUI // SP-CTI
"""ICDEV™ Asset Discovery adapters — five sources behind one contract.

    from tools.assets.discovery_adapters import AdapterRegistry, run

    print(AdapterRegistry.names())      # ['csv', 'gns3', 'netbox', 'snmp', 'ssh']
    report = run(write=False)           # health per fabric; writes nothing

Importing this package registers every built-in adapter. See ``base.py`` for
the contract and why it is two methods, and ``runner.py`` for why the report is
per fabric and carries no percentages.
"""

from __future__ import annotations

from tools.assets.discovery_adapters.base import (  # noqa: F401
    DISCOVERING_STATES,
    HEALTH_STATES,
    NOT_MEASURED_STATES,
    AdapterHealth,
    AdapterRegistry,
    DiscoveredDevice,
    DiscoveryAdapter,
    DiscoveryResult,
)
from tools.assets.discovery_adapters.csv_adapter import CSVDiscoveryAdapter
from tools.assets.discovery_adapters.gns3_adapter import GNS3DiscoveryAdapter
from tools.assets.discovery_adapters.netbox_adapter import NetBoxDiscoveryAdapter
from tools.assets.discovery_adapters.snmp_adapter import SNMPDiscoveryAdapter
from tools.assets.discovery_adapters.ssh_adapter import SSHDiscoveryAdapter

for _cls in (
    CSVDiscoveryAdapter,
    NetBoxDiscoveryAdapter,
    SNMPDiscoveryAdapter,
    SSHDiscoveryAdapter,
    GNS3DiscoveryAdapter,
):
    AdapterRegistry.register(_cls)

#: Lazily re-exported from the submodules that IMPORT this package's siblings.
#: Eager imports here would make ``python -m ...runner`` load runner twice —
#: once as ``tools.assets.discovery_adapters.runner`` from this file and once as
#: ``__main__`` — which Python warns about and which would give the two copies
#: separate module state.
_LAZY: dict[str, str] = {
    "FabricReport": "runner",
    "collect": "runner",
    "load_config": "runner",
    "run": "runner",
    "SinkReport": "sink",
    "persist": "sink",
}


def __getattr__(attr: str):  # PEP 562
    module_name = _LAZY.get(attr)
    if module_name is None:
        raise AttributeError(
            "module %r has no attribute %r" % (__name__, attr)
        )
    import importlib

    module = importlib.import_module(
        "tools.assets.discovery_adapters.%s" % module_name
    )
    value = getattr(module, attr)
    globals()[attr] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    "AdapterHealth",
    "AdapterRegistry",
    "CSVDiscoveryAdapter",
    "DISCOVERING_STATES",
    "DiscoveredDevice",
    "DiscoveryAdapter",
    "DiscoveryResult",
    "FabricReport",
    "GNS3DiscoveryAdapter",
    "HEALTH_STATES",
    "NOT_MEASURED_STATES",
    "NetBoxDiscoveryAdapter",
    "SNMPDiscoveryAdapter",
    "SSHDiscoveryAdapter",
    "SinkReport",
    "collect",
    "load_config",
    "persist",
    "run",
]
