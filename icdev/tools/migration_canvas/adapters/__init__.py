from __future__ import annotations
# CUI // SP-CTI
"""Migration Canvas — hypervisor adapter package.

Adapters pull live VM inventory from VMware vSphere, Hyper-V, and Nutanix Prism.
All adapters are air-gap safe: unreachable hosts return an empty list + status dict.
"""

from .vmware_adapter import pull_inventory as vmware_pull
from .hyperv_adapter import pull_inventory as hyperv_pull
from .nutanix_adapter import pull_inventory as nutanix_pull

ADAPTER_MAP = {
    "vmware": vmware_pull,
    "hyperv": hyperv_pull,
    "nutanix": nutanix_pull,
}


def pull_inventory(adapter_type: str, host: str, user: str, password: str, **kwargs) -> dict:
    """Dispatch to the correct adapter and normalize the result.

    Returns:
        {
            "ok": bool,
            "adapter": str,
            "host": str,
            "vms": [canonical inventory dicts],
            "vm_count": int,
            "error": str | None,
        }
    """
    fn = ADAPTER_MAP.get(adapter_type)
    if not fn:
        return {"ok": False, "adapter": adapter_type, "host": host,
                "vms": [], "vm_count": 0, "error": f"Unknown adapter: {adapter_type}"}
    try:
        vms = fn(host, user, password, **kwargs)
        return {"ok": True, "adapter": adapter_type, "host": host,
                "vms": vms, "vm_count": len(vms), "error": None}
    except Exception as exc:
        return {"ok": False, "adapter": adapter_type, "host": host,
                "vms": [], "vm_count": 0, "error": str(exc)}
