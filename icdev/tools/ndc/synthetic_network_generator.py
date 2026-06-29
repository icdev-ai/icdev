"""
ICDEV™ Network Design Canvas — Synthetic Multi-Vendor Network Data Generator

Generates realistic synthetic network infrastructure for:
  - WAM (Wide Area Mesh)      : dual-homed BGP to multiple ISPs
  - MAN (Metro Area Network)  : MPLS L3VPN core with PE-CE
  - LAN (Local Area Network)  : campus 3-tier with VLANs / dot1x
  - DCAM (Data Center Area Mesh): spine-leaf with BGP-EVPN, VXLAN

Vendors: Cisco, Juniper, Arista, Palo Alto, Fortinet
Tables: nc_hardware_profiles, nc_sites, topologies, ni_devices,
         nc_circuits, nc_racks, ni_device_configs

Usage:
  python tools/ndc/synthetic_network_generator.py [--reset] [--seed 42] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_NOW = datetime.now(timezone.utc)
_CUSTOMER_ID = "CUST-ICDEV-DEMO"

# ══════════════════════════════════════════════════════════════════════════════
# Hardware profile catalog — all 5 vendors
# ══════════════════════════════════════════════════════════════════════════════

_HARDWARE_PROFILES: list[dict] = [
    # ── Cisco ──
    {
        "vendor": "Cisco", "model": "ISR 4431", "model_family": "ISR 4000",
        "device_type": "router", "rack_units": 2,
        "power_typical_w": 350, "power_max_w": 500,
        "throughput_gbps": 1.0, "pps_mpps": 2.0,
        "routing_table_size": 500000, "arp_table_size": 32000,
        "mac_table_size": 16000, "nat_sessions": 500000, "vpn_tunnels": 2000,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"GE-0/0","count":4,"speed":"1G"},{"name":"GE-0/1","count":2,"speed":"1G"}]),
        "eol_date": "2027-01-31", "eos_date": "2029-01-31",
        "replacement_cost": 18500.0, "annual_maintenance_pct": 0.12,
    },
    {
        "vendor": "Cisco", "model": "ASR 1001-X", "model_family": "ASR 1000",
        "device_type": "router", "rack_units": 2,
        "power_typical_w": 450, "power_max_w": 650,
        "throughput_gbps": 2.5, "pps_mpps": 4.0,
        "routing_table_size": 1000000, "arp_table_size": 64000,
        "mac_table_size": 32000, "nat_sessions": 2000000, "vpn_tunnels": 10000,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"GE-0/0","count":6,"speed":"1G"},{"name":"TEN-0/0","count":2,"speed":"10G"}]),
        "eol_date": "2028-06-30", "eos_date": "2030-06-30",
        "replacement_cost": 45000.0, "annual_maintenance_pct": 0.14,
    },
    {
        "vendor": "Cisco", "model": "Catalyst 9300-48P", "model_family": "Catalyst 9300",
        "device_type": "switch-l3", "rack_units": 1,
        "power_typical_w": 250, "power_max_w": 400,
        "throughput_gbps": 176.0, "pps_mpps": 130.0,
        "routing_table_size": 16000, "arp_table_size": 48000,
        "mac_table_size": 32000, "nat_sessions": 0, "vpn_tunnels": 0,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"GE","count":48,"speed":"1G"},{"name":"TEN","count":4,"speed":"10G"}]),
        "eol_date": "2030-07-31", "eos_date": "2032-07-31",
        "replacement_cost": 8500.0, "annual_maintenance_pct": 0.10,
    },
    {
        "vendor": "Cisco", "model": "Nexus 93180YC-EX", "model_family": "Nexus 9300",
        "device_type": "switch-l3", "rack_units": 1,
        "power_typical_w": 300, "power_max_w": 450,
        "throughput_gbps": 1.6, "pps_mpps": 1800.0,
        "routing_table_size": 32000, "arp_table_size": 96000,
        "mac_table_size": 288000, "nat_sessions": 0, "vpn_tunnels": 0,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"TEN","count":48,"speed":"10G"},{"name":"100G","count":6,"speed":"100G"}]),
        "eol_date": "2029-03-31", "eos_date": "2031-03-31",
        "replacement_cost": 22000.0, "annual_maintenance_pct": 0.13,
    },
    {
        "vendor": "Cisco", "model": "ASA 5585-X SSP-40", "model_family": "ASA 5500-X",
        "device_type": "firewall", "rack_units": 2,
        "power_typical_w": 600, "power_max_w": 850,
        "throughput_gbps": 20.0, "pps_mpps": 15.0,
        "routing_table_size": 500000, "arp_table_size": 64000,
        "mac_table_size": 64000, "nat_sessions": 10000000, "vpn_tunnels": 10000,
        "vlan_count": 512,
        "ports_json": json.dumps([{"name":"GE","count":8,"speed":"1G"},{"name":"TEN","count":4,"speed":"10G"}]),
        "eol_date": "2026-08-31", "eos_date": "2028-08-31",
        "replacement_cost": 75000.0, "annual_maintenance_pct": 0.18,
    },
    # ── Juniper ──
    {
        "vendor": "Juniper", "model": "MX204", "model_family": "MX",
        "device_type": "router", "rack_units": 1,
        "power_typical_w": 350, "power_max_w": 500,
        "throughput_gbps": 1.6, "pps_mpps": 1800.0,
        "routing_table_size": 2000000, "arp_table_size": 64000,
        "mac_table_size": 32000, "nat_sessions": 2000000, "vpn_tunnels": 64000,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"TEN","count":8,"speed":"10G"},{"name":"100G","count":4,"speed":"100G"}]),
        "eol_date": "2028-12-31", "eos_date": "2030-12-31",
        "replacement_cost": 55000.0, "annual_maintenance_pct": 0.14,
    },
    {
        "vendor": "Juniper", "model": "EX4650-48Y", "model_family": "EX",
        "device_type": "switch-l3", "rack_units": 1,
        "power_typical_w": 200, "power_max_w": 320,
        "throughput_gbps": 2.0, "pps_mpps": 1480.0,
        "routing_table_size": 16000, "arp_table_size": 48000,
        "mac_table_size": 288000, "nat_sessions": 0, "vpn_tunnels": 0,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"TEN","count":48,"speed":"10G"},{"name":"100G","count":8,"speed":"100G"}]),
        "eol_date": "2029-06-30", "eos_date": "2031-06-30",
        "replacement_cost": 18000.0, "annual_maintenance_pct": 0.12,
    },
    {
        "vendor": "Juniper", "model": "SRX 4200", "model_family": "SRX",
        "device_type": "firewall", "rack_units": 1,
        "power_typical_w": 280, "power_max_w": 400,
        "throughput_gbps": 20.0, "pps_mpps": 18.0,
        "routing_table_size": 500000, "arp_table_size": 64000,
        "mac_table_size": 32000, "nat_sessions": 5000000, "vpn_tunnels": 10000,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"GE","count":8,"speed":"1G"},{"name":"TEN","count":4,"speed":"10G"}]),
        "eol_date": "2028-03-31", "eos_date": "2030-03-31",
        "replacement_cost": 35000.0, "annual_maintenance_pct": 0.15,
    },
    # ── Arista ──
    {
        "vendor": "Arista", "model": "7050X3-32S", "model_family": "7050X3",
        "device_type": "switch-l3", "rack_units": 1,
        "power_typical_w": 180, "power_max_w": 280,
        "throughput_gbps": 3.2, "pps_mpps": 2400.0,
        "routing_table_size": 128000, "arp_table_size": 128000,
        "mac_table_size": 288000, "nat_sessions": 0, "vpn_tunnels": 0,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"TEN","count":32,"speed":"10G"},{"name":"100G","count":4,"speed":"100G"}]),
        "eol_date": "2029-09-30", "eos_date": "2031-09-30",
        "replacement_cost": 16000.0, "annual_maintenance_pct": 0.11,
    },
    {
        "vendor": "Arista", "model": "7280R3-48S6", "model_family": "7280R3",
        "device_type": "router", "rack_units": 1,
        "power_typical_w": 220, "power_max_w": 350,
        "throughput_gbps": 4.8, "pps_mpps": 3600.0,
        "routing_table_size": 2000000, "arp_table_size": 256000,
        "mac_table_size": 288000, "nat_sessions": 0, "vpn_tunnels": 0,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"TEN","count":48,"speed":"10G"},{"name":"100G","count":6,"speed":"100G"}]),
        "eol_date": "2030-01-31", "eos_date": "2032-01-31",
        "replacement_cost": 32000.0, "annual_maintenance_pct": 0.12,
    },
    # ── Palo Alto ──
    {
        "vendor": "Palo Alto", "model": "PA-3220", "model_family": "PA-3200",
        "device_type": "firewall", "rack_units": 1,
        "power_typical_w": 240, "power_max_w": 350,
        "throughput_gbps": 2.0, "pps_mpps": 2.5,
        "routing_table_size": 500000, "arp_table_size": 64000,
        "mac_table_size": 64000, "nat_sessions": 4000000, "vpn_tunnels": 4000,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"GE","count":12,"speed":"1G"},{"name":"TEN","count":4,"speed":"10G"}]),
        "eol_date": "2028-11-30", "eos_date": "2030-11-30",
        "replacement_cost": 28000.0, "annual_maintenance_pct": 0.16,
    },
    {
        "vendor": "Palo Alto", "model": "PA-5250", "model_family": "PA-5200",
        "device_type": "firewall", "rack_units": 2,
        "power_typical_w": 450, "power_max_w": 650,
        "throughput_gbps": 10.0, "pps_mpps": 12.0,
        "routing_table_size": 1000000, "arp_table_size": 128000,
        "mac_table_size": 128000, "nat_sessions": 12000000, "vpn_tunnels": 12000,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"TEN","count":12,"speed":"10G"},{"name":"40G","count":4,"speed":"40G"}]),
        "eol_date": "2030-05-31", "eos_date": "2032-05-31",
        "replacement_cost": 95000.0, "annual_maintenance_pct": 0.18,
    },
    # ── Fortinet ──
    {
        "vendor": "Fortinet", "model": "FortiGate 100F", "model_family": "FortiGate F",
        "device_type": "firewall", "rack_units": 1,
        "power_typical_w": 65, "power_max_w": 100,
        "throughput_gbps": 0.02, "pps_mpps": 0.025,
        "routing_table_size": 250000, "arp_table_size": 64000,
        "mac_table_size": 32000, "nat_sessions": 2000000, "vpn_tunnels": 5000,
        "vlan_count": 1024,
        "ports_json": json.dumps([{"name":"GE","count":16,"speed":"1G"},{"name":"TEN","count":2,"speed":"10G"}]),
        "eol_date": "2028-09-30", "eos_date": "2030-09-30",
        "replacement_cost": 4500.0, "annual_maintenance_pct": 0.10,
    },
    {
        "vendor": "Fortinet", "model": "FortiGate 600F", "model_family": "FortiGate F",
        "device_type": "firewall", "rack_units": 2,
        "power_typical_w": 200, "power_max_w": 300,
        "throughput_gbps": 0.05, "pps_mpps": 0.06,
        "routing_table_size": 500000, "arp_table_size": 128000,
        "mac_table_size": 64000, "nat_sessions": 8000000, "vpn_tunnels": 20000,
        "vlan_count": 4094,
        "ports_json": json.dumps([{"name":"GE","count":8,"speed":"1G"},{"name":"TEN","count":8,"speed":"10G"},{"name":"40G","count":2,"speed":"40G"}]),
        "eol_date": "2030-02-28", "eos_date": "2032-02-28",
        "replacement_cost": 22000.0, "annual_maintenance_pct": 0.12,
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# Site definitions
# ══════════════════════════════════════════════════════════════════════════════

_SITES: list[dict] = [
    # WAM POPs
    {"name": "WAM-POP-ASH", "city": "Ashburn", "state": "VA", "site_type": "pop", "address": "12345 Data Center Blvd"},
    {"name": "WAM-POP-DAL", "city": "Dallas", "state": "TX", "site_type": "pop", "address": "6789 Telecom Pkwy"},
    # MAN aggregation
    {"name": "MAN-AGG-DEN", "city": "Denver", "state": "CO", "site_type": "aggregation", "address": "1111 Network Way"},
    {"name": "MAN-AGG-CHI", "city": "Chicago", "state": "IL", "site_type": "aggregation", "address": "2222 Fiber Ln"},
    # LAN campuses
    {"name": "LAN-HQ-VA", "city": "Arlington", "state": "VA", "site_type": "office", "address": "1 Pentagon Row"},
    {"name": "LAN-CAMPUS-CA", "city": "San Jose", "state": "CA", "site_type": "office", "address": "4567 Campus Dr"},
    {"name": "LAN-CAMPUS-TX", "city": "Austin", "state": "TX", "site_type": "office", "address": "8900 Research Blvd"},
    # DCAM
    {"name": "DCAM-EAST", "city": "Ashburn", "state": "VA", "site_type": "data_center", "address": "99 Cloud Loop"},
]


# ══════════════════════════════════════════════════════════════════════════════
# IP addressing helpers
# ══════════════════════════════════════════════════════════════════════════════

class _IPAlloc:
    """Simple sequential IP allocator for deterministic demo addressing."""

    def __init__(self, base: str):
        self._net = ipaddress.IPv4Network(base)
        self._hosts = iter(self._net.hosts())
        self._subnets: list[ipaddress.IPv4Network] = list(self._net.subnets(new_prefix=30))
        self._subnet_idx = 0
        self._vlan_base = 100

    def next_host(self) -> str:
        return str(next(self._hosts))

    def next_subnet(self) -> tuple[str, str]:
        """Return (network CIDR, first usable host) for a /30."""
        subnet = self._subnets[self._subnet_idx]
        self._subnet_idx += 1
        hosts = list(subnet.hosts())
        return str(subnet), str(hosts[0])

    def next_vlan(self) -> int:
        self._vlan_base += 1
        return self._vlan_base


def _hosts(subnet: str) -> list:
    return list(ipaddress.IPv4Network(subnet).hosts())


# ══════════════════════════════════════════════════════════════════════════════
# Topology builders
# ══════════════════════════════════════════════════════════════════════════════

_TOPOLOGY_BUILDERS: list[dict] = []


def _build_wam_topology(seed: int) -> dict:
    """WAM: Dual-homed BGP to multiple ISPs at 2 POPs."""
    random.seed(seed)
    topo_id = f"topo-wam-{seed}"
    ip = _IPAlloc("10.0.0.0/16")
    nodes: list[dict] = []
    edges: list[dict] = []

    # ISP routers (simulated upstream peers)
    isp_nodes = [
        {"id": "isp-att", "label": "ISP-ATT", "type": "pe-router", "config": {"asn": 7018, "ip": "203.0.113.1/32"}},
        {"id": "isp-lumen", "label": "ISP-LUMEN", "type": "pe-router", "config": {"asn": 3356, "ip": "198.51.100.1/32"}},
        {"id": "isp-vz", "label": "ISP-VERIZON", "type": "pe-router", "config": {"asn": 701, "ip": "192.0.2.1/32"}},
    ]
    nodes.extend(isp_nodes)

    # Edge routers at each POP
    for pop_idx, pop_name in enumerate(["WAM-POP-ASH", "WAM-POP-DAL"]):
        _ = seed + pop_idx * 100  # pop_seed (kept for readability)
        edge_id = f"edge-{pop_name.lower().replace('_','-')}"
        edge_ip = ip.next_host()
        edge = {
            "id": edge_id,
            "label": f"EDGE-{pop_name.split('-')[-1]}",
            "type": "router",
            "config": {
                "vendor": "Cisco", "model": "ASR 1001-X", "os": "ios_xr",
                "hostname": f"edge-{pop_name.lower().replace('_','-')}",
                "asn": 65001 + pop_idx, "ip": f"{edge_ip}/32",
                "local_pref": 100 + pop_idx * 10, "bfd": True,
            },
        }
        nodes.append(edge)

        # ISP links
        for isp in isp_nodes:
            subnet, edge_side = ip.next_subnet()
            _, isp_side = ip.next_subnet()
            # Actually reuse same subnet: edge gets .1, isp gets .2
            hosts = list(ipaddress.IPv4Network(subnet).hosts())
            edge_ip_addr = str(hosts[0])
            _ = str(hosts[1])  # isp_ip_addr
            vrf_name = f"WAN-{isp['label'].split('-')[1].upper()}"
            edges.append({
                "id": f"e-{edge_id}-{isp['id']}",
                "source": edge_id, "target": isp["id"],
                "label": "eBGP", "protocol": "bgp",
                "config": {"ip": f"{edge_ip_addr}/30", "vrf": vrf_name},
            })

        # Core router inside POP
        core_id = f"core-{pop_name.lower().replace('_','-')}"
        core_ip = ip.next_host()
        core = {
            "id": core_id,
            "label": f"CORE-{pop_name.split('-')[-1]}",
            "type": "router",
            "config": {
                "vendor": "Juniper", "model": "MX204", "os": "junos",
                "hostname": core_id,
                "asn": 65001 + pop_idx, "ip": f"{core_ip}/32",
                "ospf_area": 0,
            },
        }
        nodes.append(core)
        subnet, _ = ip.next_subnet()
        edge_side = str(_hosts(subnet)[0])
        core_side = str(_hosts(subnet)[1])
        edges.append({
            "id": f"e-{edge_id}-{core_id}",
            "source": edge_id, "target": core_id,
            "label": "iBGP", "protocol": "bgp",
            "config": {"ip": f"{edge_side}/30"},
        })

        # Firewall at POP edge
        fw_id = f"fw-{pop_name.lower().replace('_','-')}"
        fw_ip = ip.next_host()
        fw_vendor = ("Fortinet", "FortiGate 600F", "fortios") if pop_idx == 0 else ("Palo Alto", "PA-3220", "panos")
        fw = {
            "id": fw_id,
            "label": f"FW-{pop_name.split('-')[-1]}",
            "type": "firewall",
            "config": {
                "vendor": fw_vendor[0], "model": fw_vendor[1], "os": fw_vendor[2],
                "hostname": fw_id, "ip": f"{fw_ip}/32",
            },
        }
        nodes.append(fw)
        subnet, _ = ip.next_subnet()
        core_side = str(_hosts(subnet)[0])
        edges.append({
            "id": f"e-{core_id}-{fw_id}",
            "source": core_id, "target": fw_id,
            "label": "inside", "protocol": "static",
            "config": {"ip": f"{core_side}/30"},
        })

    # Inter-POP link
    edge_ash = next(n for n in nodes if n["id"].startswith("edge-wam-pop-ash"))
    edge_dal = next(n for n in nodes if n["id"].startswith("edge-wam-pop-dal"))
    subnet, _ = ip.next_subnet()
    ash_side = str(_hosts(subnet)[0])
    edges.append({
        "id": "e-ash-dal-backbone",
        "source": edge_ash["id"], "target": edge_dal["id"],
        "label": "backbone", "protocol": "bgp",
        "config": {"ip": f"{ash_side}/30"},
    })

    return {
        "id": topo_id,
        "name": f"WAM-BGP-{seed}",
        "description": f"WAM dual-homed BGP topology (seed={seed})",
        "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
        "nodes": nodes, "edges": edges,
        "type": "wam",
        "site_names": ["WAM-POP-ASH", "WAM-POP-DAL"],
    }


def _build_man_topology(seed: int) -> dict:
    """MAN: MPLS L3VPN core with PE-CE at 2 aggregation points."""
    random.seed(seed + 1)
    topo_id = f"topo-man-{seed}"
    ip = _IPAlloc("10.1.0.0/16")
    nodes: list[dict] = []
    edges: list[dict] = []

    # MPLS core: P routers + PE routers at each agg site
    for agg_idx, agg_name in enumerate(["MAN-AGG-DEN", "MAN-AGG-CHI"]):
        # PE router
        pe_id = f"pe-{agg_name.lower().replace('_','-')}"
        pe_ip = ip.next_host()
        pe = {
            "id": pe_id,
            "label": f"PE-{agg_name.split('-')[-1]}",
            "type": "router",
            "config": {
                "vendor": "Cisco", "model": "ASR 1001-X", "os": "ios_xr",
                "hostname": pe_id, "ip": f"{pe_ip}/32",
                "asn": 65010 + agg_idx, "ospf_area": 0,
            },
        }
        nodes.append(pe)

        # CE router (customer edge)
        ce_id = f"ce-{agg_name.lower().replace('_','-')}"
        ce_ip = ip.next_host()
        ce = {
            "id": ce_id,
            "label": f"CE-{agg_name.split('-')[-1]}",
            "type": "router",
            "config": {
                "vendor": "Juniper", "model": "MX204", "os": "junos",
                "hostname": ce_id, "ip": f"{ce_ip}/32",
                "asn": 65020 + agg_idx,
            },
        }
        nodes.append(ce)
        subnet, _ = ip.next_subnet()
        pe_side = str(_hosts(subnet)[0])
        edges.append({
            "id": f"e-{pe_id}-{ce_id}",
            "source": pe_id, "target": ce_id,
            "label": "PE-CE", "protocol": "bgp",
            "config": {"ip": f"{pe_side}/30", "vrf": f"VRF-{agg_idx+1}"},
        })

    # P router (core)
    p_id = "p-core-man"
    p_ip = ip.next_host()
    p = {
        "id": p_id,
        "label": "P-CORE",
        "type": "p-router",
        "config": {
            "vendor": "Cisco", "model": "ASR 1001-X", "os": "ios_xr",
            "hostname": p_id, "ip": f"{p_ip}/32", "ospf_area": 0,
        },
    }
    nodes.append(p)

    # Connect PEs to P
    pe_ids = [n["id"] for n in nodes if n["id"].startswith("pe-")]
    for pe_id in pe_ids:
        subnet, _ = ip.next_subnet()
        pe_side = str(_hosts(subnet)[0])
        edges.append({
            "id": f"e-{pe_id}-{p_id}",
            "source": pe_id, "target": p_id,
            "label": "MPLS", "protocol": "ldp",
            "config": {"ip": f"{pe_side}/30"},
        })

    return {
        "id": topo_id,
        "name": f"MAN-MPLS-{seed}",
        "description": f"MAN MPLS L3VPN core (seed={seed})",
        "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
        "nodes": nodes, "edges": edges,
        "type": "man",
        "site_names": ["MAN-AGG-DEN", "MAN-AGG-CHI"],
    }


def _build_lan_topology(seed: int) -> dict:
    """LAN: Campus 3-tier with core, distribution, access."""
    random.seed(seed + 2)
    topo_id = f"topo-lan-{seed}"
    ip = _IPAlloc("10.2.0.0/16")
    nodes: list[dict] = []
    edges: list[dict] = []

    campus = "LAN-HQ-VA"
    vlan_data = 110
    vlan_voice = 120
    vlan_guest = 130
    vlan_mgmt = 100

    # Core
    core_id = f"core-{campus.lower().replace('_','-')}"
    core_ip = ip.next_host()
    core = {
        "id": core_id,
        "label": "CORE-1",
        "type": "switch-l3",
        "config": {
            "vendor": "Cisco", "model": "Catalyst 9300-48P", "os": "ios_switch",
            "hostname": core_id, "ip": f"{core_ip}/32",
            "ospf_area": 1, "vrf": "GUEST",
        },
    }
    nodes.append(core)

    # Distribution switches
    for dist_idx in range(2):
        dist_id = f"dist-{campus.lower().replace('_','-')}-{dist_idx+1}"
        dist_ip = ip.next_host()
        vendor = random.choice([("Cisco", "Catalyst 9300-48P", "ios_switch"), ("Arista", "7050X3-32S", "eos")])
        dist = {
            "id": dist_id,
            "label": f"DIST-{dist_idx+1}",
            "type": "switch-l3",
            "config": {
                "vendor": vendor[0], "model": vendor[1], "os": vendor[2],
                "hostname": dist_id, "ip": f"{dist_ip}/32",
                "ospf_area": 1,
            },
        }
        nodes.append(dist)
        edges.append({
            "id": f"e-{core_id}-{dist_id}",
            "source": core_id, "target": dist_id,
            "label": "trunk", "protocol": "ospf",
            "config": {"trunk": True, "vlan": vlan_mgmt, "allowed_vlans": f"{vlan_mgmt},{vlan_data},{vlan_voice},{vlan_guest}"},
        })

    # Access switches + firewall
    for dist_idx in range(2):
        dist_id = f"dist-{campus.lower().replace('_','-')}-{dist_idx+1}"
        for acc_idx in range(2):
            acc_id = f"acc-{campus.lower().replace('_','-')}-{dist_idx+1}-{acc_idx+1}"
            acc_ip = ip.next_host()
            vendor = random.choice([("Cisco", "Catalyst 9300-48P", "ios_switch"), ("Juniper", "EX4650-48Y", "junos")])
            acc = {
                "id": acc_id,
                "label": f"ACC-{dist_idx+1}-{acc_idx+1}",
                "type": "switch-l2",
                "config": {
                    "vendor": vendor[0], "model": vendor[1], "os": vendor[2],
                    "hostname": acc_id, "ip": f"{acc_ip}/32",
                },
            }
            nodes.append(acc)
            edges.append({
                "id": f"e-{dist_id}-{acc_id}",
                "source": dist_id, "target": acc_id,
                "label": "access", "protocol": "dot1x",
                "config": {"trunk": True, "vlan": vlan_data, "allowed_vlans": f"{vlan_data},{vlan_voice},{vlan_guest}"},
            })

    # Firewall at campus edge
    fw_id = f"fw-{campus.lower().replace('_','-')}"
    fw_ip = ip.next_host()
    fw = {
        "id": fw_id,
        "label": "FW-HQ",
        "type": "firewall",
        "config": {
            "vendor": "Fortinet", "model": "FortiGate 600F", "os": "fortios",
            "hostname": fw_id, "ip": f"{fw_ip}/32",
        },
    }
    nodes.append(fw)
    subnet, _ = ip.next_subnet()
    core_side = str(_hosts(subnet)[0])
    edges.append({
        "id": f"e-{core_id}-{fw_id}",
        "source": core_id, "target": fw_id,
        "label": "edge", "protocol": "static",
        "config": {"ip": f"{core_side}/30"},
    })

    return {
        "id": topo_id,
        "name": f"LAN-CAMPUS-{seed}",
        "description": f"LAN campus 3-tier topology (seed={seed})",
        "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
        "nodes": nodes, "edges": edges,
        "type": "lan",
        "site_names": [campus],
    }


def _build_dcam_topology(seed: int) -> dict:
    """DCAM: Spine-leaf with BGP-EVPN, VXLAN, multi-tenant VRFs."""
    random.seed(seed + 3)
    topo_id = f"topo-dcam-{seed}"
    ip = _IPAlloc("10.3.0.0/16")
    nodes: list[dict] = []
    edges: list[dict] = []

    site = "DCAM-EAST"
    spines = []
    leaves = []

    # Spine routers
    for s_idx in range(2):
        spine_id = f"spine-{s_idx+1}-{site.lower().replace('_','-')}"
        spine_ip = ip.next_host()
        vendor = random.choice([("Arista", "7280R3-48S6", "eos"), ("Cisco", "Nexus 93180YC-EX", "ios_switch")])
        spine = {
            "id": spine_id,
            "label": f"SPINE-{s_idx+1}",
            "type": "switch-l3",
            "config": {
                "vendor": vendor[0], "model": vendor[1], "os": vendor[2],
                "hostname": spine_id, "ip": f"{spine_ip}/32",
                "ospf_area": 0,
            },
        }
        nodes.append(spine)
        spines.append(spine)

    # Leaf switches
    for l_idx in range(4):
        leaf_id = f"leaf-{l_idx+1}-{site.lower().replace('_','-')}"
        leaf_ip = ip.next_host()
        vendor = random.choice([("Arista", "7050X3-32S", "eos"), ("Juniper", "EX4650-48Y", "junos"), ("Cisco", "Nexus 93180YC-EX", "ios_switch")])
        tenant_vrf = f"TENANT-{((l_idx % 2) + 1)}"
        leaf = {
            "id": leaf_id,
            "label": f"LEAF-{l_idx+1}",
            "type": "switch-l3",
            "config": {
                "vendor": vendor[0], "model": vendor[1], "os": vendor[2],
                "hostname": leaf_id, "ip": f"{leaf_ip}/32",
                "ospf_area": 0, "vrf": tenant_vrf,
            },
        }
        nodes.append(leaf)
        leaves.append(leaf)

        # Connect leaf to each spine
        for spine in spines:
            subnet, _ = ip.next_subnet()
            spine_side = str(_hosts(subnet)[0])
            edges.append({
                "id": f"e-{spine['id']}-{leaf_id}",
                "source": spine["id"], "target": leaf_id,
                "label": "fabric", "protocol": "bgp-evpn",
                "config": {"ip": f"{spine_side}/30"},
            })

    # Border leaf with firewall for external connectivity
    border_id = f"border-1-{site.lower().replace('_','-')}"
    border_ip = ip.next_host()
    border = {
        "id": border_id,
        "label": "BORDER-1",
        "type": "switch-l3",
        "config": {
            "vendor": "Arista", "model": "7280R3-48S6", "os": "eos",
            "hostname": border_id, "ip": f"{border_ip}/32",
            "ospf_area": 0,
        },
    }
    nodes.append(border)
    for spine in spines:
        subnet, _ = ip.next_subnet()
        spine_side = str(_hosts(subnet)[0])
        border_side = str(_hosts(subnet)[1])
        edges.append({
            "id": f"e-{spine['id']}-{border_id}",
            "source": spine["id"], "target": border_id,
            "label": "fabric", "protocol": "bgp-evpn",
            "config": {"ip": f"{spine_side}/30"},
        })

    # Firewall attached to border
    fw_id = f"fw-dcam-{site.lower().replace('_','-')}"
    fw_ip = ip.next_host()
    fw = {
        "id": fw_id,
        "label": "FW-DCAM",
        "type": "firewall",
        "config": {
            "vendor": "Palo Alto", "model": "PA-5250", "os": "panos",
            "hostname": fw_id, "ip": f"{fw_ip}/32",
        },
    }
    nodes.append(fw)
    subnet, _ = ip.next_subnet()
    border_side = str(_hosts(subnet)[0])
    edges.append({
        "id": f"e-{border_id}-{fw_id}",
        "source": border_id, "target": fw_id,
        "label": "edge", "protocol": "static",
        "config": {"ip": f"{border_side}/30"},
    })

    return {
        "id": topo_id,
        "name": f"DCAM-SPINE-LEAF-{seed}",
        "description": f"DCAM spine-leaf BGP-EVPN topology (seed={seed})",
        "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
        "nodes": nodes, "edges": edges,
        "type": "dcam",
        "site_names": [site],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Circuit builder
# ══════════════════════════════════════════════════════════════════════════════

_CARRIERS = ["ATT", "Lumen", "Verizon", "Zayo", "Cogent"]
_CIRCUIT_TYPES = ["MPLS", "DIA", "WAVE", "Ethernet", "SD-WAN"]


def _build_circuits(topology: dict, seed: int) -> list[dict]:
    """Build nc_circuits rows for a topology."""
    random.seed(seed + 500)
    circuits: list[dict] = []
    edges = topology.get("edges", [])

    for idx, edge in enumerate(edges):
        if edge.get("protocol") in ("bgp", "bgp-evpn"):
            carrier = random.choice(_CARRIERS)
            bw = random.choice(["1G", "10G", "100G", "400G"])
            c_type = random.choice(_CIRCUIT_TYPES)
            start = (_NOW - timedelta(days=random.randint(30, 1000))).strftime("%Y-%m-%d")
            end = (_NOW + timedelta(days=random.randint(365, 1095))).strftime("%Y-%m-%d")
            circuits.append({
                "id": f"circ-{topology['id']}-{idx}",
                "topology_id": topology["id"],
                "circuit_id": f"{carrier}-{random.randint(10000,99999)}",
                "carrier": carrier,
                "circuit_type": c_type,
                "bandwidth": bw,
                "handoff_a": edge.get("source", ""),
                "handoff_z": edge.get("target", ""),
                "customer": _CUSTOMER_ID,
                "site": topology["site_names"][0] if topology.get("site_names") else "",
                "monthly_cost_usd": round(random.uniform(500, 15000), 2),
                "contract_start": start,
                "contract_end": end,
                "sla_uptime_pct": round(random.uniform(99.5, 99.999), 3),
            })
    return circuits


# ══════════════════════════════════════════════════════════════════════════════
# DB helpers
# ══════════════════════════════════════════════════════════════════════════════


def _get_conn():
    try:
        from tools.network.db.init_db import get_connection
        return get_connection()
    except Exception:
        import sqlite3
        db = _ROOT / "data" / "network_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn


def _insert_hardware_profiles(conn, reset: bool) -> int:
    if reset:
        conn.execute("DELETE FROM nc_hardware_profiles")
    cols = list(_HARDWARE_PROFILES[0].keys())
    placeholders = ",".join("?" * len(cols))
    col_names = ",".join(cols)
    rows = []
    for hp in _HARDWARE_PROFILES:
        row = [hp.get(c) for c in cols]
        # Add synthetic id
        row.insert(0, f"hp-{hp['vendor'].lower().replace(' ','-')}-{hp['model'].lower().replace(' ','-')}")
    # Rebuild with id
    actual_cols = ["id"] + cols
    placeholders = ",".join("?" * len(actual_cols))
    col_names = ",".join(actual_cols)
    rows = []
    for hp in _HARDWARE_PROFILES:
        hp_id = f"hp-{hp['vendor'].lower().replace(' ','-')}-{hp['model'].lower().replace(' ','-')}"
        rows.append([hp_id] + [hp.get(c) for c in cols])
    conn.executemany(f"INSERT OR IGNORE INTO nc_hardware_profiles ({col_names}) VALUES ({placeholders})", rows)
    return len(rows)


def _insert_sites(conn, reset: bool) -> int:
    if reset:
        conn.execute("DELETE FROM nc_sites")
    cols = ["id", "customer_id", "name", "address", "city", "state", "country", "site_type"]
    placeholders = ",".join("?" * len(cols))
    rows = []
    for s in _SITES:
        rows.append([
            f"site-{s['name'].lower().replace('_','-')}",
            _CUSTOMER_ID,
            s["name"],
            s["address"],
            s["city"],
            s["state"],
            "US",
            s["site_type"],
        ])
    conn.executemany(f"INSERT OR IGNORE INTO nc_sites ({','.join(cols)}) VALUES ({placeholders})", rows)
    return len(rows)


def _insert_topologies(conn, topologies: list[dict], reset: bool) -> int:
    if reset:
        conn.execute("DELETE FROM topologies WHERE id LIKE 'topo-%'")
    rows = []
    for t in topologies:
        rows.append((
            t["id"], t["name"], t["description"],
            t["graph_json"], None, "CUI",
            _NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            _NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO topologies (id,name,description,graph_json,template_id,classification,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        rows,
    )
    return len(rows)


def _insert_devices(conn, topologies: list[dict], reset: bool) -> int:
    if reset:
        conn.execute("DELETE FROM ni_devices WHERE topology_id LIKE 'topo-%'")
    rows = []
    for topo in topologies:
        for node in topo.get("nodes", []):
            ntype = node.get("type", "")
            if ntype in {"pe-router", "p-router"}:
                ntype = "router"
            cfg = node.get("config", {})
            node_id = node["id"]
            label = node.get("label", node_id)
            vendor = cfg.get("vendor", "Unknown")
            model = cfg.get("model", "")
            site = topo["site_names"][0] if topo.get("site_names") else ""
            # Pick a firmware version based on vendor
            fw_map = {
                "Cisco": random.choice(["16.12.5", "17.6.4", "15.7(3)M8"]),
                "Juniper": random.choice(["21.4R3", "22.2R2", "20.4R3"]),
                "Arista": random.choice(["4.29.2F", "4.28.3M", "4.30.1F"]),
                "Palo Alto": random.choice(["10.2.3", "11.0.1", "10.1.8"]),
                "Fortinet": random.choice(["7.2.4", "7.0.12", "6.4.13"]),
            }
            fw = fw_map.get(vendor, "1.0")
            # EOL/EOS based on model from hardware profiles
            hp = next((h for h in _HARDWARE_PROFILES if h["vendor"] == vendor and h["model"] == model), None)
            eol = hp["eol_date"] if hp else (_NOW + timedelta(days=365*3)).strftime("%Y-%m-%d")
            eos = hp["eos_date"] if hp else (_NOW + timedelta(days=365*5)).strftime("%Y-%m-%d")
            cost = hp["replacement_cost"] if hp else round(random.uniform(5000, 50000), 2)
            crit = round(random.uniform(3.0, 9.8), 1)

            rows.append((
                f"dev-{node_id}",
                topo["id"],
                node_id,
                label,
                ntype,
                vendor,
                model,
                fw,
                eol,
                eos,
                (_NOW - timedelta(days=random.randint(200, 1500))).strftime("%Y-%m-%d"),
                round(cost * 0.7, 2),
                round(cost * 0.12, 2),
                cost,
                site,
                f"Rack-{random.randint(1,20)}",
                crit,
                random.randint(0, 10),
                "",
                json.dumps(cfg),
            ))
    conn.executemany(
        """INSERT OR REPLACE INTO ni_devices
        (id,topology_id,node_id,label,device_type,vendor,model,firmware_version,eol_date,eos_date,
         purchase_date,purchase_cost,annual_maintenance_cost,replacement_cost,site,rack_location,
         criticality_score,downstream_count,notes,properties_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        rows,
    )
    return len(rows)


def _insert_circuits(conn, circuits: list[dict], reset: bool) -> int:
    if reset:
        conn.execute("DELETE FROM nc_circuits WHERE topology_id LIKE 'topo-%'")
    rows = []
    for c in circuits:
        rows.append((
            c["id"], c["topology_id"], c["circuit_id"], c["carrier"],
            c["circuit_type"], c["bandwidth"], c["handoff_a"], c["handoff_z"],
            c["customer"], c["site"], c["monthly_cost_usd"],
            c["contract_start"], c["contract_end"], c["sla_uptime_pct"],
            "installed",
            _NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO nc_circuits
        (id,topology_id,circuit_id,carrier,circuit_type,bandwidth,handoff_a,handoff_z,
         customer,site,monthly_cost_usd,contract_start,contract_end,sla_uptime_pct,
         install_status,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        rows,
    )
    return len(rows)


def _insert_configs(conn, topologies: list[dict]) -> int:
    """Generate and insert device configs via config_generator.py."""
    try:
        from tools.network.config_generator import generate_device_configs
    except ImportError as exc:
        print(f"[WARN] Cannot import config_generator: {exc}")
        return 0

    rows = []
    for topo in topologies:
        graph = {"nodes": topo.get("nodes", []), "edges": topo.get("edges", [])}
        try:
            configs = generate_device_configs(graph, topo["name"])
        except Exception as exc:
            print(f"[WARN] Config generation failed for {topo['name']}: {exc}")
            continue
        for filename, config_text in configs.items():
            # Map filename back to a device id heuristically
            hostname = filename.split("_")[0]
            dev_id = None
            for node in topo.get("nodes", []):
                cfg = node.get("config", {})
                if cfg.get("hostname") == hostname or node.get("label", "").replace("-", "").lower() == hostname.replace("-", "").lower():
                    dev_id = f"dev-{node['id']}"
                    break
            if not dev_id:
                dev_id = f"dev-{hostname}"
            config_hash = hashlib.sha256(config_text.encode()).hexdigest()[:16]
            rows.append((
                f"cfg-{dev_id}-{topo['id']}",
                dev_id,
                "running",
                config_text,
                config_hash,
                "synthetic_generator",
                1,
            ))

    if rows:
        conn.executemany(
            """INSERT OR REPLACE INTO ni_device_configs
            (id,device_id,config_type,config_text,config_hash,source,version)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            rows,
        )
    return len(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════


def generate(reset: bool = False, seed: int = 42) -> dict:
    random.seed(seed)
    conn = _get_conn()
    counts: dict[str, int] = {}
    try:
        # Ensure schema exists
        from tools.network.db.init_db import init_db
        init_db()
    except Exception:
        pass

    try:
        # Insert reference data
        counts["nc_hardware_profiles"] = _insert_hardware_profiles(conn, reset)
        counts["nc_sites"] = _insert_sites(conn, reset)

        # Build topologies
        topologies = [
            _build_wam_topology(seed),
            _build_man_topology(seed),
            _build_lan_topology(seed),
            _build_dcam_topology(seed),
        ]

        counts["topologies"] = _insert_topologies(conn, topologies, reset)
        counts["ni_devices"] = _insert_devices(conn, topologies, reset)

        circuits: list[dict] = []
        for topo in topologies:
            circuits.extend(_build_circuits(topo, seed))
        counts["nc_circuits"] = _insert_circuits(conn, circuits, reset)

        counts["ni_device_configs"] = _insert_configs(conn, topologies)

        conn.commit()
    finally:
        conn.close()

    total = sum(v for v in counts.values() if isinstance(v, int))
    return {
        "status": "ok",
        "counts": counts,
        "total": total,
        "seed": seed,
        "topologies": [t["name"] for t in topologies],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic Multi-Vendor Network Data Generator")
    parser.add_argument("--reset", action="store_true", help="Clear existing synthetic data before insert")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = generate(args.reset, args.seed)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Generated {result['total']} synthetic records")
        for t, c in result["counts"].items():
            print(f"  {t:35s}: {c:4d} rows")
        print(f"Topologies: {', '.join(result['topologies'])}")


if __name__ == "__main__":
    main()
