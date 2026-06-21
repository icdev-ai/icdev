# CUI // SP-CTI
"""Synthetic Network Config Generator — creates realistic Cisco IOS-style configs.

Generates multi-device topologies with:
  • Cisco IOS router, L3 switch, and firewall configs
  • Coherent IP addressing (RFC 1918) and VLAN plans
  • Drift variants: IP changes, VLAN modifications, ACL alterations, interface downs

Usage:
    from tools.network.synthetic_config_gen import generate_topology, apply_drift

    baseline = generate_topology("topo-demo-01", device_count=4)
    drifted  = apply_drift(baseline, intensity="medium")
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

# ── Device types ──────────────────────────────────────────────────────────────

_VENDORS = ["Cisco", "Juniper", "Arista"]

_DEVICE_TEMPLATES = {
    "router": {
        "hostname_prefix": "RTR",
        "vendor": "Cisco",
        "interfaces": ["GigabitEthernet0/0", "GigabitEthernet0/1", "GigabitEthernet0/2"],
        "loopback": "Loopback0",
    },
    "l3switch": {
        "hostname_prefix": "SW",
        "vendor": "Cisco",
        "interfaces": ["GigabitEthernet1/0/1", "GigabitEthernet1/0/2", "GigabitEthernet1/0/3"],
        "loopback": None,
    },
    "firewall": {
        "hostname_prefix": "FW",
        "vendor": "Cisco",
        "interfaces": ["GigabitEthernet0/0", "GigabitEthernet0/1"],
        "loopback": None,
    },
}

_VLAN_NAMES = [
    ("10", "MGMT"), ("20", "SERVERS"), ("30", "USER"), ("40", "VOICE"),
    ("50", "GUEST"), ("100", "TRANSIT"), ("200", "DMZ"), ("999", "BLACKHOLE"),
]

_ACL_ENTRIES = [
    "permit tcp 10.0.0.0 0.255.255.255 any eq 443",
    "permit tcp 10.0.0.0 0.255.255.255 any eq 80",
    "permit udp any any eq 53",
    "permit icmp 10.0.0.0 0.255.255.255 any",
    "deny ip any any log",
]

_BANNER_MOTD = (
    "AUTHORIZED USERS ONLY. This system is the property of ACME Corp.\n"
    "Unauthorized access is prohibited and will be prosecuted.\n"
    "All activity is monitored and logged."
)


# ── Config builders ────────────────────────────────────────────────────────────

def _router_config(
    hostname: str,
    device_index: int,
    interfaces: list[str],
    loopback: str | None,
    vlans: list[tuple[str, str]],
    acl_entries: list[str],
    banner: str = _BANNER_MOTD,
) -> str:
    lines: list[str] = []
    lines.append(f"!")
    lines.append(f"hostname {hostname}")
    lines.append(f"!")
    lines.append(f"ip domain-name icdev.local")
    lines.append(f"!")

    # Loopback
    if loopback:
        lo_ip = f"10.255.{device_index}.1"
        lines += [
            f"interface {loopback}",
            f" ip address {lo_ip} 255.255.255.255",
            f" no shutdown",
            f"!",
        ]

    # Physical interfaces
    for i, iface in enumerate(interfaces):
        subnet = f"10.{device_index}.{i+1}"
        ip = f"{subnet}.1"
        mask = "255.255.255.0"
        lines += [
            f"interface {iface}",
            f" description Link-to-segment-{i+1}",
            f" ip address {ip} {mask}",
            f" no shutdown",
            f"!",
        ]

    # VLANs (for switches)
    for vid, vname in vlans:
        lines += [f"vlan {vid}", f" name {vname}", f"!"]

    # Static routes
    lines.append(f"ip route 0.0.0.0 0.0.0.0 10.{device_index}.1.254")
    lines.append(f"!")

    # ACL
    lines.append(f"ip access-list extended MGMT-IN")
    for entry in acl_entries:
        lines.append(f" {entry}")
    lines.append(f"!")

    # SNMP
    lines += [
        f"snmp-server community icdev-ro RO",
        f"snmp-server trap-source Loopback0" if loopback else f"snmp-server trap-source {interfaces[0]}",
        f"snmp-server host 10.0.0.10 version 2c icdev-ro",
        f"!",
    ]

    # Banner
    lines += [
        f"banner motd ^C",
        banner,
        f"^C",
        f"!",
    ]

    lines.append("end")
    return "\n".join(lines)


def _switch_config(
    hostname: str,
    device_index: int,
    interfaces: list[str],
    vlans: list[tuple[str, str]],
    acl_entries: list[str],
) -> str:
    lines: list[str] = [f"!", f"hostname {hostname}", f"!"]

    for vid, vname in vlans:
        lines += [f"vlan {vid}", f" name {vname}", f"!"]

    for i, iface in enumerate(interfaces):
        vid, _ = vlans[i % len(vlans)]
        lines += [
            f"interface {iface}",
            f" switchport mode access",
            f" switchport access vlan {vid}",
            f" spanning-tree portfast",
            f" no shutdown",
            f"!",
        ]

    # SVI for management VLAN
    mgmt_ip = f"10.{device_index}.10.1"
    lines += [
        f"interface Vlan10",
        f" description MGMT-SVI",
        f" ip address {mgmt_ip} 255.255.255.0",
        f" no shutdown",
        f"!",
    ]

    lines.append("end")
    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────

@dataclass
class SyntheticTopology:
    topology_id: str
    device_configs: dict[str, str] = field(default_factory=dict)  # device_id → config_text
    device_meta: dict[str, dict[str, Any]] = field(default_factory=dict)  # device_id → metadata


def generate_topology(
    topology_id: str,
    device_count: int = 4,
    *,
    include_firewall: bool = True,
    seed: int = 42,
) -> SyntheticTopology:
    """Generate a coherent synthetic network topology.

    Args:
        topology_id: Unique identifier for this topology.
        device_count: Number of devices (1 firewall + N-1 routers/switches).
        include_firewall: Whether to include a firewall as the first device.
        seed: Random seed for reproducibility.

    Returns:
        SyntheticTopology with Cisco IOS-style config text for each device.
    """
    rng = random.Random(seed)
    topo = SyntheticTopology(topology_id=topology_id)

    vlans = _VLAN_NAMES[:min(len(_VLAN_NAMES), device_count + 2)]
    acl_entries = list(_ACL_ENTRIES)

    devices: list[tuple[str, str]] = []  # (device_id, type)

    if include_firewall and device_count >= 1:
        devices.append(("fw-01", "firewall"))
    for i in range(1, device_count):
        dtype = "l3switch" if i % 3 == 0 else "router"
        devices.append((f"{'sw' if dtype == 'l3switch' else 'rtr'}-{i:02d}", dtype))

    for idx, (device_id, dtype) in enumerate(devices):
        tmpl = _DEVICE_TEMPLATES[dtype]
        hostname = f"{tmpl['hostname_prefix']}-{topology_id.upper()}-{idx+1:02d}"
        ifaces = tmpl["interfaces"]

        if dtype == "l3switch":
            cfg = _switch_config(hostname, idx + 1, ifaces, vlans, acl_entries)
        else:
            cfg = _router_config(
                hostname, idx + 1, ifaces, tmpl.get("loopback"),
                vlans if dtype != "firewall" else [], acl_entries,
            )

        topo.device_configs[device_id] = cfg
        topo.device_meta[device_id] = {
            "hostname": hostname,
            "type": dtype,
            "vendor": tmpl["vendor"],
            "vlan_count": len(vlans),
        }

    return topo


def apply_drift(
    baseline: SyntheticTopology,
    intensity: str = "medium",
    *,
    seed: int = 99,
) -> SyntheticTopology:
    """Apply realistic config drift to a baseline topology.

    Drift intensity controls how many and which categories are changed:
      low    — 1-2 interface state changes
      medium — IP changes + VLAN renames + interface downs
      high   — IP changes + ACL removals + VLAN deletions + missing device

    Args:
        baseline: The baseline topology to drift from.
        intensity: 'low' | 'medium' | 'high'
        seed: Random seed.

    Returns:
        A new SyntheticTopology with drifted configs (baseline is unchanged).
    """
    rng = random.Random(seed)
    drifted = SyntheticTopology(
        topology_id=baseline.topology_id,
        device_meta=dict(baseline.device_meta),
    )

    devices = list(baseline.device_configs.items())

    for device_id, config_text in devices:
        lines = config_text.splitlines()
        new_lines: list[str] = []

        for line in lines:
            # LOW: shut down an interface on 1 device
            if intensity in ("medium", "high") and device_id == devices[0][0]:
                if "GigabitEthernet0/2" in line and "description" in line:
                    new_lines.append(line)
                    new_lines.append(" shutdown")
                    continue

            # MEDIUM: change one IP address on second device
            if intensity in ("medium", "high") and device_id == devices[min(1, len(devices)-1)][0]:
                m_ip = __import__("re").match(r"^( ip address )(\S+)( .+)$", line)
                if m_ip and "10." in m_ip.group(2):
                    parts = m_ip.group(2).split(".")
                    if len(parts) == 4:
                        parts[3] = str((int(parts[3]) + 50) % 254 or 1)
                        new_ip = ".".join(parts)
                        new_lines.append(f"{m_ip.group(1)}{new_ip}{m_ip.group(3)}")
                        continue

            # MEDIUM: rename a VLAN
            if intensity in ("medium", "high") and "name SERVERS" in line:
                new_lines.append(line.replace("SERVERS", "APP-SERVERS"))
                continue

            # HIGH: remove an ACL entry
            if intensity == "high" and "permit tcp" in line and "443" in line:
                continue  # drop this line

            # HIGH: drop a VLAN entirely
            if intensity == "high" and "vlan 50" in line.lower():
                continue
            if intensity == "high" and "name GUEST" in line:
                continue

            new_lines.append(line)

        drifted.device_configs[device_id] = "\n".join(new_lines)

    # HIGH: remove one device entirely
    if intensity == "high" and len(devices) > 2:
        last_device_id = devices[-1][0]
        del drifted.device_configs[last_device_id]
        drifted.device_meta.pop(last_device_id, None)

    return drifted


def topology_to_snapshot(topo: SyntheticTopology) -> dict[str, str]:
    """Convert a SyntheticTopology to the snapshot dict format used by drift_detector."""
    return dict(topo.device_configs)
