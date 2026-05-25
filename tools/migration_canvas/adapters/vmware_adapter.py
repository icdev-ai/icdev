from __future__ import annotations

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""VMware vSphere REST adapter — pull live VM inventory.

Uses vSphere REST API (vCenter 6.7+).  No third-party dependencies — stdlib
urllib only.  Air-gap safe: unreachable vCenter returns empty list without
raising.

Canonical output schema (matches mc_srv_inventory columns):
    hostname, vcpus, ram_gb, disk_count, total_disk_gb, disk_type,
    nic_count, os_family, os_name, power_state, hypervisor, datacenter,
    cluster, source
"""

import json
import logging
import socket
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone

logger = get_logger("icdev.migration_canvas.adapters.vmware")

_TIMEOUT = 15


def _base_url(host: str) -> str:
    host = host.rstrip("/")
    if not host.startswith("http"):
        host = f"https://{host}"
    return host


def _session_token(host: str, user: str, password: str) -> str | None:
    """Authenticate and return vmware-api-session-id."""
    import base64
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    url = f"{_base_url(host)}/rest/com/vmware/cis/session"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            return data.get("value")
    except Exception as exc:
        logger.warning("vSphere auth failed (%s): %s", host, exc)
        return None


def _api_get(host: str, token: str, path: str) -> dict | list | None:
    """Perform authenticated GET against vSphere REST API."""
    url = f"{_base_url(host)}{path}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url, headers={"vmware-api-session-id": token, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as resp:
            return json.loads(resp.read().decode()).get("value")
    except Exception as exc:
        logger.warning("vSphere GET %s failed: %s", path, exc)
        return None


def _os_family(guest_os: str) -> str:
    g = (guest_os or "").lower()
    if "windows" in g:
        return "windows"
    if "rhel" in g or "red hat" in g:
        return "rhel"
    if "centos" in g:
        return "centos"
    if "ubuntu" in g:
        return "ubuntu"
    if "sles" in g or "suse" in g:
        return "suse"
    if "linux" in g:
        return "linux"
    return "other"


def _disk_gb(storage: dict) -> float:
    """Convert vSphere storage value (bytes) to GB."""
    committed = storage.get("committed", 0) or 0
    return round(committed / (1024 ** 3), 1)


def pull_inventory(host: str, user: str, password: str, datacenter: str | None = None) -> list[dict]:
    """Pull live VM inventory from vSphere REST API.

    Args:
        host: vCenter hostname or IP (https assumed if no scheme).
        user: vCenter username (e.g. administrator@vsphere.local).
        password: vCenter password.
        datacenter: Optional datacenter filter by name.

    Returns:
        List of canonical inventory dicts.  Empty list if vCenter unreachable.
    """
    # Connectivity check (fast fail)
    h = host.split("://")[-1].split("/")[0].split(":")[0]
    try:
        socket.setdefaulttimeout(5)
        socket.getaddrinfo(h, 443)
    except OSError:
        logger.info("vCenter %s not reachable — returning empty inventory", host)
        return []
    finally:
        socket.setdefaulttimeout(None)

    token = _session_token(host, user, password)
    if not token:
        return []

    # Fetch VMs (optionally filtered by datacenter)
    path = "/rest/vcenter/vm"
    vms_raw = _api_get(host, token, path) or []

    results = []
    for vm in vms_raw:
        vm_id = vm.get("vm", "")
        # Fetch detailed VM config
        detail = _api_get(host, token, f"/rest/vcenter/vm/{vm_id}")
        if not detail:
            # Fallback to summary fields
            detail = {}

        cpu = detail.get("cpu", {})
        memory = detail.get("memory", {})
        guest_os_info = detail.get("guest_OS", vm.get("guest_OS", ""))
        disks = detail.get("disks", {})
        nics = detail.get("nics", {})
        power_state = vm.get("power_state", detail.get("power_state", ""))

        vcpus = (cpu.get("count", 0) or 0) * (cpu.get("cores_per_socket", 1) or 1)
        ram_gb = round((memory.get("size_MiB", 0) or 0) / 1024, 1)
        disk_list = list(disks.values()) if isinstance(disks, dict) else (disks or [])
        nic_list = list(nics.values()) if isinstance(nics, dict) else (nics or [])
        total_disk_gb = sum(
            round((d.get("capacity", 0) or 0) / (1024 ** 3), 1) for d in disk_list
        )

        results.append({
            "hostname": vm.get("name", vm_id),
            "vcpus": vcpus or 1,
            "ram_gb": ram_gb or 0,
            "disk_count": len(disk_list),
            "total_disk_gb": total_disk_gb,
            "disk_type": "VMware virtual disk",
            "nic_count": len(nic_list),
            "primary_nic_gbps": 1.0,
            "os_family": _os_family(guest_os_info),
            "os_name": guest_os_info or "Unknown",
            "os_arch": "x86_64",
            "bios_type": "UEFI",
            "virtualization_ext": 1,
            "power_state": power_state,
            "hypervisor": "vmware",
            "datacenter": datacenter or "",
            "cluster": "",
            "source": "vmware_live",
            "pulled_at": datetime.now(timezone.utc).isoformat(),
        })

    logger.info("vSphere pull from %s: %d VMs", host, len(results))
    return results
