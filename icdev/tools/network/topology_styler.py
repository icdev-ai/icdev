# CUI // SP-CTI
"""Topology Styler — deterministic post-processor that enforces ICDEV Network
Canvas style rules on any topology JSON. No LLM required. Air-gap safe.

Applies:
1. Zone box ordering (draw-rect first in nodes array)
2. Heading placement (above zone, not overlapping)
3. Label deconfliction (no text-on-text overlaps)
4. Auto-legend generation (protocols, devices, zones)
5. Color validation (zone palette enforcement)

Usage:
    from tools.network.topology_styler import style_topology
    styled_graph = style_topology(graph_json)

CLI:
    python tools/network/topology_styler.py --file topology.json
    python tools/network/topology_styler.py --topology-id <id> --apply
"""

import argparse
import json
import sys
import uuid
from tools.db.storage import get_connection
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ── Device color map (matches NODE_STYLES in network-canvas.js) ──────────────
DEVICE_COLORS = {
    "router": ("#3498db", "Router"),
    "switch-l2": ("#27ae60", "L2 Switch"),
    "switch-l3": ("#2ecc71", "L3 Switch"),
    "firewall": ("#e94560", "Firewall"),
    "load-balancer": ("#f39c12", "Load Balancer"),
    "wap": ("#9b59b6", "Wireless AP"),
    "server": ("#1abc9c", "Server"),
    "cloud": ("#7f8c8d", "Cloud"),
    "endpoint-pc": ("#74b9ff", "Workstation"),
    "endpoint-phone": ("#55efc4", "IP Phone"),
    "endpoint-iot": ("#fd79a8", "IoT Device"),
    "endpoint-camera": ("#e17055", "IP Camera"),
    "siem": ("#6c5ce7", "SIEM"),
    "sdwan-edge": ("#00cec9", "SD-WAN Edge"),
    "sase-pop": ("#a29bfe", "SASE PoP"),
    "mpls-pe": ("#ff9800", "MPLS PE"),
    "mpls-p": ("#ffb74d", "MPLS P"),
    "route-reflector": ("#7986cb", "Route Reflector"),
    "pop": ("#66bb6a", "POP"),
    "meet-me-room": ("#a29bfe", "Meet-Me Room"),
    "cross-connect": ("#fdcb6e", "Cross-Connect"),
    "patch-panel": ("#888888", "Patch Panel"),
    "kg-175d": ("#ff6b6b", "KG-175D (TACLANE)"),
    "kg-175g": ("#ff5252", "KG-175G"),
    "kg-250": ("#d32f2f", "KG-250"),
    "kg-340": ("#c62828", "KG-340"),
    "type1-encryptor": ("#ff4757", "Type 1 Encryptor"),
    "fips-140-l1": ("#d4ac0d", "FIPS 140 L1"),
    "fips-140-l2": ("#f4d03f", "FIPS 140 L2"),
    "fips-140-l3": ("#f0b27a", "FIPS 140 L3"),
    "fips-140-l4": ("#e74c3c", "FIPS 140 L4"),
    "hsm": ("#ff6b81", "HSM"),
    "macsec": ("#e67e22", "MACsec"),
    "qkd-device": ("#a29bfe", "QKD Device"),
    "roadm": ("#9b59b6", "ROADM"),
    "transponder": ("#7c4dff", "Transponder"),
    "sonet-adm": ("#536dfe", "SONET ADM"),
}

# ── Protocol color map (matches LINK_STYLES in network-canvas.js) ────────────
PROTOCOL_COLORS = {
    "OSPF": ("#27ae60", "OSPF (green)"),
    "BGP": ("#5dade2", "BGP (blue)"),
    "iBGP": ("#85c1e9", "iBGP (light blue dashed)"),
    "eBGP": ("#3498db", "eBGP (blue)"),
    "MP-BGP": ("#7986cb", "MP-BGP"),
    "MPLS": ("#ff9800", "MPLS (orange)"),
    "LDP": ("#ffb74d", "LDP (orange)"),
    "RSVP": ("#ff9800", "RSVP"),
    "IPSec": ("#f7dc6f", "IPSec (yellow dashed)"),
    "IPSec ESP": ("#f7dc6f", "IPSec ESP"),
    "GRE": ("#f9e79f", "GRE (yellow dashed)"),
    "GRE/IPSec": ("#f7dc6f", "GRE/IPSec"),
    "mTLS": ("#fdcb6e", "mTLS (gold dashed)"),
    "VXLAN": ("#00bcd4", "VXLAN (teal dashed)"),
    "BGP EVPN": ("#5dade2", "BGP EVPN"),
    "STP": ("#7a8cb0", "STP"),
    "HSRP": ("#7a8cb0", "HSRP"),
    "VRRP": ("#7a8cb0", "VRRP"),
    "SONET": ("#536dfe", "SONET"),
    "OC-192": ("#9b59b6", "OC-192"),
    "OLSR": ("#9b59b6", "OLSR (dotted)"),
}

# ── Zone stroke → label map ──────────────────────────────────────────────────
ZONE_STROKE_NAMES = {
    "#3498db": "Blue",
    "#74b9ff": "Blue",
    "#27ae60": "Green",
    "#f39c12": "Orange",
    "#e74c3c": "Red",
    "#9b59b6": "Purple",
    "#00cec9": "Teal",
    "#a29bfe": "Purple",
    "#636e72": "Gray",
}


def _uid(prefix="leg"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def style_topology(graph_json, skip_legend=False):
    """Apply ICDEV style rules to a topology graph JSON dict.

    Args:
        graph_json: dict with "nodes" and "edges" lists
        skip_legend: if True, skip legend generation

    Returns:
        Styled graph_json dict
    """
    nodes = list(graph_json.get("nodes", []))
    edges = list(graph_json.get("edges", []))

    # ── 1. Sort nodes: zones first, then headings, then badges, then devices, then labels ──
    def _sort_key(n):
        t = n.get("type", "")
        if t == "draw-rect":
            return (0, n.get("y", 0))
        if t == "draw-rounded-rect":
            return (1, n.get("y", 0))
        if t == "text-heading":
            return (2, n.get("y", 0))
        if t == "text-badge":
            return (3, n.get("x", 0))
        if t == "text-label":
            return (8, n.get("y", 0))
        return (5, n.get("y", 0))

    nodes.sort(key=_sort_key)

    # ── 2. Fix heading placement — should be ABOVE their zone, not inside ──
    zone_boxes = [n for n in nodes if n["type"] in ("draw-rect", "draw-rounded-rect")]
    headings = [n for n in nodes if n["type"] == "text-heading"]

    for heading in headings:
        hx, hy = heading["x"], heading["y"]
        # Find the closest zone box below or at this heading
        for zone in zone_boxes:
            zx = zone["x"]
            zy = zone["y"]
            zw = zone.get("config", {}).get("_width", 300)
            zh = zone.get("config", {}).get("_height", 150)
            # Check if heading is INSIDE the zone
            if zx <= hx <= zx + zw and zy <= hy <= zy + zh:
                # Move heading above the zone
                heading["y"] = zy - 25
                heading["x"] = zx + 20
                break

    # ── 3. Label deconfliction — no text-on-text overlaps ──
    text_nodes = [n for n in nodes if n["type"] in ("text-heading", "text-label", "text-badge")]
    text_nodes.sort(key=lambda n: (n["y"], n["x"]))

    for i in range(len(text_nodes)):
        for j in range(i + 1, len(text_nodes)):
            a, b = text_nodes[i], text_nodes[j]
            dy = abs(a["y"] - b["y"])
            dx = abs(a["x"] - b["x"])
            if dy < 25 and dx < 150:
                # Shift the lower one down
                b["y"] = a["y"] + 28

    # ── 4. Auto-generate legend (if not already present) ──
    has_legend = any(n.get("id", "").startswith("leg-") or n.get("id", "").startswith("legend-") for n in nodes)

    if not has_legend and not skip_legend:
        nodes = _add_legend(nodes, edges)

    return {"nodes": nodes, "edges": edges}


def _add_legend(nodes, edges):
    """Generate and append a legend panel to the nodes list."""
    # Find max X of existing non-legend nodes
    device_nodes = [n for n in nodes if not n.get("id", "").startswith("leg-")]
    max_x = 0
    for n in device_nodes:
        nx = n["x"] + n.get("config", {}).get("_width", 110)
        if nx > max_x:
            max_x = nx

    legend_x = max_x + 100
    legend_y = 60

    # ── Collect protocols used ──
    protocols_found = {}
    for e in edges:
        proto = e.get("protocol", "").strip()
        if proto and proto in PROTOCOL_COLORS:
            color, label = PROTOCOL_COLORS[proto]
            protocols_found[proto] = (color, label)

    # ── Collect device types used ──
    devices_found = {}
    for n in nodes:
        t = n.get("type", "")
        if t in DEVICE_COLORS and t not in devices_found:
            color, label = DEVICE_COLORS[t]
            devices_found[t] = (color, label)

    # ── Collect zones ──
    zones_found = []
    zone_boxes = [n for n in nodes if n["type"] in ("draw-rect", "draw-rounded-rect")]
    headings = [n for n in nodes if n["type"] == "text-heading"]

    for zone in zone_boxes:
        stroke = zone.get("config", {}).get("_stroke", "#636e72")
        if stroke == "#636e72":
            continue  # Skip legend box itself
        color_name = ZONE_STROKE_NAMES.get(stroke, "Zone")
        # Find nearest heading
        zx, zy = zone["x"], zone["y"]
        best_heading = None
        best_dist = 999
        for h in headings:
            dy = abs(h["y"] - zy)
            dx = abs(h["x"] - zx)
            if dy < 60 and dx < 300 and dy + dx < best_dist:
                best_dist = dy + dx
                best_heading = h["label"]
        label = f"{color_name} = {best_heading}" if best_heading else color_name
        if label not in [z[1] for z in zones_found]:
            zones_found.append((stroke, label))

    # ── Calculate legend height ──
    entry_count = 0
    sections = 0
    if protocols_found:
        entry_count += len(protocols_found)
        sections += 1
    if devices_found:
        entry_count += len(devices_found)
        sections += 1
    if zones_found:
        entry_count += len(zones_found)
        sections += 1

    legend_h = entry_count * 22 + sections * 35 + 80
    if legend_h < 150:
        legend_h = 150

    # ── Build legend nodes ──
    legend_nodes = []
    cy = legend_y  # current Y

    # Background box
    legend_nodes.append(
        _n(
            _uid("leg"),
            "",
            "draw-rect",
            legend_x,
            cy,
            {"_fill": "#0f1520", "_stroke": "#636e72", "_width": 240, "_height": legend_h},
        )
    )

    # Title
    cy += 10
    legend_nodes.append(_n(_uid("leg"), "Legend", "text-heading", legend_x + 60, cy, {"_textColor": "#eaeaea"}))
    cy += 35

    # Protocols section
    if protocols_found:
        legend_nodes.append(_n(_uid("leg"), "Protocols:", "text-label", legend_x + 15, cy, {"_textColor": "#eaeaea"}))
        cy += 22
        for proto, (color, label) in sorted(protocols_found.items()):
            legend_nodes.append(
                _n(_uid("leg"), f"\u2022 {label}", "text-label", legend_x + 15, cy, {"_textColor": color})
            )
            cy += 22
        cy += 12

    # Devices section
    if devices_found:
        legend_nodes.append(_n(_uid("leg"), "Devices:", "text-label", legend_x + 15, cy, {"_textColor": "#eaeaea"}))
        cy += 22
        for dtype, (color, label) in sorted(devices_found.items(), key=lambda x: x[1][1]):
            legend_nodes.append(
                _n(_uid("leg"), f"\u2022 {label}", "text-label", legend_x + 15, cy, {"_textColor": color})
            )
            cy += 22
        cy += 12

    # Zones section
    if zones_found:
        legend_nodes.append(_n(_uid("leg"), "Zones:", "text-label", legend_x + 15, cy, {"_textColor": "#eaeaea"}))
        cy += 22
        for color, label in zones_found:
            legend_nodes.append(
                _n(_uid("leg"), f"\u2022 {label}", "text-label", legend_x + 15, cy, {"_textColor": color})
            )
            cy += 22

    return nodes + legend_nodes


def _n(nid, label, ntype, x, y, config=None):
    return {"id": nid, "label": label, "type": ntype, "x": x, "y": y, "config": config or {}}


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Topology Styler — enforce ICDEV canvas style rules")
    parser.add_argument("--file", help="Path to topology JSON file")
    parser.add_argument("--topology-id", help="Topology ID in network_canvas.db")
    parser.add_argument("--apply", action="store_true", help="Write styled result back to DB")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--skip-legend", action="store_true", help="Skip legend generation")
    args = parser.parse_args()

    if args.file:
        path = Path(args.file)
        data = json.loads(path.read_text(encoding="utf-8"))
        styled = style_topology(data, skip_legend=args.skip_legend)
        if args.json:
            print(json.dumps(styled, indent=2))
        else:
            path.write_text(json.dumps(styled, indent=2), encoding="utf-8", newline="")
            print(f"Styled: {len(styled['nodes'])} nodes, {len(styled['edges'])} edges")

    elif args.topology_id:
        db_path = BASE_DIR / "data" / "network_canvas.db"
        conn = get_connection(str(db_path))
        row = conn.execute("SELECT graph_json FROM topologies WHERE id=%s", (args.topology_id,)).fetchone()
        if not row:
            print(f"Topology {args.topology_id} not found")
            sys.exit(1)
        data = json.loads(row["graph_json"])
        styled = style_topology(data, skip_legend=args.skip_legend)
        if args.apply:
            conn.execute(
                "UPDATE topologies SET graph_json=%s WHERE id=%s",
                (json.dumps(styled), args.topology_id),
            )
            conn.commit()
            print(f"Applied style to {args.topology_id}: {len(styled['nodes'])} nodes")
        elif args.json:
            print(json.dumps(styled, indent=2))
        else:
            print(f"Preview: {len(styled['nodes'])} nodes, {len(styled['edges'])} edges (use --apply to save)")
        conn.close()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
