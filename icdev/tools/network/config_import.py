# [CUI // SP-CTI]
"""ICDEV™ NDC — Import Cisco IOS / NX-OS / Junos configurations and
synthesize a network topology graph.

Pipeline:

1. **Parse** each config file into a structured device record:
   ``{hostname, vendor, model, interfaces[], neighbors[], routing}``.
   Cisco uses ``ciscoconfparse2``; Junos is parsed by hand for the
   ``set``-style and curly-brace formats (no clean Python lib available).
2. **Synthesize edges** by inferring adjacency:
   - LLDP/CDP neighbor tables when present in ``show`` dumps.
   - Same-subnet IPs on different devices (point-to-point /30, /31).
   - Interface descriptions like ``uplink to core-sw01 gi0/24``.
3. **Emit** a graph dict ``{nodes, edges}`` compatible with
   ``tools.network.export_import.to_drawio`` /
   ``tools.network.visio_export.export_vsdx`` /
   ``tools.network.layout.auto_layout``.

CLI:

    python tools/network/config_import.py --file core-sw01.cfg --json
    python tools/network/config_import.py --dir ./configs/ \\
        --output topology.drawio --auto-layout

Each device becomes a node; interfaces and routing become node properties
suitable for the canvas tooltip.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path

logger = get_logger(__name__)


# ── Public API ────────────────────────────────────────────────────────


def import_config(path: str) -> dict:
    """Parse a single config file. Auto-detects Cisco vs Junos.

    Returns a device dict with ``hostname``, ``vendor``, ``interfaces``,
    ``neighbors``, ``routing``, ``raw_path``.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    vendor = detect_vendor(text)
    if vendor == "juniper":
        device = _parse_junos(text)
    else:
        device = _parse_cisco(text)
    device["raw_path"] = str(path)
    device["vendor"] = vendor
    return device


def import_directory(dir_path: str) -> list[dict]:
    """Parse every ``*.cfg`` / ``*.conf`` / ``*.txt`` in a directory."""
    base = Path(dir_path)
    devices: list[dict] = []
    patterns = ("*.cfg", "*.conf", "*.txt", "*.config")
    seen: set[Path] = set()
    for pat in patterns:
        for p in base.glob(pat):
            if p in seen:
                continue
            seen.add(p)
            try:
                devices.append(import_config(str(p)))
            except Exception as e:
                logger.warning("config_import: %s failed: %s", p, e)
    return devices


def synthesize_topology(devices: list[dict]) -> dict:
    """Convert parsed devices into a topology graph.

    Edges are inferred from:
      1. LLDP/CDP neighbor entries (highest confidence)
      2. Shared subnets on two devices' interfaces (P2P inference)
      3. Interface descriptions naming a peer device (keyword match)
    """
    nodes: list[dict] = []
    name_to_id: dict[str, str] = {}

    for d in devices:
        hostname = d.get("hostname") or "unknown"
        node_id = f"cfg-{_safe(hostname)}"
        name_to_id[hostname.lower()] = node_id
        nodes.append({
            "id": node_id,
            "label": hostname,
            "type": _classify_device(d),
            "x": 0,
            "y": 0,
            "properties": {
                "vendor": d.get("vendor", ""),
                "model": d.get("model", ""),
                "os": d.get("os", ""),
                "interface_count": len(d.get("interfaces", [])),
                "neighbor_count": len(d.get("neighbors", [])),
            },
            "interfaces": d.get("interfaces", []),
            "sources": [Path(d.get("raw_path", "")).name or "config"],
        })

    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()

    # 1. LLDP/CDP neighbors
    for d in devices:
        src_id = name_to_id.get((d.get("hostname") or "").lower())
        if not src_id:
            continue
        for nb in d.get("neighbors", []):
            peer = (nb.get("peer") or "").lower()
            dst_id = name_to_id.get(peer)
            if not dst_id or dst_id == src_id:
                continue
            key = tuple(sorted([src_id, dst_id]))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({
                "id": f"cfg-edge-{len(edges)}",
                "source": src_id,
                "target": dst_id,
                "label": f"{nb.get('local_if', '')} → {nb.get('remote_if', '')}".strip(" →"),
                "type": "ethernet",
                "properties": {"discovery": "lldp_cdp", "confidence": 0.95},
            })

    # 2. Same-subnet inference
    subnet_to_endpoints: dict[str, list[tuple[str, str]]] = {}
    for d in devices:
        src_id = name_to_id.get((d.get("hostname") or "").lower())
        if not src_id:
            continue
        for iface in d.get("interfaces", []):
            ip = iface.get("ip")
            mask = iface.get("mask")
            if not ip or not mask:
                continue
            try:
                net = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
            except ValueError:
                continue
            # Skip giant subnets — they're rarely point-to-point evidence
            if net.prefixlen < 24:
                continue
            subnet_to_endpoints.setdefault(str(net), []).append(
                (src_id, iface.get("name", ""))
            )

    for subnet, endpoints in subnet_to_endpoints.items():
        # Pair each unique device pair sharing the subnet
        seen_pair: set[tuple[str, str]] = set()
        for i in range(len(endpoints)):
            for j in range(i + 1, len(endpoints)):
                a, ai = endpoints[i]
                b, bi = endpoints[j]
                if a == b:
                    continue
                pair = tuple(sorted([a, b]))
                if pair in seen_pair or pair in seen_edges:
                    continue
                seen_pair.add(pair)
                seen_edges.add(pair)
                # Higher confidence for /30 and /31
                conf = 0.90 if "/3" in subnet else 0.65
                edges.append({
                    "id": f"cfg-edge-{len(edges)}",
                    "source": a,
                    "target": b,
                    "label": f"{ai} ↔ {bi} ({subnet})",
                    "type": "ethernet",
                    "properties": {"discovery": "subnet", "subnet": subnet,
                                   "confidence": conf},
                })

    # 3. Interface description keyword match (peer device names)
    hostnames = sorted(name_to_id.keys(), key=len, reverse=True)
    for d in devices:
        src_id = name_to_id.get((d.get("hostname") or "").lower())
        if not src_id:
            continue
        for iface in d.get("interfaces", []):
            desc = (iface.get("description") or "").lower()
            if not desc:
                continue
            for h in hostnames:
                if h == (d.get("hostname") or "").lower():
                    continue
                # Match whole-word hostname
                if re.search(r"\b" + re.escape(h) + r"\b", desc):
                    dst_id = name_to_id[h]
                    pair = tuple(sorted([src_id, dst_id]))
                    if pair in seen_edges:
                        break
                    seen_edges.add(pair)
                    edges.append({
                        "id": f"cfg-edge-{len(edges)}",
                        "source": src_id,
                        "target": dst_id,
                        "label": iface.get("name", "") + " (desc match)",
                        "type": "ethernet",
                        "properties": {"discovery": "description",
                                       "description": iface.get("description", ""),
                                       "confidence": 0.55},
                    })
                    break

    return {"nodes": nodes, "edges": edges,
            "_devices": len(devices),
            "_edges_by_method": _count_methods(edges)}


# ── Vendor detection ──────────────────────────────────────────────────


def detect_vendor(text: str) -> str:
    """Sniff the config format. Returns ``cisco`` or ``juniper``."""
    sample = text[:8192].lower()
    junos_hits = sum(1 for s in (
        "set system host-name",
        "set interfaces",
        "version 2",  # Junos rare match — keep weak
        "system {",
        "interfaces {",
        "routing-options {",
    ) if s in sample)
    cisco_hits = sum(1 for s in (
        "interface gigabitethernet",
        "interface fastethernet",
        "interface ethernet",
        "interface vlan",
        "ip route",
        "router ospf",
        "router bgp",
        "hostname ",
        "switchport ",
    ) if s in sample)
    if junos_hits > cisco_hits:
        return "juniper"
    return "cisco"


# ── Cisco parser (IOS / IOS-XE / NX-OS) ───────────────────────────────


def _parse_cisco(text: str) -> dict:
    """Parse Cisco IOS-style config via ciscoconfparse2 (optional GPL dep)."""
    try:
        from ciscoconfparse2 import CiscoConfParse  # type: ignore
    except ImportError:
        get_logger(__name__).warning(
            "ciscoconfparse2 not installed — Cisco config parsing unavailable. "
            "Install it in an isolated environment if needed."
        )
        return {"vendor": "cisco", "hostname": "", "interfaces": [], "routes": [],
                "acls": [], "vrfs": [], "parse_error": "ciscoconfparse2 not available"}

    parse = CiscoConfParse(text.splitlines(), syntax="ios")

    # Hostname
    hostname = ""
    for ln in parse.find_objects(r"^hostname\s+"):
        hostname = ln.text.split(None, 1)[1].strip()
        break

    # Interfaces
    interfaces: list[dict] = []
    for if_obj in parse.find_objects(r"^interface\s+"):
        name = if_obj.text.split(None, 1)[1].strip()
        iface: dict = {"name": name}
        for child in if_obj.children:
            t = child.text.strip()
            if t.startswith("description "):
                iface["description"] = t[len("description "):].strip()
            elif t.startswith("ip address "):
                m = re.match(r"ip address (\S+)\s+(\S+)", t)
                if m:
                    iface["ip"] = m.group(1)
                    iface["mask"] = _mask_to_cidr(m.group(2))
            elif t.startswith("switchport access vlan "):
                iface["vlan"] = t.split()[-1]
            elif t.startswith("speed "):
                iface["speed"] = t.split()[-1]
            elif t == "shutdown":
                iface["shutdown"] = True
        interfaces.append(iface)

    # CDP/LLDP neighbors (only present in show-tech dumps)
    neighbors = _extract_cisco_neighbors(text)

    # Routing protocol summary
    routing: dict = {}
    for proto in ("router ospf", "router bgp", "router eigrp"):
        for obj in parse.find_objects(r"^" + proto):
            routing.setdefault(proto.split()[1], []).append(obj.text)

    # Model from show version banner if present
    model = ""
    m = re.search(r"cisco\s+(\S+)\s+(\(.*?\))?\s*processor", text, re.IGNORECASE)
    if m:
        model = m.group(1)

    return {
        "hostname": hostname,
        "model": model,
        "os": "cisco",
        "interfaces": interfaces,
        "neighbors": neighbors,
        "routing": routing,
    }


def _extract_cisco_neighbors(text: str) -> list[dict]:
    """Pull CDP / LLDP neighbor table entries from a show dump."""
    neighbors: list[dict] = []
    # CDP detail format:
    #   Device ID: switch-02.example.com
    #   ... Interface: GigabitEthernet0/1, Port ID (outgoing port): GigabitEthernet0/24
    cdp_pattern = re.compile(
        r"Device ID:\s*(\S+).*?Interface:\s*(\S+),\s*Port ID[^:]*:\s*(\S+)",
        re.DOTALL,
    )
    for m in cdp_pattern.finditer(text):
        peer = m.group(1).split(".")[0]
        neighbors.append({
            "peer": peer,
            "local_if": m.group(2).rstrip(","),
            "remote_if": m.group(3).rstrip(","),
            "protocol": "cdp",
        })
    # LLDP brief:  System Name | Local Intf | Port ID
    for m in re.finditer(
        r"^(\S+)\s+(Gi\S+|Eth\S+|Te\S+|Fa\S+|ge-\S+|xe-\S+)\s+(\S+)\s+",
        text, re.MULTILINE,
    ):
        if m.group(1).lower() in ("system", "device", "capability"):
            continue
        neighbors.append({
            "peer": m.group(1).split(".")[0],
            "local_if": m.group(2),
            "remote_if": m.group(3),
            "protocol": "lldp",
        })
    return neighbors


# ── Junos parser (set-format + curly-brace) ──────────────────────────


def _parse_junos(text: str) -> dict:
    """Parse Junos config — handles both ``set`` and curly-brace formats."""
    if "set " in text and text.count("set ") > text.count("{") / 2:
        return _parse_junos_set(text)
    return _parse_junos_curly(text)


def _parse_junos_set(text: str) -> dict:
    """Junos set-format parser."""
    hostname = ""
    interfaces: dict[str, dict] = {}
    neighbors: list[dict] = []

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("set "):
            continue
        parts = line.split()
        # set system host-name <name>
        if parts[1:3] == ["system", "host-name"] and len(parts) >= 4:
            hostname = parts[3]
        # set interfaces <name> unit <u> family inet address X/Y
        elif parts[1] == "interfaces" and len(parts) >= 3:
            ifname = parts[2]
            iface = interfaces.setdefault(ifname, {"name": ifname})
            if "description" in parts:
                idx = parts.index("description")
                iface["description"] = " ".join(parts[idx + 1:]).strip('"')
            if "address" in parts:
                idx = parts.index("address")
                if idx + 1 < len(parts):
                    cidr = parts[idx + 1]
                    if "/" in cidr:
                        ip, mask = cidr.split("/", 1)
                        iface["ip"] = ip
                        iface["mask"] = mask
            if "vlan-id" in parts:
                idx = parts.index("vlan-id")
                if idx + 1 < len(parts):
                    iface["vlan"] = parts[idx + 1]
        # set protocols lldp neighbor … (rare in static configs)

    return {
        "hostname": hostname,
        "model": "",
        "os": "junos",
        "interfaces": list(interfaces.values()),
        "neighbors": neighbors,
        "routing": {},
    }


def _parse_junos_curly(text: str) -> dict:
    """Junos curly-brace-format parser (heuristic, adequate for hostname,
    interfaces, addresses, descriptions)."""
    hostname = ""
    interfaces: dict[str, dict] = {}

    m = re.search(r"host-name\s+(\S+);", text)
    if m:
        hostname = m.group(1)

    # interfaces { <name> { ... unit X { family inet { address A/B; } } } }
    iface_blocks = re.finditer(
        r"^\s*([\w\-/]+)\s*{([^{}]*(?:{[^{}]*}[^{}]*)*)}",
        _slice_section(text, "interfaces"),
        re.MULTILINE,
    )
    for blk in iface_blocks:
        name = blk.group(1)
        body = blk.group(2)
        iface: dict = {"name": name}
        d = re.search(r"description\s+\"?([^\";]+)\"?;", body)
        if d:
            iface["description"] = d.group(1).strip()
        addr = re.search(r"address\s+([\d.]+)/(\d+)", body)
        if addr:
            iface["ip"] = addr.group(1)
            iface["mask"] = addr.group(2)
        v = re.search(r"vlan-id\s+(\d+);", body)
        if v:
            iface["vlan"] = v.group(1)
        interfaces[name] = iface

    return {
        "hostname": hostname,
        "model": "",
        "os": "junos",
        "interfaces": list(interfaces.values()),
        "neighbors": [],
        "routing": {},
    }


def _slice_section(text: str, name: str) -> str:
    """Cut out the contents of a top-level Junos ``name { ... }`` block."""
    m = re.search(rf"\b{re.escape(name)}\s*{{", text)
    if not m:
        return ""
    start = m.end()
    depth = 1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return text[start:]


# ── Helpers ───────────────────────────────────────────────────────────


def _safe(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s).strip("_") or "node"


def _mask_to_cidr(mask: str) -> str:
    """Convert dotted-quad netmask to CIDR length, or pass through if already
    CIDR or shorthand."""
    if mask.isdigit():
        return mask
    try:
        return str(ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen)
    except ValueError:
        return mask


def _classify_device(d: dict) -> str:
    """Heuristic device-type classifier from hostname + interface names."""
    name = (d.get("hostname") or "").lower()
    if any(k in name for k in ("fw", "firewall", "asa", "palo", "srx")):
        return "firewall"
    if any(k in name for k in ("rtr", "router", "edge", "core-r", "isr")):
        return "router"
    if any(k in name for k in ("sw", "switch", "nexus", "tor", "leaf", "spine")):
        return "switch"
    if any(k in name for k in ("server", "srv", "host", "vm")):
        return "server"
    # Fallback: count interface name prefixes
    ifnames = [i.get("name", "").lower() for i in d.get("interfaces", [])]
    if any(n.startswith(("ge-", "xe-", "et-")) for n in ifnames):
        return "router"  # Junos default
    if any("vlan" in n for n in ifnames):
        return "switch"
    return "unknown"


def _count_methods(edges: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in edges:
        m = (e.get("properties") or {}).get("discovery", "unknown")
        counts[m] = counts.get(m, 0) + 1
    return counts


# ── CLI ────────────────────────────────────────────────────────────────


def _cli() -> int:
    p = argparse.ArgumentParser(
        description="Parse Cisco/Juniper configs into a topology graph",
    )
    p.add_argument("--file", help="Single config file")
    p.add_argument("--dir", help="Directory of config files")
    p.add_argument("--output", help="Write graph to this file (.json/.drawio/.svg)")
    p.add_argument("--auto-layout", action="store_true",
                   help="Apply hierarchical auto-layout before output")
    p.add_argument("--json", action="store_true", help="Print JSON to stdout")
    args = p.parse_args()

    if not args.file and not args.dir:
        p.error("--file or --dir required")

    devices = ([import_config(args.file)] if args.file
               else import_directory(args.dir))
    graph = synthesize_topology(devices)

    if args.auto_layout:
        from tools.network.layout import auto_layout
        auto_layout(graph)

    if args.output:
        out = Path(args.output)
        if out.suffix == ".drawio":
            from tools.network.export_import import to_drawio
            out.write_text(to_drawio(graph, out.stem), encoding="utf-8", newline="")
        elif out.suffix == ".svg":
            from tools.network.export_import import to_svg
            out.write_text(to_svg(graph, out.stem), encoding="utf-8", newline="")
        elif out.suffix == ".vsdx":
            from tools.network.visio_export import export_vsdx
            export_vsdx(graph, str(out))
        else:
            out.write_text(json.dumps(graph, indent=2), encoding="utf-8", newline="")
        print(f"wrote {out}", file=sys.stderr)

    if args.json or not args.output:
        print(json.dumps(graph, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
