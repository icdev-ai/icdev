from __future__ import annotations

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Nutanix Prism REST adapter — pull live VM inventory.

Uses Nutanix Prism REST API v2.  No third-party dependencies — stdlib urllib.
Air-gap safe: unreachable Prism returns empty list without raising.

Canonical output schema matches mc_srv_inventory columns.
"""

import json
import socket
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone

logger = get_logger("icdev.migration_canvas.adapters.nutanix")

_TIMEOUT = 15
_PAGE_SIZE = 250


def _base_url(host: str) -> str:
    host = host.rstrip("/")
    if not host.startswith("http"):
        host = f"https://{host}:9440"
    return host


def _api_get(host: str, user: str, password: str, path: str) -> dict | None:
    """Authenticated GET against Nutanix Prism REST API v2."""
    import base64
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    url = f"{_base_url(host)}{path}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        logger.warning("Nutanix API %s → HTTP %d", path, exc.code)
        return None
    except Exception as exc:
        logger.warning("Nutanix API %s failed: %s", path, exc)
        return None


def _disk_gb(disk_list: list) -> tuple[int, float]:
    """Return (count, total_gb) from Nutanix vm_disk_info list."""
    count = 0
    total = 0.0
    for disk in (disk_list or []):
        if disk.get("is_cdrom"):
            continue
        size_bytes = disk.get("size", 0) or 0
        total += size_bytes / (1024 ** 3)
        count += 1
    return count, round(total, 1)


def _os_family(os_type: str) -> str:
    t = (os_type or "").lower()
    if "windows" in t:
        return "windows"
    if "rhel" in t or "red hat" in t:
        return "rhel"
    if "centos" in t:
        return "centos"
    if "ubuntu" in t:
        return "ubuntu"
    if "suse" in t or "sles" in t:
        return "suse"
    if "linux" in t:
        return "linux"
    return "other"


def _normalize(vm: dict, cluster: str) -> dict:
    vcpus = (vm.get("num_vcpus", 0) or 0) * (vm.get("num_cores_per_vcpu", 1) or 1)
    ram_gb = round((vm.get("memory_mb", 0) or 0) / 1024, 1)
    disks = vm.get("vm_disk_info", []) or []
    disk_count, total_disk_gb = _disk_gb(disks)
    nics = vm.get("vm_nics", []) or []
    guest_os = vm.get("guest_os", "") or ""
    power = vm.get("power_state", "") or ""
    return {
        "hostname": vm.get("name", vm.get("uuid", "unknown")),
        "vcpus": vcpus or 1,
        "ram_gb": ram_gb,
        "disk_count": disk_count,
        "total_disk_gb": total_disk_gb,
        "disk_type": "AHV virtual disk",
        "nic_count": len(nics),
        "primary_nic_gbps": 1.0,
        "os_family": _os_family(guest_os),
        "os_name": guest_os or "Unknown",
        "os_arch": "x86_64",
        "bios_type": "UEFI",
        "virtualization_ext": 1,
        "power_state": power,
        "hypervisor": "nutanix",
        "datacenter": "",
        "cluster": cluster,
        "source": "nutanix_live",
        "pulled_at": datetime.now(timezone.utc).isoformat(),
    }


def pull_inventory(host: str, user: str, password: str, cluster: str | None = None, **kwargs) -> list[dict]:
    """Pull live VM inventory from Nutanix Prism REST API.

    Args:
        host: Prism IP or hostname (https://host:9440 assumed if no scheme).
        user: Prism username.
        password: Prism password.
        cluster: Optional cluster filter string (for display only).

    Returns:
        List of canonical inventory dicts.  Empty list if Prism unreachable.
    """
    h = host.split("://")[-1].split("/")[0].split(":")[0]
    try:
        socket.setdefaulttimeout(5)
        socket.getaddrinfo(h, 9440)
    except OSError:
        logger.info("Nutanix Prism %s not reachable — returning empty inventory", host)
        return []
    finally:
        socket.setdefaulttimeout(None)

    # Nutanix Prism v2 paginates with offset/count
    results = []
    offset = 0
    while True:
        path = f"/api/nutanix/v2.0/vms/?offset={offset}&count={_PAGE_SIZE}&include_vm_disk_config=true&include_vm_nic_config=true"
        page = _api_get(host, user, password, path)
        if not page:
            break
        entities = page.get("entities", [])
        for vm in entities:
            results.append(_normalize(vm, cluster or h))
        if len(entities) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    logger.info("Nutanix pull from %s: %d VMs", host, len(results))
    return results
