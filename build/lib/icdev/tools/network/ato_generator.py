# [CUI // SP-CTI]
"""ICDEV Network Design Canvas -- ATO Package Auto-Generator.

Pure functions for generating partial ATO package artifacts from network
topology data.  All functions are deterministic (no LLM, no Flask dependency).

Artifacts generated:
  1. System Boundary Diagram (RMF Step 1) -- SVG markup
  2. Data Flow Diagram -- structured JSON with flow classification
  3. Ports / Protocols / Services (PPS) Matrix -- tabular data
  4. Pre-populated STIG Checklist -- generated from topology objects

Each function takes a graph dict (nodes/edges) plus optional metadata and
returns a serialisable dict.
"""

from __future__ import annotations

import hashlib
import uuid as _uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from tools.network.compliance import (
    ENCRYPTOR_RATINGS,
    run_compliance_audit,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ENCRYPTOR_TYPES = frozenset(
    {
        "fips-140-l1",
        "fips-140-l2",
        "fips-140-l3",
        "fips-140-l4",
        "hsm",
        "type1-encryptor",
        "kg-175d",
        "kg-175g",
        "kg-250",
        "kg-340",
        "kg-245x",
        "kg-255",
        "macsec",
    }
)

_FIREWALL_TYPES = frozenset(
    {
        "firewall",
        "aws-nfw",
        "az-fw",
        "gcp-armor",
        "oci-waf",
        "aws-waf",
    }
)

_WAN_TYPES = frozenset(
    {
        "cloud",
        "aws-dx",
        "az-er",
        "gcp-ic",
        "oci-fc",
        "ibm-dl",
        "aws-vpn",
        "az-vpn-gw",
        "gcp-vpn",
        "ibm-vpn",
        "sdwan-overlay",
        "internet-exchange",
        "aws-dx-gw",
        "az-er-global",
    }
)

_CLOUD_PREFIXES = ("aws-", "az-", "gcp-", "oci-", "ibm-")

_DX_TYPES = frozenset(
    {
        "aws-dx",
        "aws-dx-gw",
        "az-er",
        "az-er-global",
        "gcp-ic",
        "oci-fc",
        "ibm-dl",
    }
)

_VPN_TYPES = frozenset(
    {
        "aws-vpn",
        "az-vpn-gw",
        "gcp-vpn",
        "ibm-vpn",
    }
)

# Map node type -> human-readable category for boundary diagram zones
_ZONE_MAP: dict[str, str] = {
    "firewall": "Perimeter / DMZ",
    "aws-nfw": "Perimeter / DMZ",
    "az-fw": "Perimeter / DMZ",
    "gcp-armor": "Perimeter / DMZ",
    "oci-waf": "Perimeter / DMZ",
    "aws-waf": "Perimeter / DMZ",
    "router": "Core Network",
    "switch-l3": "Distribution",
    "switch-l2": "Access Layer",
    "wap": "Wireless Access",
    "server": "Server Enclave",
    "siem": "Management / NOC",
    "network-tap": "Management / NOC",
    "wlc": "Management / NOC",
    "endpoint-pc": "User Enclave",
    "endpoint-phone": "User Enclave",
    "endpoint-iot": "IoT Segment",
    "endpoint-camera": "IoT Segment",
    # Cloud extended types
    "aws-dx-gw": "Cloud Interconnect",
    "aws-privatelink": "Private Service Endpoint",
    "aws-tgw": "Core Network",
    "aws-shield": "Edge Protection",
    "az-er-global": "Cloud Interconnect",
    "az-privatelink": "Private Service Endpoint",
    "az-vwan": "Core Network",
    "az-frontdoor": "Edge Protection",
    "gcp-ic": "Cloud Interconnect",
    "gcp-psc": "Private Service Endpoint",
    "gcp-ncc": "Core Network",
    "gcp-cloud-armor": "Edge Protection",
    "oci-fc": "Cloud Interconnect",
    "oci-spe": "Private Service Endpoint",
    "oci-drg": "Core Network",
    "oci-waas": "Edge Protection",
}

# Protocol -> well-known port/service mapping
_PROTOCOL_PPS: dict[str, dict[str, str]] = {
    "ospf": {"port": "89/IP", "service": "OSPF Routing", "direction": "Internal"},
    "bgp": {"port": "179/TCP", "service": "BGP Routing", "direction": "Internal/External"},
    "bgp evpn": {"port": "179/TCP", "service": "BGP EVPN Overlay", "direction": "Internal"},
    "ipsec": {"port": "500,4500/UDP + ESP/IP", "service": "IPSec VPN", "direction": "Bidirectional"},
    "ipsec esp": {"port": "500,4500/UDP + 50/IP", "service": "IPSec ESP", "direction": "Bidirectional"},
    "gre/ipsec": {"port": "47/IP + 500,4500/UDP", "service": "GRE over IPSec", "direction": "Bidirectional"},
    "tls": {"port": "443/TCP", "service": "TLS Encrypted", "direction": "Bidirectional"},
    "https": {"port": "443/TCP", "service": "HTTPS Management", "direction": "Inbound"},
    "ssh": {"port": "22/TCP", "service": "SSH Management", "direction": "Inbound"},
    "snmpv3": {"port": "161-162/UDP", "service": "SNMP Monitoring", "direction": "Internal"},
    "snmpv1": {"port": "161-162/UDP", "service": "SNMPv1 (INSECURE)", "direction": "Internal"},
    "snmpv2": {"port": "161-162/UDP", "service": "SNMPv2 (INSECURE)", "direction": "Internal"},
    "telnet": {"port": "23/TCP", "service": "Telnet (INSECURE)", "direction": "Inbound"},
    "http": {"port": "80/TCP", "service": "HTTP (INSECURE)", "direction": "Inbound"},
    "macsec": {"port": "L2/802.1AE", "service": "MACsec Link Encryption", "direction": "Point-to-Point"},
    "vxlan": {"port": "4789/UDP", "service": "VXLAN Overlay", "direction": "Internal"},
    "mpls": {"port": "Label/L2.5", "service": "MPLS Transport", "direction": "Internal"},
    "dns": {"port": "53/TCP+UDP", "service": "DNS Resolution", "direction": "Bidirectional"},
    "ntp": {"port": "123/UDP", "service": "NTP Time Sync", "direction": "Outbound"},
    "syslog": {"port": "514/UDP or 6514/TCP", "service": "Syslog Logging", "direction": "Internal"},
    "radius": {"port": "1812-1813/UDP", "service": "RADIUS Auth", "direction": "Internal"},
    "tacacs": {"port": "49/TCP", "service": "TACACS+ Auth", "direction": "Internal"},
    "rdma": {"port": "4791/UDP", "service": "RoCEv2 RDMA", "direction": "Internal"},
    "bfd": {"port": "3784/UDP", "service": "BFD (single-hop)", "direction": "bidirectional"},
    "bfd-multi": {"port": "4784/UDP", "service": "BFD (multi-hop)", "direction": "bidirectional"},
    "vrrp": {"port": "112/IP", "service": "VRRP", "direction": "bidirectional"},
    "hsrp": {"port": "1985/UDP", "service": "HSRP", "direction": "bidirectional"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# STIG helpers (extracted from generate_stig_checklist)
# ---------------------------------------------------------------------------

def _compute_stig_facts(nodes, edges, node_types, adj):
    """Pre-compute topology characteristics for STIG evaluation."""
    type_set = set(node_types.values())
    has_firewall = bool(_FIREWALL_TYPES & type_set)
    firewall_count = sum(1 for t in type_set if t in _FIREWALL_TYPES)
    wan_nodes = [n for n in nodes if node_types.get(n["id"]) in _WAN_TYPES]
    core_types = {"router", "switch-l3", "mpls-pe", "mpls-p", "route-reflector"}
    dns_types = {"aws-r53", "az-dns", "gcp-dns"}
    cloud_types = {t for t in type_set if t.startswith(_CLOUD_PREFIXES)}
    has_oob = any(
        "oob" in (n.get("label") or "").lower()
        or "management" in (n.get("label") or "").lower()
        or n.get("type") in ("siem", "network-tap", "wlc")
        for n in nodes
    )
    insecure_protos = [e for e in edges if (e.get("protocol") or "").lower() in ("telnet", "http", "snmpv1", "snmpv2")]
    encrypted_wan = (
        all(
            any(node_types.get(nb) in _ENCRYPTOR_TYPES for nb in adj.get(wn["id"], set()))
            or any(
                (e.get("protocol") or "").lower() in ("ipsec", "ipsec esp", "macsec", "tls", "gre/ipsec")
                for e in edges
                if wn["id"] in (e["source"], e["target"])
            )
            for wn in wan_nodes
        )
        if wan_nodes else True
    )
    fips_on_encrypted = (
        all(
            any(node_types.get(nid) in _ENCRYPTOR_TYPES for nid in (e["source"], e["target"]))
            for e in edges
            if (e.get("protocol") or "").lower() in ("ipsec", "ipsec esp", "macsec", "tls", "gre/ipsec")
        )
        if edges else True
    )
    core_redundant = all(
        len(adj.get(n["id"], set())) >= 2
        for n in nodes
        if node_types.get(n["id"]) in core_types or "core" in (n.get("label") or "").lower()
    )
    dns_count = sum(1 for n in nodes if node_types.get(n["id"]) in dns_types)
    unlabeled = [n for n in nodes if not n.get("label") or n.get("label", "").startswith("Untitled")]
    has_type1 = bool({"type1-encryptor", "kg-175d", "kg-175g", "kg-250", "kg-340", "kg-245x", "kg-255"} & type_set)
    encryptors_match = True
    for n in nodes:
        if node_types.get(n["id"], "") in _ENCRYPTOR_TYPES:
            max_rating = ENCRYPTOR_RATINGS.get(node_types[n["id"]], 0)
            for e in edges:
                if n["id"] in (e["source"], e["target"]):
                    label = (e.get("label") or "").upper()
                    link_mbps = 1000
                    if "400G" in label:
                        link_mbps = 400000
                    elif "100G" in label:
                        link_mbps = 100000
                    elif "40G" in label:
                        link_mbps = 40000
                    elif "10G" in label:
                        link_mbps = 10000
                    if link_mbps > max_rating > 0:
                        encryptors_match = False
    return {
        "has_firewall": has_firewall, "firewall_count": firewall_count,
        "wan_nodes": wan_nodes, "core_types": core_types, "dns_types": dns_types,
        "cloud_types": cloud_types, "has_oob": has_oob,
        "insecure_protos": insecure_protos, "encrypted_wan": encrypted_wan,
        "fips_on_encrypted": fips_on_encrypted, "core_redundant": core_redundant,
        "dns_count": dns_count, "unlabeled": unlabeled,
        "has_type1": has_type1, "encryptors_match": encryptors_match,
    }


def _evaluate_stig_check(check, item, nodes, node_types, facts, classification, has_as_built_version):
    """Evaluate a single named STIG check; return (status, comments)."""
    has_firewall = facts["has_firewall"]
    firewall_count = facts["firewall_count"]
    core_types = facts["core_types"]

    if check == "mgmt_encrypted":
        if facts["insecure_protos"]:
            protos = ", ".join(set((e.get("protocol") or "").upper() for e in facts["insecure_protos"]))
            return "Open", f"Insecure management protocols detected: {protos}"
        return "NotAFinding", "No insecure management protocols (Telnet/HTTP/SNMPv1/v2) found in topology."

    if check == "firewall_at_boundary":
        if has_firewall:
            return "NotAFinding", f"{firewall_count} firewall device(s) present in topology."
        return "Open", "No firewall device found at network boundary."

    if check == "fips_crypto":
        if facts["fips_on_encrypted"]:
            return "NotAFinding", "All encrypted links have FIPS-validated crypto devices."
        return "Open", "Encrypted links found without FIPS 140-2/3 validated crypto modules."

    if check == "core_redundancy":
        core_count = sum(1 for n in nodes if node_types.get(n["id"]) in core_types)
        if core_count == 0:
            return "Not_Applicable", "No core/distribution devices in topology."
        if facts["core_redundant"]:
            return "NotAFinding", "All core/distribution devices have 2+ uplinks."
        return "Open", "One or more core/distribution devices have only a single uplink."

    if check == "oob_mgmt":
        if facts["has_oob"]:
            return "NotAFinding", "Out-of-band management network or management device detected."
        return "Open", "No OOB management network detected in topology."

    if check == "dns_redundancy":
        dns_count = facts["dns_count"]
        if dns_count >= 2:
            return "NotAFinding", f"{dns_count} DNS services found."
        if dns_count == 0 and len(nodes) <= 3:
            return "Not_Applicable", "Small topology -- DNS redundancy check not applicable."
        return "Open", f"Only {dns_count} DNS service(s) found; need at least 2."

    if check == "wan_encrypted":
        if not facts["wan_nodes"]:
            return "Not_Applicable", "No WAN links in topology."
        if facts["encrypted_wan"]:
            return "NotAFinding", "All WAN links have encryption (IPSec/MACsec/Type 1)."
        return "Open", "One or more WAN links lack encryption."

    if check == "cloud_isolated":
        if not facts["cloud_types"]:
            return "Not_Applicable", "No cloud resources in topology."
        if has_firewall:
            return "NotAFinding", "Cloud VPCs have firewall separation from on-premises."
        return "Open", "Cloud resources present but no firewall isolating from on-prem."

    if check == "segmentation":
        if len(nodes) <= 5:
            return "Not_Applicable", "Small topology -- segmentation check not applicable."
        if firewall_count >= 2:
            return "NotAFinding", f"{firewall_count} firewalls provide network segmentation."
        if firewall_count == 1:
            return "Open", "Only 1 firewall -- insufficient for east-west segmentation."
        return "Open", "No firewalls for internal segmentation."

    if check == "devices_labeled":
        unlabeled = facts["unlabeled"]
        if not unlabeled:
            return "NotAFinding", "All devices have descriptive labels/hostnames."
        return "Open", f"{len(unlabeled)} device(s) missing descriptive hostnames."

    if check == "as_built_exists":
        if has_as_built_version:
            return "NotAFinding", "As-built version saved in topology history."
        return "Open", "No as-built version found -- save current topology as 'As-Built'."

    if check == "type1_for_secret":
        if classification not in ("SECRET", "TOP SECRET"):
            return "Not_Applicable", f"Classification is {classification} -- Type 1 not required."
        if facts["has_type1"]:
            return "NotAFinding", "NSA Type 1 encryptors present for SECRET+ links."
        return "Open", "SECRET+ classification requires NSA Type 1 encryption."

    if check == "encryptor_speed":
        enc_count = sum(1 for n in nodes if node_types.get(n["id"]) in _ENCRYPTOR_TYPES)
        if enc_count == 0:
            return "Not_Applicable", "No encryption devices in topology."
        if facts["encryptors_match"]:
            return "NotAFinding", "All encryptor throughput ratings match link bandwidths."
        return "Open", "One or more encryptors are under-rated for their link bandwidth."

    return "Not_Reviewed", ""


def _classify_node_zone(node: dict) -> str:
    """Return the security zone name for a node based on its type."""
    ntype = node.get("type", "")
    if ntype in _ZONE_MAP:
        return _ZONE_MAP[ntype]
    if ntype.startswith(_CLOUD_PREFIXES):
        # Determine CSP
        for pfx in _CLOUD_PREFIXES:
            if ntype.startswith(pfx):
                return f"Cloud ({pfx.rstrip('-').upper()})"
        return "Cloud"
    if ntype in _WAN_TYPES:
        return "WAN / External"
    if ntype in _ENCRYPTOR_TYPES:
        return "Encryption Boundary"
    label = (node.get("label") or "").lower()
    if "dmz" in label:
        return "Perimeter / DMZ"
    if "mgmt" in label or "management" in label or "oob" in label:
        return "Management / NOC"
    return "General"


# ---------------------------------------------------------------------------
# 1. System Boundary Diagram  (RMF Step 1)
# ---------------------------------------------------------------------------


def generate_boundary_diagram(
    graph: dict,
    system_name: str = "Information System",
    classification: str = "CUI",
    groups: list[dict] | None = None,
    region_id: str | None = None,
) -> dict:
    """Generate a system boundary diagram from topology data.

    Returns a dict with:
      - zones: list of zone dicts (name, node_count, nodes, is_boundary)
      - interconnections: list of cross-zone edges
      - external_interfaces: list of WAN/cloud entry points
      - svg: SVG markup of the boundary diagram
      - metadata: system_name, classification, generated_at
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # If region_id specified and groups provided, filter to nodes in that region
    if region_id and groups:
        region_node_ids = _collect_region_nodes(region_id, groups, nodes)
        if region_node_ids:
            node_set = set(region_node_ids)
            nodes = [n for n in nodes if n["id"] in node_set]
            edges = [e for e in edges if e["source"] in node_set or e["target"] in node_set]

    node_map = {n["id"]: n for n in nodes}

    # Assign nodes to zones
    zone_nodes: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        zone = _classify_node_zone(n)
        zone_nodes[zone].append(
            {
                "id": n["id"],
                "label": n.get("label", n["id"]),
                "type": n.get("type", "unknown"),
            }
        )

    # Identify cross-zone edges (interconnections)
    interconnections = []
    for e in edges:
        src = node_map.get(e["source"])
        tgt = node_map.get(e["target"])
        if not src or not tgt:
            continue
        src_zone = _classify_node_zone(src)
        tgt_zone = _classify_node_zone(tgt)
        if src_zone != tgt_zone:
            interconnections.append(
                {
                    "source_zone": src_zone,
                    "target_zone": tgt_zone,
                    "source_node": src.get("label", e["source"]),
                    "target_node": tgt.get("label", e["target"]),
                    "protocol": e.get("protocol", ""),
                    "label": e.get("label", ""),
                }
            )

    # External interfaces (WAN nodes or edges leaving the boundary)
    external_interfaces = []
    for n in nodes:
        ntype = n.get("type", "")
        if ntype in _WAN_TYPES or ntype == "internet-exchange":
            external_interfaces.append(
                {
                    "node_id": n["id"],
                    "label": n.get("label", n["id"]),
                    "type": ntype,
                    "connected_to": [
                        node_map[nb]["label"]
                        for e in edges
                        for nb in [
                            e["target"] if e["source"] == n["id"] else e["source"] if e["target"] == n["id"] else None
                        ]
                        if nb and nb in node_map
                    ],
                }
            )

    zones = []
    boundary_zones = {"Perimeter / DMZ", "Encryption Boundary", "WAN / External"}
    for zname, znodes in sorted(zone_nodes.items()):
        zones.append(
            {
                "name": zname,
                "node_count": len(znodes),
                "nodes": znodes,
                "is_boundary": zname in boundary_zones,
            }
        )

    svg = _render_boundary_svg(zones, interconnections, system_name, classification)

    return {
        "artifact": "system_boundary_diagram",
        "system_name": system_name,
        "classification": classification,
        "zones": zones,
        "zone_count": len(zones),
        "interconnections": interconnections,
        "external_interfaces": external_interfaces,
        "svg": svg,
        "generated_at": _now_iso(),
    }


def _collect_region_nodes(
    region_id: str,
    groups: list[dict],
    nodes: list[dict],
) -> list[str]:
    """Collect all node IDs belonging to a region (group) and its children."""
    group_map = {g["id"]: g for g in groups}
    target = group_map.get(region_id)
    if not target:
        return []

    # Collect this group + all descendant groups
    group_ids = set()
    queue = [region_id]
    while queue:
        gid = queue.pop()
        group_ids.add(gid)
        for g in groups:
            if g.get("parent_id") == gid and g["id"] not in group_ids:
                queue.append(g["id"])

    # Collect auto_nodes from all matched groups
    node_ids: set[str] = set()
    for gid in group_ids:
        g = group_map.get(gid)
        if not g:
            continue
        import json

        try:
            auto = json.loads(g.get("auto_nodes_json") or "[]")
            node_ids.update(auto)
        except Exception:
            pass

    # Also include nodes geometrically inside the region bounds
    if target and not node_ids:
        rx, ry = float(target.get("pos_x") or 0), float(target.get("pos_y") or 0)
        rw, rh = float(target.get("width") or 400), float(target.get("height") or 300)
        for n in nodes:
            nx, ny = float(n.get("x") or 0), float(n.get("y") or 0)
            if rx <= nx <= rx + rw and ry <= ny <= ry + rh:
                node_ids.add(n["id"])

    return list(node_ids)


def _render_boundary_svg(
    zones: list[dict],
    interconnections: list[dict],
    system_name: str,
    classification: str,
) -> str:
    """Render a simplified SVG system boundary diagram."""
    zone_colors = {
        "Perimeter / DMZ": "#e74c3c",
        "Core Network": "#2980b9",
        "Distribution": "#27ae60",
        "Access Layer": "#8e44ad",
        "Wireless Access": "#8e44ad",
        "Server Enclave": "#d35400",
        "Management / NOC": "#2c3e50",
        "User Enclave": "#16a085",
        "IoT Segment": "#f39c12",
        "WAN / External": "#c0392b",
        "Encryption Boundary": "#e67e22",
        "General": "#7f8c8d",
    }

    margin = 20
    zone_w = 220
    zone_h_base = 60
    node_h = 18
    cols = 3
    padding = 15

    # Calculate zone heights
    zone_dims: list[tuple[int, int, int, int]] = []  # x, y, w, h
    max_row_h: list[int] = []
    for i, z in enumerate(zones):
        col = i % cols
        row = i // cols
        h = zone_h_base + len(z["nodes"]) * node_h
        while row >= len(max_row_h):
            max_row_h.append(0)
        max_row_h[row] = max(max_row_h[row], h)

    # Now compute positions
    y_offset = 80  # header space
    for i, z in enumerate(zones):
        col = i % cols
        row = i // cols
        x = margin + col * (zone_w + padding)
        y = y_offset + sum(max_row_h[:row]) + row * padding
        h = zone_h_base + len(z["nodes"]) * node_h
        zone_dims.append((x, y, zone_w, h))

    total_w = margin * 2 + cols * zone_w + (cols - 1) * padding
    total_h = y_offset + sum(max_row_h) + len(max_row_h) * padding + 60
    total_w = max(total_w, 500)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} {total_h}" '
        f'width="{total_w}" height="{total_h}" style="font-family:monospace;font-size:11px">',
        f'<rect width="{total_w}" height="{total_h}" fill="#1a1a2e" rx="8"/>',
        # Classification banner
        f'<rect x="0" y="0" width="{total_w}" height="24" fill="#c0392b"/>',
        f'<text x="{total_w // 2}" y="16" text-anchor="middle" fill="#fff" '
        f'font-weight="bold" font-size="11">{classification} // SP-CTI</text>',
        # Title
        f'<text x="{total_w // 2}" y="50" text-anchor="middle" fill="#ecf0f1" '
        f'font-size="16" font-weight="bold">System Boundary Diagram: {_svg_escape(system_name)}</text>',
        f'<text x="{total_w // 2}" y="68" text-anchor="middle" fill="#95a5a6" '
        f'font-size="10">RMF Step 1 -- Authorization Boundary</text>',
        # Outer boundary rectangle
        f'<rect x="{margin - 5}" y="{y_offset - 10}" '
        f'width="{total_w - 2 * margin + 10}" height="{total_h - y_offset - 30}" '
        f'fill="none" stroke="#e74c3c" stroke-width="2" stroke-dasharray="8,4" rx="6"/>',
        f'<text x="{margin}" y="{total_h - 15}" fill="#e74c3c" font-size="9">--- Authorization Boundary ---</text>',
    ]

    for i, z in enumerate(zones):
        x, y, w, h = zone_dims[i]
        color = zone_colors.get(z["name"], "#7f8c8d")
        stroke = "#e74c3c" if z["is_boundary"] else color
        lines.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'fill="{color}22" stroke="{stroke}" stroke-width="1.5" rx="4"/>'
        )
        lines.append(
            f'<text x="{x + 8}" y="{y + 18}" fill="{color}" font-weight="bold" '
            f'font-size="11">{_svg_escape(z["name"])} ({z["node_count"]})</text>'
        )
        for j, nd in enumerate(z["nodes"][:8]):
            ny = y + 36 + j * node_h
            lines.append(
                f'<text x="{x + 14}" y="{ny}" fill="#bdc3c7" font-size="9">{_svg_escape(nd["label"][:25])}</text>'
            )
        if len(z["nodes"]) > 8:
            ny = y + 36 + 8 * node_h
            lines.append(
                f'<text x="{x + 14}" y="{ny}" fill="#7f8c8d" font-size="9">... +{len(z["nodes"]) - 8} more</text>'
            )

    lines.append("</svg>")
    return "\n".join(lines)


def _svg_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ---------------------------------------------------------------------------
# 2. Data Flow Diagram
# ---------------------------------------------------------------------------


def generate_data_flow_diagram(
    graph: dict,
    classification: str = "CUI",
    groups: list[dict] | None = None,
    region_id: str | None = None,
) -> dict:
    """Generate a data flow diagram from topology edges.

    Returns:
      - flows: list of data flow dicts with classification, encryption status
      - trust_boundaries: list of boundaries where trust level changes
      - external_entities: nodes that represent external data sources/sinks
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    if region_id and groups:
        region_node_ids = set(_collect_region_nodes(region_id, groups, nodes))
        if region_node_ids:
            nodes = [n for n in nodes if n["id"] in region_node_ids]
            edges = [e for e in edges if e["source"] in region_node_ids or e["target"] in region_node_ids]

    node_map = {n["id"]: n for n in nodes}

    encrypted_protos = {"ipsec", "ipsec esp", "gre/ipsec", "tls", "https", "ssh", "macsec", "snmpv3"}

    flows = []
    for e in edges:
        src = node_map.get(e["source"])
        tgt = node_map.get(e["target"])
        if not src or not tgt:
            continue
        proto = (e.get("protocol") or "").lower().strip()
        is_encrypted = proto in encrypted_protos
        src_zone = _classify_node_zone(src)
        tgt_zone = _classify_node_zone(tgt)
        crosses_boundary = src_zone != tgt_zone

        # Determine data classification for this flow
        flow_class = classification
        if any(node_map.get(nid, {}).get("type", "") in _WAN_TYPES for nid in [e["source"], e["target"]]):
            flow_class = f"{classification} (in-transit)"

        flows.append(
            {
                "id": e.get("id", ""),
                "source": {
                    "id": e["source"],
                    "label": src.get("label", e["source"]),
                    "type": src.get("type", ""),
                    "zone": src_zone,
                },
                "target": {
                    "id": e["target"],
                    "label": tgt.get("label", e["target"]),
                    "type": tgt.get("type", ""),
                    "zone": tgt_zone,
                },
                "protocol": e.get("protocol", ""),
                "label": e.get("label", ""),
                "encrypted": is_encrypted,
                "encryption_method": proto if is_encrypted else "NONE",
                "data_classification": flow_class,
                "crosses_boundary": crosses_boundary,
                "bandwidth": e.get("bandwidth", ""),
            }
        )

    # Trust boundaries: zone transitions
    trust_boundaries = []
    seen_boundaries: set[tuple[str, str]] = set()
    for f in flows:
        if f["crosses_boundary"]:
            pair = tuple(sorted([f["source"]["zone"], f["target"]["zone"]]))
            if pair not in seen_boundaries:
                seen_boundaries.add(pair)
                trust_boundaries.append(
                    {
                        "zone_a": pair[0],
                        "zone_b": pair[1],
                        "flows_crossing": sum(
                            1 for fl in flows if tuple(sorted([fl["source"]["zone"], fl["target"]["zone"]])) == pair
                        ),
                        "all_encrypted": all(
                            fl["encrypted"]
                            for fl in flows
                            if tuple(sorted([fl["source"]["zone"], fl["target"]["zone"]])) == pair
                        ),
                    }
                )

    # External entities
    external_entities = []
    for n in nodes:
        ntype = n.get("type", "")
        if ntype in _WAN_TYPES or ntype == "internet-exchange" or ntype.startswith("cloud"):
            external_entities.append(
                {
                    "id": n["id"],
                    "label": n.get("label", n["id"]),
                    "type": ntype,
                    "zone": _classify_node_zone(n),
                }
            )

    return {
        "artifact": "data_flow_diagram",
        "classification": classification,
        "total_flows": len(flows),
        "encrypted_flows": sum(1 for f in flows if f["encrypted"]),
        "unencrypted_flows": sum(1 for f in flows if not f["encrypted"]),
        "cross_boundary_flows": sum(1 for f in flows if f["crosses_boundary"]),
        "flows": flows,
        "trust_boundaries": trust_boundaries,
        "external_entities": external_entities,
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# 3. Ports / Protocols / Services Matrix
# ---------------------------------------------------------------------------


def generate_pps_matrix(
    graph: dict,
    groups: list[dict] | None = None,
    region_id: str | None = None,
) -> dict:
    """Generate a Ports/Protocols/Services matrix from topology edges.

    Returns a list of PPS entries with port, protocol, service, justification,
    and encryption status.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    if region_id and groups:
        region_node_ids = set(_collect_region_nodes(region_id, groups, nodes))
        if region_node_ids:
            nodes = [n for n in nodes if n["id"] in region_node_ids]
            edges = [e for e in edges if e["source"] in region_node_ids or e["target"] in region_node_ids]

    node_map = {n["id"]: n for n in nodes}

    # Collect unique protocol usages
    pps_entries: dict[str, dict] = {}  # keyed by protocol
    for e in edges:
        proto = (e.get("protocol") or "").lower().strip()
        if not proto:
            # Infer from label
            label = (e.get("label") or "").lower()
            if "ipsec" in label:
                proto = "ipsec"
            elif "ospf" in label:
                proto = "ospf"
            elif "bgp" in label:
                proto = "bgp"
            elif "vxlan" in label:
                proto = "vxlan"
            elif "mpls" in label:
                proto = "mpls"
        if not proto:
            continue

        pps = _PROTOCOL_PPS.get(
            proto,
            {
                "port": "N/A",
                "service": proto.upper(),
                "direction": "Bidirectional",
            },
        )

        src = node_map.get(e["source"])
        tgt = node_map.get(e["target"])
        src_label = src.get("label", e["source"]) if src else e["source"]
        tgt_label = tgt.get("label", e["target"]) if tgt else e["target"]

        key = proto
        if key not in pps_entries:
            encrypted_protos = {"ipsec", "ipsec esp", "gre/ipsec", "tls", "https", "ssh", "macsec", "snmpv3"}
            pps_entries[key] = {
                "protocol": proto.upper(),
                "port": pps["port"],
                "service": pps["service"],
                "direction": pps["direction"],
                "encrypted": proto in encrypted_protos,
                "fips_validated": False,
                "justification": "",
                "usage_count": 0,
                "endpoints": [],
            }

        entry = pps_entries[key]
        entry["usage_count"] += 1
        if len(entry["endpoints"]) < 10:
            entry["endpoints"].append(f"{src_label} <-> {tgt_label}")

        # Check if FIPS-validated crypto is on this link
        if src and src.get("type", "") in _ENCRYPTOR_TYPES:
            entry["fips_validated"] = True
        if tgt and tgt.get("type", "") in _ENCRYPTOR_TYPES:
            entry["fips_validated"] = True

    # Generate justifications
    for key, entry in pps_entries.items():
        if key in ("ospf", "bgp", "bgp evpn"):
            entry["justification"] = "Dynamic routing protocol required for network convergence and path selection."
        elif key in ("ipsec", "ipsec esp", "gre/ipsec", "tls", "macsec"):
            entry["justification"] = (
                "Encryption protocol required for protecting CUI/classified data in transit per NIST SC-8, SC-13."
            )
        elif key in ("ssh", "https"):
            entry["justification"] = "Encrypted management access per NIST SC-8, AC-17."
        elif key in ("snmpv3",):
            entry["justification"] = "Encrypted network monitoring per NIST SI-4."
        elif key in ("vxlan",):
            entry["justification"] = "Network overlay for micro-segmentation per NIST SC-7."
        elif key in ("telnet", "http", "snmpv1", "snmpv2"):
            entry["justification"] = "WARNING: Insecure protocol -- must be replaced with encrypted alternative."
        else:
            entry["justification"] = f"Network service: {entry['service']}"

    matrix = sorted(pps_entries.values(), key=lambda x: x["protocol"])

    return {
        "artifact": "ports_protocols_services_matrix",
        "total_protocols": len(matrix),
        "encrypted_protocols": sum(1 for e in matrix if e["encrypted"]),
        "insecure_protocols": sum(
            1 for e in matrix if not e["encrypted"] and e["protocol"].lower() in ("telnet", "http", "snmpv1", "snmpv2")
        ),
        "matrix": matrix,
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# 4. Pre-populated STIG Checklist
# ---------------------------------------------------------------------------

# DISA Network STIG checks derived from topology objects
_STIG_CHECKLIST_ITEMS: list[dict[str, Any]] = [
    {
        "stig_id": "V-237753",
        "rule_id": "SV-237753r1",
        "severity": "CAT1",
        "title": "Network devices must use FIPS-validated encryption for management",
        "check": "mgmt_encrypted",
        "fix": "Configure SSH/HTTPS for all device management; disable Telnet/HTTP.",
    },
    {
        "stig_id": "V-237754",
        "rule_id": "SV-237754r1",
        "severity": "CAT1",
        "title": "Network firewalls must be deployed at enclave boundaries",
        "check": "firewall_at_boundary",
        "fix": "Deploy firewall between internal network and WAN/internet.",
    },
    {
        "stig_id": "V-237755",
        "rule_id": "SV-237755r1",
        "severity": "CAT1",
        "title": "Encrypted tunnels must use FIPS 140-2/3 validated crypto modules",
        "check": "fips_crypto",
        "fix": "Replace non-FIPS crypto with FIPS 140-2 Level 2+ validated modules.",
    },
    {
        "stig_id": "V-237756",
        "rule_id": "SV-237756r1",
        "severity": "CAT2",
        "title": "Core/distribution network devices must have redundant links",
        "check": "core_redundancy",
        "fix": "Add redundant uplinks to all core and distribution devices.",
    },
    {
        "stig_id": "V-237757",
        "rule_id": "SV-237757r1",
        "severity": "CAT2",
        "title": "Network must implement out-of-band management",
        "check": "oob_mgmt",
        "fix": "Deploy separate OOB management network for device administration.",
    },
    {
        "stig_id": "V-237758",
        "rule_id": "SV-237758r1",
        "severity": "CAT2",
        "title": "DNS must be configured with redundant servers",
        "check": "dns_redundancy",
        "fix": "Configure at least 2 DNS resolvers or use cloud DNS service.",
    },
    {
        "stig_id": "V-237759",
        "rule_id": "SV-237759r1",
        "severity": "CAT2",
        "title": "WAN links must be encrypted",
        "check": "wan_encrypted",
        "fix": "Deploy IPSec, MACsec, or Type 1 encryption on all WAN links.",
    },
    {
        "stig_id": "V-237760",
        "rule_id": "SV-237760r1",
        "severity": "CAT2",
        "title": "Cloud VPCs must be isolated from on-premises networks via firewall",
        "check": "cloud_isolated",
        "fix": "Deploy firewall/ACL between cloud VPC and on-premises network.",
    },
    {
        "stig_id": "V-237761",
        "rule_id": "SV-237761r1",
        "severity": "CAT2",
        "title": "Network segmentation must prevent lateral movement",
        "check": "segmentation",
        "fix": "Deploy internal firewalls or ACLs between security zones.",
    },
    {
        "stig_id": "V-237762",
        "rule_id": "SV-237762r1",
        "severity": "CAT3",
        "title": "All network devices must have descriptive hostnames",
        "check": "devices_labeled",
        "fix": "Assign meaningful hostnames to all network devices.",
    },
    {
        "stig_id": "V-237763",
        "rule_id": "SV-237763r1",
        "severity": "CAT3",
        "title": "Network diagram must match as-built documentation",
        "check": "as_built_exists",
        "fix": "Save current topology as 'As-Built' version for ATO documentation.",
    },
    {
        "stig_id": "V-237764",
        "rule_id": "SV-237764r1",
        "severity": "CAT2",
        "title": "SECRET+ networks require NSA Type 1 encryption",
        "check": "type1_for_secret",
        "fix": "Deploy KG-175D, KG-250, or other NSA Type 1 encryptors on SECRET+ links.",
    },
    {
        "stig_id": "V-237765",
        "rule_id": "SV-237765r1",
        "severity": "CAT2",
        "title": "Encryptor throughput must match or exceed link bandwidth",
        "check": "encryptor_speed",
        "fix": "Upgrade encryptor to model rated for link bandwidth.",
    },
    {
        "check_id": "NET-HYB-BFD",
        "title": "BFD enabled on dedicated interconnect VIFs",
        "severity": "CAT1",
        "check": lambda nodes, edges, types: any(
            n.get("config", {}).get("bfd_enabled") for n in nodes if types.get(n["id"]) in _DX_TYPES
        ),
        "fix": "Enable BFD on all DX/ER/IC/FC virtual interfaces for sub-second failover",
    },
    {
        "check_id": "NET-HYB-VPN-BACKUP",
        "title": "VPN backup for dedicated interconnect",
        "severity": "CAT2",
        "check": lambda nodes, edges, types: (
            any(types.get(n["id"]) in _VPN_TYPES for n in nodes)
            if any(types.get(n["id"]) in _DX_TYPES for n in nodes)
            else True
        ),
        "fix": "Configure Site-to-Site VPN as automatic backup for DX/ER failure",
    },
]


def generate_stig_checklist(
    graph: dict,
    classification: str = "CUI",
    has_as_built_version: bool = False,
    groups: list[dict] | None = None,
    region_id: str | None = None,
) -> dict:
    """Generate a pre-populated STIG checklist from topology objects.

    Each checklist item is evaluated against the topology and marked as
    NotAFinding, Open, or Not_Applicable with evidence/comments.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    if region_id and groups:
        region_node_ids = set(_collect_region_nodes(region_id, groups, nodes))
        if region_node_ids:
            nodes = [n for n in nodes if n["id"] in region_node_ids]
            edges = [e for e in edges if e["source"] in region_node_ids or e["target"] in region_node_ids]

    node_types = {n["id"]: n.get("type", "") for n in nodes}

    adj: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])

    facts = _compute_stig_facts(nodes, edges, node_types, adj)

    checklist: list[dict] = []
    for item in _STIG_CHECKLIST_ITEMS:
        check = item["check"]
        if callable(check):
            try:
                passed = check(nodes, edges, node_types)
                status = "NotAFinding" if passed else "Open"
            except Exception:
                status = "Not_Reviewed"
            checklist.append({
                "stig_id": item.get("check_id", "CUSTOM"),
                "rule_id": item.get("check_id", "CUSTOM"),
                "severity": item.get("severity", "CAT2"),
                "title": item.get("title", ""),
                "status": status,
                "comments": "",
                "fix_text": item.get("fix", "") if status == "Open" else "",
            })
            continue

        status, comments = _evaluate_stig_check(
            check, item, nodes, node_types, facts, classification, has_as_built_version
        )
        checklist.append({
            "stig_id": item["stig_id"],
            "rule_id": item["rule_id"],
            "severity": item["severity"],
            "title": item["title"],
            "status": status,
            "comments": comments,
            "fix_text": item["fix"] if status == "Open" else "",
        })

    cat1_open = sum(1 for c in checklist if c["severity"] == "CAT1" and c["status"] == "Open")
    cat2_open = sum(1 for c in checklist if c["severity"] == "CAT2" and c["status"] == "Open")
    cat3_open = sum(1 for c in checklist if c["severity"] == "CAT3" and c["status"] == "Open")

    return {
        "artifact": "stig_checklist",
        "classification": classification,
        "total_checks": len(checklist),
        "not_a_finding": sum(1 for c in checklist if c["status"] == "NotAFinding"),
        "open": sum(1 for c in checklist if c["status"] == "Open"),
        "not_applicable": sum(1 for c in checklist if c["status"] == "Not_Applicable"),
        "not_reviewed": sum(1 for c in checklist if c["status"] == "Not_Reviewed"),
        "cat1_open": cat1_open,
        "cat2_open": cat2_open,
        "cat3_open": cat3_open,
        "gate_passed": cat1_open == 0,
        "checklist": checklist,
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# 5. Full ATO Package Generator (combines all artifacts)
# ---------------------------------------------------------------------------


def generate_ato_package(
    topology_id: str,
    graph: dict,
    system_name: str = "Information System",
    classification: str = "CUI",
    regimes: list[str] | None = None,
    groups: list[dict] | None = None,
    region_id: str | None = None,
    has_as_built_version: bool = False,
) -> dict:
    """Generate a complete partial ATO package with all 4 artifacts.

    Args:
        topology_id: UUID of the topology.
        graph: Dict with "nodes" and "edges" lists.
        system_name: Name of the system for documentation.
        classification: CUI, SECRET, TOP SECRET, PUBLIC.
        regimes: List of compliance regime keys for the audit.
        groups: List of nc_groups dicts (for region filtering).
        region_id: Optional group ID to scope generation to a region.
        has_as_built_version: Whether an as-built version exists.

    Returns:
        Dict containing all 4 artifacts plus summary metadata.
    """
    if regimes is None:
        regimes = ["fisma_high", "stig"]

    # Generate all 4 artifacts
    boundary = generate_boundary_diagram(graph, system_name, classification, groups, region_id)
    data_flow = generate_data_flow_diagram(graph, classification, groups, region_id)
    pps = generate_pps_matrix(graph, groups, region_id)
    stig = generate_stig_checklist(graph, classification, has_as_built_version, groups, region_id)

    # Run compliance audit for additional context
    audit = run_compliance_audit(topology_id, graph, regimes, classification, has_as_built_version)

    # Compute overall readiness
    stig_pass_rate = round(stig["not_a_finding"] / max(stig["total_checks"] - stig["not_applicable"], 1) * 100, 1)
    avg_compliance = 0.0
    if audit["scores"]:
        avg_compliance = round(sum(s["score_pct"] for s in audit["scores"].values()) / len(audit["scores"]), 1)
    encryption_coverage = round(data_flow["encrypted_flows"] / max(data_flow["total_flows"], 1) * 100, 1)

    # Package ID for tracking
    pkg_id = str(_uuid.uuid4())
    pkg_hash = hashlib.sha256(f"{topology_id}:{region_id or 'full'}:{_now_iso()}".encode()).hexdigest()[:12]

    return {
        "package_id": pkg_id,
        "package_hash": pkg_hash,
        "topology_id": topology_id,
        "system_name": system_name,
        "classification": classification,
        "regimes": regimes,
        "region_id": region_id,
        "generated_at": _now_iso(),
        # Summary
        "summary": {
            "stig_pass_rate": stig_pass_rate,
            "stig_gate_passed": stig["gate_passed"],
            "compliance_score": avg_compliance,
            "encryption_coverage_pct": encryption_coverage,
            "zone_count": boundary["zone_count"],
            "total_flows": data_flow["total_flows"],
            "total_protocols": pps["total_protocols"],
            "insecure_protocols": pps["insecure_protocols"],
            "cat1_findings": audit["cat1_count"],
            "cat2_findings": audit["cat2_count"],
            "cat3_findings": audit["cat3_count"],
            "overall_readiness": "GREEN"
            if (stig["gate_passed"] and avg_compliance >= 80 and pps["insecure_protocols"] == 0)
            else "YELLOW"
            if (stig["cat1_open"] <= 1 and avg_compliance >= 60)
            else "RED",
        },
        # Artifacts
        "artifacts": {
            "system_boundary_diagram": boundary,
            "data_flow_diagram": data_flow,
            "ports_protocols_services": pps,
            "stig_checklist": stig,
            "compliance_audit": audit,
        },
    }


# ---------------------------------------------------------------------------
# 6. PPS Matrix for Enclave / Device Pair
# ---------------------------------------------------------------------------


def get_topology_enclaves(graph: dict, groups: list[dict] | None = None) -> dict:
    """Return all enclaves (zones) and device nodes available for pair selection.

    Returns:
        {
          "enclaves": [{"id": zone_name, "label": zone_name, "node_count": N}, ...],
          "nodes": [{"id": node_id, "label": node_label, "type": node_type, "zone": zone}, ...],
        }
    """
    nodes = graph.get("nodes", [])
    zone_counts: dict[str, int] = defaultdict(int)
    node_list = []
    for n in nodes:
        zone = _classify_node_zone(n)
        zone_counts[zone] += 1
        node_list.append(
            {
                "id": n["id"],
                "label": n.get("label", n["id"]),
                "type": n.get("type", "unknown"),
                "zone": zone,
            }
        )

    enclaves = [{"id": z, "label": z, "node_count": c} for z, c in sorted(zone_counts.items())]
    return {"enclaves": enclaves, "nodes": node_list}


def generate_pps_matrix_for_pair(
    graph: dict,
    source_selector: str,
    dest_selector: str,
    selector_type: str = "zone",  # "zone" | "node"
    groups: list[dict] | None = None,
) -> dict:
    """Generate a PPS matrix scoped to traffic between two enclaves or two nodes.

    Args:
        graph: Dict with "nodes" and "edges" lists.
        source_selector: Zone name (selector_type="zone") or node ID (selector_type="node").
        dest_selector: Zone name or node ID for the destination side.
        selector_type: "zone" to filter by security zone pair, "node" for exact device pair.
        groups: nc_groups rows (unused here, reserved for future region scoping).

    Returns:
        PPS matrix dict with rows filtered to the selected pair plus SSP export fields.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    node_map: dict[str, dict] = {n["id"]: n for n in nodes}

    # Build sets of node IDs for each side
    if selector_type == "node":
        src_ids = {source_selector}
        dst_ids = {dest_selector}
        src_label = node_map.get(source_selector, {}).get("label", source_selector)
        dst_label = node_map.get(dest_selector, {}).get("label", dest_selector)
    else:
        # Zone-based: collect all nodes in each zone
        src_ids = {n["id"] for n in nodes if _classify_node_zone(n) == source_selector}
        dst_ids = {n["id"] for n in nodes if _classify_node_zone(n) == dest_selector}
        src_label = source_selector
        dst_label = dest_selector

    # Filter edges that cross between the two selected sides (either direction)
    cross_edges = []
    for e in edges:
        src_in_src = e["source"] in src_ids
        src_in_dst = e["source"] in dst_ids
        tgt_in_src = e["target"] in src_ids
        tgt_in_dst = e["target"] in dst_ids

        if (src_in_src and tgt_in_dst) or (src_in_dst and tgt_in_src):
            cross_edges.append(e)

    # Also inspect firewall rules stored in node config
    firewall_rows: list[dict] = []
    firewall_node_ids = src_ids | dst_ids
    for nid in firewall_node_ids:
        n = node_map.get(nid)
        if not n:
            continue
        if n.get("type", "") not in _FIREWALL_TYPES:
            continue
        rules = []
        cfg = n.get("config") or {}
        if isinstance(cfg, dict):
            rules = cfg.get("rules", cfg.get("acl", cfg.get("policy", [])))
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            proto = (rule.get("protocol") or rule.get("proto") or "").lower().strip()
            port = str(rule.get("port") or rule.get("dst_port") or rule.get("dport") or "N/A")
            action = (rule.get("action") or "permit").lower()
            src_addr = rule.get("src", rule.get("source", "any"))
            dst_addr = rule.get("dst", rule.get("destination", "any"))
            svc = rule.get("service", rule.get("application", proto.upper() if proto else "ANY"))
            firewall_rows.append(
                {
                    "_from_firewall": True,
                    "_firewall_label": n.get("label", nid),
                    "_action": action,
                    "protocol": proto.upper() if proto else "ANY",
                    "port": port,
                    "service": svc,
                    "src_addr": src_addr,
                    "dst_addr": dst_addr,
                }
            )

    # Build PPS entries from cross-boundary edges (same logic as generate_pps_matrix)
    pps_entries: dict[str, dict] = {}
    encrypted_protos = {"ipsec", "ipsec esp", "gre/ipsec", "tls", "https", "ssh", "macsec", "snmpv3"}
    insecure_protos = {"telnet", "http", "snmpv1", "snmpv2"}

    for e in cross_edges:
        proto = (e.get("protocol") or "").lower().strip()
        if not proto:
            lbl = (e.get("label") or "").lower()
            for kw in ("ipsec", "ospf", "bgp", "vxlan", "mpls", "tls", "ssh", "https"):
                if kw in lbl:
                    proto = kw
                    break
        if not proto:
            proto = "unknown"

        pps_info = _PROTOCOL_PPS.get(
            proto,
            {
                "port": "N/A",
                "service": proto.upper() if proto != "unknown" else "Unspecified",
                "direction": "Bidirectional",
            },
        )

        src_node = node_map.get(e["source"])
        tgt_node = node_map.get(e["target"])
        src_node_label = src_node.get("label", e["source"]) if src_node else e["source"]
        tgt_node_label = tgt_node.get("label", e["target"]) if tgt_node else e["target"]

        # Determine flow direction relative to src/dst selection
        if e["source"] in src_ids:
            flow_src = src_node_label
            flow_dst = tgt_node_label
        else:
            flow_src = tgt_node_label
            flow_dst = src_node_label

        key = proto
        if key not in pps_entries:
            fips_val = False
            if src_node and src_node.get("type", "") in _ENCRYPTOR_TYPES:
                fips_val = True
            if tgt_node and tgt_node.get("type", "") in _ENCRYPTOR_TYPES:
                fips_val = True

            pps_entries[key] = {
                "protocol": proto.upper(),
                "port": pps_info["port"],
                "service": pps_info["service"],
                "direction": pps_info["direction"],
                "encrypted": proto in encrypted_protos,
                "fips_validated": fips_val,
                "risk": "HIGH" if proto in insecure_protos else "LOW" if proto in encrypted_protos else "MEDIUM",
                "justification": _pps_justification(proto),
                "usage_count": 0,
                "endpoints": [],
                "source_enclave": src_label,
                "dest_enclave": dst_label,
            }

        entry = pps_entries[key]
        entry["usage_count"] += 1
        if len(entry["endpoints"]) < 10:
            entry["endpoints"].append(f"{flow_src} → {flow_dst}")

    matrix = sorted(pps_entries.values(), key=lambda x: (x["risk"] != "HIGH", x["protocol"]))

    # Merge firewall rule entries as supplemental rows
    fw_supplement: list[dict] = []
    for fr in firewall_rows:
        fw_supplement.append(
            {
                "protocol": fr["protocol"],
                "port": fr["port"],
                "service": fr["service"],
                "direction": f"{fr['src_addr']} → {fr['dst_addr']}",
                "encrypted": fr["protocol"].lower() in encrypted_protos,
                "fips_validated": False,
                "risk": "HIGH"
                if fr["_action"] == "deny"
                else ("HIGH" if fr["protocol"].lower() in insecure_protos else "MEDIUM"),
                "justification": f"Firewall rule on {fr['_firewall_label']}: action={fr['_action']}",
                "usage_count": 1,
                "endpoints": [f"{fr['src_addr']} → {fr['dst_addr']}"],
                "source_enclave": src_label,
                "dest_enclave": dst_label,
                "_source": "firewall_rule",
                "_action": fr["_action"],
            }
        )

    return {
        "artifact": "pps_matrix_pair",
        "source_selector": source_selector,
        "dest_selector": dest_selector,
        "selector_type": selector_type,
        "source_label": src_label,
        "dest_label": dst_label,
        "total_protocols": len(matrix),
        "total_fw_rules": len(fw_supplement),
        "encrypted_protocols": sum(1 for e in matrix if e["encrypted"]),
        "insecure_protocols": sum(1 for e in matrix if e["risk"] == "HIGH" and not e.get("_source")),
        "matrix": matrix,
        "firewall_rules": fw_supplement,
        "generated_at": _now_iso(),
    }


def _pps_justification(proto: str) -> str:
    """Return standard justification text for a protocol."""
    routing = {"ospf", "bgp", "bgp evpn", "mpls", "vxlan"}
    encrypted = {"ipsec", "ipsec esp", "gre/ipsec", "tls", "macsec"}
    mgmt_sec = {"ssh", "https", "snmpv3"}
    insecure = {"telnet", "http", "snmpv1", "snmpv2"}
    infra = {"dns", "ntp", "syslog", "radius", "tacacs", "rdma"}

    if proto in routing:
        return "Dynamic routing/overlay protocol required for network convergence. (NIST SC-5, SC-7)"
    if proto in encrypted:
        return "Encryption protocol required to protect CUI/classified data in transit. (NIST SC-8, SC-13)"
    if proto in mgmt_sec:
        return "Encrypted management protocol required for secure device access. (NIST SC-8, AC-17)"
    if proto in insecure:
        return "WARNING: Insecure protocol — must be replaced with encrypted alternative per STIG."
    if proto in infra:
        return f"Infrastructure service ({proto.upper()}) required for system operations. (NIST SC-5)"
    return f"Network service required for {proto.upper()} functionality."


def export_pps_as_ssp_table(pps_result: dict, fmt: str = "csv") -> str:
    """Export a PPS matrix result as an SSP-formatted table.

    Args:
        pps_result: Return value of generate_pps_matrix_for_pair().
        fmt: "csv" or "markdown".

    Returns:
        String content of the table (UTF-8).
    """
    rows = pps_result.get("matrix", []) + pps_result.get("firewall_rules", [])
    src_label = pps_result.get("source_label", "Source")
    dst_label = pps_result.get("dest_label", "Destination")

    if fmt == "markdown":
        lines = [
            "## SSP — Ports/Protocols/Services Matrix",
            f"**Source Enclave:** {src_label}  ",
            f"**Destination Enclave:** {dst_label}  ",
            f"**Generated:** {pps_result.get('generated_at', '')}",
            "",
            "| # | Protocol | Port | Service | Direction | Encrypted | FIPS | Risk | Justification | Endpoints |",
            "|---|----------|------|---------|-----------|-----------|------|------|---------------|-----------|",
        ]
        for i, row in enumerate(rows, 1):
            enc = "Yes" if row.get("encrypted") else "No"
            fips = "Yes" if row.get("fips_validated") else "No"
            eps = "; ".join(row.get("endpoints", [])[:3])
            lines.append(
                f"| {i} | {row['protocol']} | {row['port']} | {row['service']} "
                f"| {row['direction']} | {enc} | {fips} | {row.get('risk', 'MEDIUM')} "
                f"| {row['justification']} | {eps} |"
            )
        return "\n".join(lines)

    # CSV
    import io
    import csv as _csv

    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(
        [
            "Source Enclave",
            "Destination Enclave",
            "Protocol",
            "Port",
            "Service",
            "Direction",
            "Encrypted",
            "FIPS Validated",
            "Risk",
            "Justification",
            "Endpoints",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                src_label,
                dst_label,
                row["protocol"],
                row["port"],
                row["service"],
                row["direction"],
                "Yes" if row.get("encrypted") else "No",
                "Yes" if row.get("fips_validated") else "No",
                row.get("risk", "MEDIUM"),
                row["justification"],
                "; ".join(row.get("endpoints", [])[:5]),
            ]
        )
    return buf.getvalue()
