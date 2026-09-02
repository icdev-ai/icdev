# CUI // SP-CTI — ICDEV Network Canvas Auto-Discovery
from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

# Classification: CUI — Controlled Unclassified Information
"""ICDEV™ Network Canvas — Live Network Auto-Discovery Agent.

Scans live networks using SNMP, SSH (CDP/LLDP), and optional ping sweep
to auto-generate JointJS topology graph JSON.  Bridges the gap between
as-designed canvas topologies and as-built reality.

Protocols supported:
  - SNMP v2c/v3: sysDescr, sysName, IF-MIB, LLDP-MIB, CISCO-CDP-MIB
  - SSH (Netmiko): ``show cdp neighbors detail``, ``show lldp neighbors detail``
  - ICMP ping sweep: discover live hosts in a subnet

Dependencies (optional — gracefully degrade):
  - pysnmp       — SNMP polling
  - netmiko      — SSH device access
  - (stdlib)     — ping sweep via subprocess

Usage:
    python tools/network/discovery.py --target 10.0.0.0/24 --method snmp --community public --json
    python tools/network/discovery.py --target 10.0.0.1 --method ssh --username admin --json
    python tools/network/discovery.py --target 10.0.0.0/24 --method ping --json
    python tools/network/discovery.py --diff --discovered disc.json --designed topo.json --json
"""

import argparse
import ipaddress
import json
import subprocess
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.network.discovery")

# ── Optional dependency probes ────────────────────────────────────────────────

_HAS_PYSNMP = False
try:
    from pysnmp.hlapi import (  # type: ignore[import-untyped]
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        UsmUserData,  # noqa: F401 — reserved for SNMPv3 support
        getCmd,
        nextCmd,
    )

    _HAS_PYSNMP = True
except ImportError:
    pass

_HAS_NETMIKO = False
try:
    from netmiko import ConnectHandler  # type: ignore[import-untyped]

    _HAS_NETMIKO = True
except ImportError:
    pass

# ── SNMP OIDs ─────────────────────────────────────────────────────────────────

OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
OID_IF_TABLE = "1.3.6.1.2.1.2.2.1"
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
# LLDP-MIB
OID_LLDP_REM_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.9"
OID_LLDP_REM_PORT_DESC = "1.0.8802.1.1.2.1.4.1.1.8"
OID_LLDP_REM_MAN_ADDR = "1.0.8802.1.1.2.1.4.2.1.4"
# Cisco CDP
OID_CDP_CACHE_DEVICE_ID = "1.3.6.1.4.1.9.9.23.1.2.1.1.6"
OID_CDP_CACHE_PLATFORM = "1.3.6.1.4.1.9.9.23.1.2.1.1.8"
OID_CDP_CACHE_ADDRESS = "1.3.6.1.4.1.9.9.23.1.2.1.1.4"

# ── Device type inference ─────────────────────────────────────────────────────

_DESCR_TO_TYPE: list[tuple[str, str]] = [
    ("adaptive security appliance", "firewall"),
    ("asa", "firewall"),
    ("fortigate", "firewall"),
    ("palo alto", "firewall"),
    ("firepower", "firewall"),
    ("pfsense", "firewall"),
    ("catalyst", "switch-l3"),
    ("nexus", "switch-l3"),
    ("switch", "switch-l2"),
    ("arista", "switch-l3"),
    ("junos", "router"),
    ("ios xr", "router"),
    ("router", "router"),
    ("cisco ios", "router"),
    ("wireless", "wap"),
    ("wlc", "wlc"),
    ("aironet", "wap"),
    ("server", "server"),
    ("linux", "server"),
    ("windows", "server"),
    ("vmware", "server"),
    ("esxi", "server"),
    ("f5", "load-balancer"),
    ("big-ip", "load-balancer"),
    ("netscaler", "load-balancer"),
    ("citrix", "load-balancer"),
]


def _infer_device_type(sys_descr: str) -> str:
    """Infer canvas node type from SNMP sysDescr string."""
    descr_lower = sys_descr.lower()
    for pattern, node_type in _DESCR_TO_TYPE:
        if pattern in descr_lower:
            return node_type
    return "server"  # default fallback


def _infer_vendor(sys_descr: str) -> str:
    """Extract vendor name from sysDescr."""
    descr_lower = sys_descr.lower()
    vendors = [
        ("cisco", "Cisco"),
        ("arista", "Arista"),
        ("juniper", "Juniper"),
        ("palo alto", "Palo Alto"),
        ("fortinet", "Fortinet"),
        ("fortigate", "Fortinet"),
        ("f5", "F5"),
        ("big-ip", "F5"),
        ("dell", "Dell"),
        ("hp", "HP"),
        ("hewlett", "HPE"),
        ("vmware", "VMware"),
        ("linux", "Linux"),
        ("microsoft", "Microsoft"),
        ("ubiquiti", "Ubiquiti"),
        ("mikrotik", "MikroTik"),
    ]
    for pattern, name in vendors:
        if pattern in descr_lower:
            return name
    return "Unknown"


# ── SNMP Discovery ────────────────────────────────────────────────────────────


def _snmp_get(target: str, oid: str, community: str = "public", port: int = 161, timeout: float = 2.0) -> str | None:
    """SNMP GET a single OID.  Returns string value or None."""
    if not _HAS_PYSNMP:
        return None
    try:
        iterator = getCmd(
            SnmpEngine(),
            CommunityData(community),
            UdpTransportTarget((target, port), timeout=timeout, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        error_indication, error_status, _error_index, var_binds = next(iterator)
        if error_indication or error_status:
            return None
        for _oid, val in var_binds:
            return str(val)
    except Exception:
        return None
    return None


def _snmp_walk(
    target: str, oid: str, community: str = "public", port: int = 161, timeout: float = 2.0
) -> list[tuple[str, str]]:
    """SNMP WALK (GETNEXT) an OID subtree.  Returns list of (oid, value) tuples."""
    if not _HAS_PYSNMP:
        return []
    results: list[tuple[str, str]] = []
    try:
        for error_indication, error_status, _error_index, var_binds in nextCmd(
            SnmpEngine(),
            CommunityData(community),
            UdpTransportTarget((target, port), timeout=timeout, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
        ):
            if error_indication or error_status:
                break
            for oid_val, val in var_binds:
                results.append((str(oid_val), str(val)))
    except Exception:
        pass
    return results


def discover_snmp(
    target: str, community: str = "public", port: int = 161, timeout: float = 2.0
) -> dict[str, Any] | None:
    """Discover a single device via SNMP.

    Returns a discovery record dict or None on failure.
    """
    if not _HAS_PYSNMP:
        logger.warning("pysnmp not installed — SNMP discovery unavailable")
        return None

    sys_descr = _snmp_get(target, OID_SYS_DESCR, community, port, timeout)
    if sys_descr is None:
        return None

    sys_name = _snmp_get(target, OID_SYS_NAME, community, port, timeout) or target
    sys_oid = _snmp_get(target, OID_SYS_OBJECT_ID, community, port, timeout) or ""

    # Collect interfaces
    interfaces: list[dict[str, Any]] = []
    if_descrs = _snmp_walk(target, OID_IF_DESCR, community, port, timeout)
    if_speeds = dict(_snmp_walk(target, OID_IF_SPEED, community, port, timeout))
    if_status = dict(_snmp_walk(target, OID_IF_OPER_STATUS, community, port, timeout))
    for oid_str, if_name in if_descrs:
        idx = oid_str.rsplit(".", 1)[-1]
        speed_oid = f"{OID_IF_SPEED}.{idx}"
        status_oid = f"{OID_IF_OPER_STATUS}.{idx}"
        interfaces.append(
            {
                "index": int(idx),
                "name": if_name,
                "speed_bps": int(if_speeds.get(speed_oid, 0)),
                "oper_status": "up" if if_status.get(status_oid) == "1" else "down",
            }
        )

    # Collect LLDP neighbors
    lldp_neighbors: list[dict[str, str]] = []
    lldp_names = _snmp_walk(target, OID_LLDP_REM_SYS_NAME, community, port, timeout)
    lldp_ports = dict(_snmp_walk(target, OID_LLDP_REM_PORT_DESC, community, port, timeout))
    for oid_str, neighbor_name in lldp_names:
        suffix = oid_str[len(str(OID_LLDP_REM_SYS_NAME)) :]
        port_oid = f"{OID_LLDP_REM_PORT_DESC}{suffix}"
        lldp_neighbors.append(
            {
                "neighbor": neighbor_name,
                "remote_port": lldp_ports.get(port_oid, ""),
                "protocol": "lldp",
            }
        )

    # Collect CDP neighbors (Cisco)
    cdp_neighbors: list[dict[str, str]] = []
    cdp_devices = _snmp_walk(target, OID_CDP_CACHE_DEVICE_ID, community, port, timeout)
    cdp_platforms = dict(_snmp_walk(target, OID_CDP_CACHE_PLATFORM, community, port, timeout))
    for oid_str, device_id in cdp_devices:
        suffix = oid_str[len(str(OID_CDP_CACHE_DEVICE_ID)) :]
        platform_oid = f"{OID_CDP_CACHE_PLATFORM}{suffix}"
        cdp_neighbors.append(
            {
                "neighbor": device_id,
                "platform": cdp_platforms.get(platform_oid, ""),
                "protocol": "cdp",
            }
        )

    return {
        "ip": target,
        "hostname": sys_name,
        "sys_descr": sys_descr,
        "sys_object_id": sys_oid,
        "device_type": _infer_device_type(sys_descr),
        "vendor": _infer_vendor(sys_descr),
        "interfaces": interfaces,
        "neighbors": lldp_neighbors + cdp_neighbors,
        "method": "snmp",
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


# ── SSH Discovery (Netmiko) ──────────────────────────────────────────────────


def _parse_cdp_detail(output: str) -> list[dict[str, str]]:
    """Parse ``show cdp neighbors detail`` output into neighbor records."""
    neighbors: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Device ID:"):
            if current:
                neighbors.append(current)
            current = {"neighbor": line.split(":", 1)[1].strip(), "protocol": "cdp"}
        elif line.startswith("Platform:"):
            # "Platform: cisco WS-C3750-24P,  Capabilities: ..."
            parts = line.split(",", 1)
            current["platform"] = parts[0].split(":", 1)[1].strip()
        elif line.startswith("Interface:"):
            # "Interface: GigabitEthernet0/1,  Port ID (outgoing port): GigabitEthernet0/1"
            parts = line.split(",", 1)
            current["local_port"] = parts[0].split(":", 1)[1].strip()
            if len(parts) > 1 and "Port ID" in parts[1]:
                current["remote_port"] = parts[1].split(":", 1)[1].strip()
        elif "IP address:" in line.lower() or "Management address" in line.lower():
            ip_part = line.split(":", 1)[1].strip() if ":" in line else ""
            if ip_part:
                current["ip"] = ip_part
    if current:
        neighbors.append(current)
    return neighbors


def _parse_lldp_detail(output: str) -> list[dict[str, str]]:
    """Parse ``show lldp neighbors detail`` output into neighbor records."""
    neighbors: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("System Name:"):
            if current.get("neighbor"):
                neighbors.append(current)
            current = {"neighbor": line.split(":", 1)[1].strip(), "protocol": "lldp"}
        elif line.startswith("Port Description:"):
            current["remote_port"] = line.split(":", 1)[1].strip()
        elif line.startswith("Management Addresses:") or "Management Address:" in line:
            ip_part = line.split(":", 1)[1].strip() if ":" in line else ""
            if ip_part:
                current["ip"] = ip_part
        elif line.startswith("System Description:"):
            current["sys_descr"] = line.split(":", 1)[1].strip()
    if current.get("neighbor"):
        neighbors.append(current)
    return neighbors


def discover_ssh(
    target: str,
    username: str,
    password: str = "",
    device_type: str = "cisco_ios",
    enable_secret: str = "",
    port: int = 22,
) -> dict[str, Any] | None:
    """Discover a device and its neighbors via SSH using Netmiko.

    Runs ``show cdp neighbors detail`` and ``show lldp neighbors detail``.
    """
    if not _HAS_NETMIKO:
        logger.warning("netmiko not installed — SSH discovery unavailable")
        return None

    device_params: dict[str, Any] = {
        "device_type": device_type,
        "host": target,
        "username": username,
        "password": password,
        "port": port,
        "timeout": 10,
    }
    if enable_secret:
        device_params["secret"] = enable_secret

    try:
        conn = ConnectHandler(**device_params)
        if enable_secret:
            conn.enable()

        hostname = conn.find_prompt().rstrip("#>")

        # Try CDP
        cdp_neighbors: list[dict[str, str]] = []
        try:
            cdp_output = conn.send_command("show cdp neighbors detail", read_timeout=15)
            cdp_neighbors = _parse_cdp_detail(cdp_output)
        except Exception:
            pass

        # Try LLDP
        lldp_neighbors: list[dict[str, str]] = []
        try:
            lldp_output = conn.send_command("show lldp neighbors detail", read_timeout=15)
            lldp_neighbors = _parse_lldp_detail(lldp_output)
        except Exception:
            pass

        # Get version info for type inference
        sys_descr = ""
        try:
            ver_output = conn.send_command("show version", read_timeout=15)
            # Take first non-empty line as sysDescr
            for vline in ver_output.splitlines():
                vline = vline.strip()
                if vline:
                    sys_descr = vline
                    break
        except Exception:
            pass

        # Get interfaces
        interfaces: list[dict[str, Any]] = []
        try:
            intf_output = conn.send_command("show ip interface brief", read_timeout=15)
            for iline in intf_output.splitlines()[1:]:  # skip header
                parts = iline.split()
                if len(parts) >= 6:
                    interfaces.append(
                        {
                            "name": parts[0],
                            "ip": parts[1] if parts[1] != "unassigned" else "",
                            "oper_status": parts[4].lower() if len(parts) > 4 else "unknown",
                        }
                    )
        except Exception:
            pass

        conn.disconnect()

        return {
            "ip": target,
            "hostname": hostname,
            "sys_descr": sys_descr,
            "device_type": _infer_device_type(sys_descr) if sys_descr else device_type.replace("_", "-"),
            "vendor": _infer_vendor(sys_descr) if sys_descr else "Unknown",
            "interfaces": interfaces,
            "neighbors": cdp_neighbors + lldp_neighbors,
            "method": "ssh",
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("SSH discovery failed for %s: %s", target, exc)
        return None


# ── Ping Sweep ────────────────────────────────────────────────────────────────


def ping_sweep(subnet: str, timeout: float = 1.0, max_hosts: int = 256) -> list[str]:
    """ICMP ping sweep a subnet.  Returns list of responding IPs.

    Uses subprocess ping (no root/raw sockets needed on most platforms).
    Limited to max_hosts to prevent accidental /16 scans.
    """
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        logger.error("Invalid subnet: %s — %s", subnet, exc)
        return []

    hosts = list(network.hosts())[:max_hosts]
    alive: list[str] = []

    for host in hosts:
        ip = str(host)
        try:
            # Windows: -n 1 -w timeout_ms; Unix: -c 1 -W timeout_s
            if sys.platform == "win32":
                cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
            else:
                cmd = ["ping", "-c", "1", "-W", str(int(timeout)), ip]
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout + 2,
            )
            if result.returncode == 0:
                alive.append(ip)
        except (subprocess.TimeoutExpired, OSError):
            pass

    return alive


# ── Topology Builder ──────────────────────────────────────────────────────────


def build_graph_json(devices: list[dict[str, Any]], layout: str = "grid") -> dict[str, Any]:
    """Convert discovered device records into JointJS graph JSON.

    Args:
        devices: List of discovery records (from discover_snmp/discover_ssh).
        layout:  "grid" (default) or "radial" positioning.

    Returns:
        {"nodes": [...], "edges": [...]} compatible with Network Canvas.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    # Track hostname/IP → node_id for dedup and edge building
    host_to_id: dict[str, str] = {}

    # Build nodes
    for i, dev in enumerate(devices):
        node_id = str(_uuid.uuid4())
        key = dev.get("hostname", dev.get("ip", f"device-{i}"))
        host_to_id[key] = node_id
        # Also map by IP
        if dev.get("ip"):
            host_to_id[dev["ip"]] = node_id

        # Grid layout: 4 columns, 200px spacing
        if layout == "radial":
            import math

            angle = (2 * math.pi * i) / max(len(devices), 1)
            radius = 250
            x = 500 + radius * math.cos(angle)
            y = 400 + radius * math.sin(angle)
        else:
            col = i % 4
            row = i // 4
            x = 100 + col * 220
            y = 100 + row * 200

        nodes.append(
            {
                "id": node_id,
                "type": dev.get("device_type", "server"),
                "label": dev.get("hostname", dev.get("ip", f"device-{i}")),
                "x": x,
                "y": y,
                "config": {
                    "hostname": dev.get("hostname", ""),
                    "ip_address": dev.get("ip", ""),
                    "vendor": dev.get("vendor", ""),
                    "sys_descr": dev.get("sys_descr", ""),
                    "discovery_method": dev.get("method", ""),
                    "discovered_at": dev.get("discovered_at", ""),
                },
            }
        )

    # Build edges from neighbor relationships
    seen_edges: set[tuple[str, str]] = set()
    for dev in devices:
        src_key = dev.get("hostname", dev.get("ip", ""))
        src_id = host_to_id.get(src_key)
        if not src_id:
            continue
        for neighbor in dev.get("neighbors", []):
            neighbor_name = neighbor.get("neighbor", "")
            neighbor_ip = neighbor.get("ip", "")
            # Try to resolve neighbor to an existing node
            dst_id = host_to_id.get(neighbor_name) or host_to_id.get(neighbor_ip)
            if not dst_id:
                # Create a stub node for the unresolved neighbor
                dst_id = str(_uuid.uuid4())
                host_to_id[neighbor_name] = dst_id
                # Infer type from platform if available
                platform = neighbor.get("platform", "")
                dev_type = _infer_device_type(platform) if platform else "server"
                idx = len(nodes)
                if layout == "radial":
                    import math

                    angle = (2 * math.pi * idx) / max(idx + 1, 1)
                    x = 500 + 350 * math.cos(angle)
                    y = 400 + 350 * math.sin(angle)
                else:
                    col = idx % 4
                    row = idx // 4
                    x = 100 + col * 220
                    y = 100 + row * 200
                nodes.append(
                    {
                        "id": dst_id,
                        "type": dev_type,
                        "label": neighbor_name or neighbor_ip or f"unknown-{idx}",
                        "x": x,
                        "y": y,
                        "config": {
                            "hostname": neighbor_name,
                            "ip_address": neighbor_ip,
                            "platform": platform,
                            "discovery_method": "neighbor",
                            "discovered_via": neighbor.get("protocol", ""),
                        },
                    }
                )

            # Deduplicate edges (undirected)
            edge_key = tuple(sorted([src_id, dst_id]))
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append(
                    {
                        "id": str(_uuid.uuid4()),
                        "source": src_id,
                        "target": dst_id,
                        "label": neighbor.get("remote_port", ""),
                        "protocol": neighbor.get("protocol", ""),
                    }
                )

    return {"nodes": nodes, "edges": edges}


# ── As-Designed vs As-Built Diff ──────────────────────────────────────────────


def diff_topologies(designed: dict[str, Any], discovered: dict[str, Any]) -> dict[str, Any]:
    """Compare designed (canvas) topology against discovered (live) topology.

    Matching is by hostname (case-insensitive) or IP address.

    Returns:
        {
            "matched": [{"designed": node, "discovered": node, "diffs": [...]}],
            "designed_only": [node, ...],      # in canvas but not found live
            "discovered_only": [node, ...],    # found live but not in canvas
            "edge_mismatches": [...],
            "summary": { ... }
        }
    """

    def _node_key(node: dict) -> str:
        """Build lookup key from hostname or IP."""
        hostname = (node.get("label", "") or "").strip().lower()
        ip = (node.get("config", {}).get("ip_address", "") or "").strip()
        return hostname or ip or node.get("id", "")

    # Index designed nodes
    designed_nodes = {_node_key(n): n for n in designed.get("nodes", [])}
    discovered_nodes = {_node_key(n): n for n in discovered.get("nodes", [])}

    matched: list[dict[str, Any]] = []
    designed_only: list[dict[str, Any]] = []
    discovered_only: list[dict[str, Any]] = []

    for key, d_node in designed_nodes.items():
        if key in discovered_nodes:
            disc_node = discovered_nodes[key]
            diffs: list[str] = []
            # Compare type
            if d_node.get("type") != disc_node.get("type"):
                diffs.append(f"type: designed={d_node.get('type')} discovered={disc_node.get('type')}")
            # Compare vendor
            d_vendor = d_node.get("config", {}).get("vendor", "")
            disc_vendor = disc_node.get("config", {}).get("vendor", "")
            if d_vendor and disc_vendor and d_vendor.lower() != disc_vendor.lower():
                diffs.append(f"vendor: designed={d_vendor} discovered={disc_vendor}")
            matched.append(
                {
                    "designed": d_node,
                    "discovered": disc_node,
                    "diffs": diffs,
                }
            )
        else:
            designed_only.append(d_node)

    for key, disc_node in discovered_nodes.items():
        if key not in designed_nodes:
            discovered_only.append(disc_node)

    # Edge comparison: compare connectivity by (src_label, dst_label) pairs
    def _edge_set(graph: dict) -> set[tuple[str, str]]:
        node_map = {n["id"]: _node_key(n) for n in graph.get("nodes", [])}
        result: set[tuple[str, str]] = set()
        for e in graph.get("edges", []):
            src = node_map.get(e.get("source"), "?")
            dst = node_map.get(e.get("target"), "?")
            result.add(tuple(sorted([src, dst])))
        return result

    designed_edges = _edge_set(designed)
    discovered_edges = _edge_set(discovered)

    edge_mismatches: list[dict[str, Any]] = []
    for edge in designed_edges - discovered_edges:
        edge_mismatches.append({"type": "designed_only", "endpoints": list(edge)})
    for edge in discovered_edges - designed_edges:
        edge_mismatches.append({"type": "discovered_only", "endpoints": list(edge)})

    total_designed = len(designed_nodes)
    total_discovered = len(discovered_nodes)
    match_count = len(matched)
    drift_count = sum(1 for m in matched if m["diffs"])

    return {
        "matched": matched,
        "designed_only": designed_only,
        "discovered_only": discovered_only,
        "edge_mismatches": edge_mismatches,
        "summary": {
            "total_designed": total_designed,
            "total_discovered": total_discovered,
            "matched": match_count,
            "with_drift": drift_count,
            "designed_only": len(designed_only),
            "discovered_only": len(discovered_only),
            "edge_designed_only": sum(1 for e in edge_mismatches if e["type"] == "designed_only"),
            "edge_discovered_only": sum(1 for e in edge_mismatches if e["type"] == "discovered_only"),
            "drift_score": round(
                (drift_count + len(designed_only) + len(discovered_only))
                / max(total_designed + total_discovered, 1)
                * 100,
                1,
            ),
        },
    }


# ── Full Discovery Orchestrator ───────────────────────────────────────────────


def run_discovery(
    targets: list[str],
    method: str = "snmp",
    community: str = "public",
    username: str = "",
    password: str = "",
    device_type: str = "cisco_ios",
    port: int = 0,
    timeout: float = 2.0,
    layout: str = "grid",
    hop_limit: int = 2,
) -> dict[str, Any]:
    """Run full discovery workflow.

    1. Expand targets (subnets → individual IPs via ping sweep if needed)
    2. Discover each reachable host
    3. Optionally crawl neighbors up to hop_limit depth
    4. Build JointJS graph JSON

    Args:
        targets:     List of IPs or CIDR subnets.
        method:      "snmp", "ssh", or "ping" (ping only finds hosts, no topology).
        community:   SNMP community string (v2c).
        username:    SSH username.
        password:    SSH password.
        device_type: Netmiko device_type for SSH connections.
        port:        Override default port (161 for SNMP, 22 for SSH).
        timeout:     Per-host timeout in seconds.
        layout:      Graph layout — "grid" or "radial".
        hop_limit:   Max neighbor hops to crawl (0 = no crawl).

    Returns:
        {
            "devices": [...],
            "graph_json": {"nodes": [...], "edges": [...]},
            "stats": { ... },
            "discovered_at": "ISO-8601"
        }
    """
    if port == 0:
        port = 161 if method == "snmp" else 22

    # Expand subnets
    expanded_ips: list[str] = []
    for t in targets:
        try:
            net = ipaddress.ip_network(t, strict=False)
            if net.num_addresses > 1:
                # Subnet — ping sweep first
                alive = ping_sweep(str(net), timeout=timeout)
                expanded_ips.extend(alive)
            else:
                expanded_ips.append(str(net.network_address))
        except ValueError:
            # Treat as hostname
            expanded_ips.append(t)

    # Deduplicate
    expanded_ips = list(dict.fromkeys(expanded_ips))

    # Discover each host
    all_devices: list[dict[str, Any]] = []
    discovered_hosts: set[str] = set()

    def _discover_one(ip: str) -> dict[str, Any] | None:
        if method == "snmp":
            return discover_snmp(ip, community, port, timeout)
        elif method == "ssh":
            return discover_ssh(ip, username, password, device_type, port=port)
        return None

    # Phase 1: direct targets
    for ip in expanded_ips:
        if ip in discovered_hosts:
            continue
        dev = _discover_one(ip)
        if dev:
            all_devices.append(dev)
            discovered_hosts.add(ip)
            if dev.get("hostname"):
                discovered_hosts.add(dev["hostname"])

    # Phase 2: neighbor crawl
    for hop in range(hop_limit):
        new_targets: list[str] = []
        for dev in list(all_devices):
            for nbr in dev.get("neighbors", []):
                nbr_ip = nbr.get("ip", "")
                nbr_name = nbr.get("neighbor", "")
                if nbr_ip and nbr_ip not in discovered_hosts:
                    new_targets.append(nbr_ip)
                elif nbr_name and nbr_name not in discovered_hosts:
                    # Can't crawl by hostname without DNS — skip
                    pass
        if not new_targets:
            break
        for ip in new_targets:
            if ip in discovered_hosts:
                continue
            dev = _discover_one(ip)
            if dev:
                all_devices.append(dev)
                discovered_hosts.add(ip)
                if dev.get("hostname"):
                    discovered_hosts.add(dev["hostname"])

    # Build graph
    graph_json = build_graph_json(all_devices, layout=layout)

    return {
        "devices": all_devices,
        "graph_json": graph_json,
        "stats": {
            "targets_scanned": len(expanded_ips),
            "devices_discovered": len(all_devices),
            "nodes_generated": len(graph_json["nodes"]),
            "edges_generated": len(graph_json["edges"]),
            "method": method,
            "hop_limit": hop_limit,
        },
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli():
    """CLI entry point for standalone discovery."""
    parser = argparse.ArgumentParser(
        description="ICDEV Network Canvas — Live Auto-Discovery Agent",
    )
    parser.add_argument("--target", nargs="+", help="IP addresses or CIDR subnets to scan")
    parser.add_argument("--method", choices=["snmp", "ssh", "ping"], default="snmp", help="Discovery method")
    parser.add_argument("--community", default="public", help="SNMP community string (v2c)")
    parser.add_argument("--username", default="", help="SSH username")
    parser.add_argument("--password", default="", help="SSH password")
    parser.add_argument("--device-type", default="cisco_ios", help="Netmiko device type for SSH")
    parser.add_argument("--port", type=int, default=0, help="Override port (default: 161/SNMP, 22/SSH)")
    parser.add_argument("--timeout", type=float, default=2.0, help="Per-host timeout in seconds")
    parser.add_argument("--hop-limit", type=int, default=2, help="Max neighbor crawl depth (0=none)")
    parser.add_argument("--layout", choices=["grid", "radial"], default="grid", help="Graph layout")
    parser.add_argument("--diff", action="store_true", help="Run diff mode (compare discovered vs designed)")
    parser.add_argument("--discovered", help="Path to discovered graph JSON (for --diff)")
    parser.add_argument("--designed", help="Path to designed graph JSON (for --diff)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--gate", action="store_true", help="Gate mode — exit 1 if drift > 20%%")

    args = parser.parse_args()

    if args.diff:
        if not args.discovered or not args.designed:
            print("ERROR: --diff requires --discovered and --designed paths", file=sys.stderr)
            sys.exit(1)
        with open(args.discovered, encoding="utf-8") as f:
            disc = json.load(f)
        with open(args.designed, encoding="utf-8") as f:
            des = json.load(f)
        result = diff_topologies(des, disc)
        if args.json:
            json.dump(result, sys.stdout, indent=2, default=str)
        else:
            s = result["summary"]
            print(f"Designed: {s['total_designed']}  Discovered: {s['total_discovered']}")
            print(f"Matched: {s['matched']}  With drift: {s['with_drift']}")
            print(f"Designed-only: {s['designed_only']}  Discovered-only: {s['discovered_only']}")
            print(
                f"Edge mismatches: designed-only={s['edge_designed_only']}, discovered-only={s['edge_discovered_only']}"
            )
            print(f"Drift score: {s['drift_score']}%")
        if args.gate and result["summary"]["drift_score"] > 20:
            sys.exit(1)
        return

    if not args.target:
        print("ERROR: --target required (IP or CIDR)", file=sys.stderr)
        sys.exit(1)

    if args.method == "ping":
        alive = []
        for t in args.target:
            alive.extend(ping_sweep(t, timeout=args.timeout))
        result_data: dict[str, Any] = {
            "alive_hosts": alive,
            "count": len(alive),
            "graph_json": build_graph_json(
                [
                    {
                        "ip": ip,
                        "hostname": ip,
                        "device_type": "server",
                        "vendor": "Unknown",
                        "neighbors": [],
                        "method": "ping",
                        "discovered_at": datetime.now(timezone.utc).isoformat(),
                    }
                    for ip in alive
                ],
                layout=args.layout,
            ),
        }
        if args.json:
            json.dump(result_data, sys.stdout, indent=2, default=str)
        else:
            print(f"Alive hosts ({len(alive)}):")
            for ip in alive:
                print(f"  {ip}")
        return

    result = run_discovery(
        targets=args.target,
        method=args.method,
        community=args.community,
        username=args.username,
        password=args.password,
        device_type=args.device_type,
        port=args.port,
        timeout=args.timeout,
        layout=args.layout,
        hop_limit=args.hop_limit,
    )
    if args.json:
        json.dump(result, sys.stdout, indent=2, default=str)
    else:
        print(
            f"Discovered {result['stats']['devices_discovered']} devices, "
            f"{result['stats']['nodes_generated']} nodes, "
            f"{result['stats']['edges_generated']} edges"
        )
        for dev in result["devices"]:
            print(
                f"  [{dev['device_type']}] {dev['hostname']} ({dev['ip']}) "
                f"— {dev['vendor']}, {len(dev.get('neighbors', []))} neighbors"
            )


if __name__ == "__main__":
    _cli()
