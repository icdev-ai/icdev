#!/usr/bin/env python3
"""Update all 23 original network canvas templates with zone boxes,
heading labels, badges, and cross-connect nodes.

Idempotent — checks for existing zone nodes before adding.

Usage:
    python tools/network/update_template_zones.py
"""

import json
import sys
from tools.db.storage import get_connection
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "network_canvas.db"

# ---------------------------------------------------------------------------
# Color palette helpers
# ---------------------------------------------------------------------------
COLORS = {
    "blue": {"fill": "#0a1628", "stroke": "#74b9ff", "text": "#74b9ff"},
    "green": {"fill": "#0a180a", "stroke": "#27ae60", "text": "#27ae60"},
    "orange": {"fill": "#1a1500", "stroke": "#f39c12", "text": "#f39c12"},
    "red": {"fill": "#1a0a0a", "stroke": "#e74c3c", "text": "#e74c3c"},
    "purple": {"fill": "#120a20", "stroke": "#9b59b6", "text": "#9b59b6"},
    "teal": {"fill": "#0a1a1a", "stroke": "#00cec9", "text": "#00cec9"},
}


def _zone(tpl_short: str, name: str, color: str, x: int, y: int, w: int, h: int) -> dict:
    """Create a draw-rect zone node."""
    c = COLORS[color]
    safe = name.lower().replace(" ", "-").replace("/", "-").replace("(", "").replace(")", "")
    return {
        "id": f"zone-{tpl_short}-{safe}",
        "label": name,
        "type": "draw-rect",
        "x": x,
        "y": y,
        "config": {
            "_fill": c["fill"],
            "_stroke": c["stroke"],
            "_width": w,
            "_height": h,
            "_fillOpacity": 0.6,
        },
    }


def _heading(tpl_short: str, text: str, color: str, x: int, y: int) -> dict:
    """Create a text-heading node."""
    c = COLORS[color]
    safe = text.lower().replace(" ", "-").replace("/", "-").replace("(", "").replace(")", "")
    return {
        "id": f"heading-{tpl_short}-{safe}",
        "label": text,
        "type": "text-heading",
        "x": x,
        "y": y,
        "config": {"_textColor": c["text"]},
    }


def _badge(tpl_short: str, text: str, x: int, y: int) -> dict:
    """Create a text-badge node."""
    safe = text.lower().replace(" ", "-").replace("/", "-").replace("(", "").replace(")", "")
    return {
        "id": f"badge-{tpl_short}-{safe}",
        "label": text,
        "type": "text-badge",
        "x": x,
        "y": y,
        "config": {"_fill": "#0f3460", "_stroke": "#4a9eff"},
    }


def _label(tpl_short: str, text: str, color: str, x: int, y: int) -> dict:
    """Create a text-label node."""
    c = COLORS[color]
    safe = text.lower().replace(" ", "-").replace("/", "-").replace("(", "").replace(")", "")
    return {
        "id": f"label-{tpl_short}-{safe}",
        "label": text,
        "type": "text-label",
        "x": x,
        "y": y,
        "config": {"_textColor": c["text"]},
    }


def _xconn(node_id: str, label: str, x: int, y: int) -> dict:
    """Create a cross-connect device node."""
    return {
        "id": node_id,
        "label": label,
        "type": "cross-connect",
        "x": x,
        "y": y,
        "config": {},
    }


def _edge(eid: str, src: str, tgt: str, label: str = "", protocol: str = "") -> dict:
    return {"id": eid, "source": src, "target": tgt, "label": label, "protocol": protocol}


def _shift_existing(nodes: list[dict], dx: int = 0, dy: int = 0) -> None:
    """Shift all non-zone/non-heading/non-badge/non-label existing nodes."""
    draw_types = {"draw-rect", "draw-rounded-rect", "text-heading", "text-label", "text-badge"}
    for n in nodes:
        if n["type"] not in draw_types:
            n["x"] += dx
            n["y"] += dy


# ---------------------------------------------------------------------------
# Per-template zone definitions
# ---------------------------------------------------------------------------


def zones_gre_ipsec(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "HQ Site", "blue", 40, 60, 320, 120),
            _zone(short, "WAN / ISP", "orange", 440, 60, 160, 120),
            _zone(short, "Branch Site", "green", 640, 60, 330, 120),
            _heading(short, "HQ Site", "blue", 120, 40),
            _heading(short, "Branch Site", "green", 730, 40),
            _badge(short, "GRE/IPSec", 440, 10),
        ],
        [],
        True,
    )  # shift_down=True


def zones_three_tier(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Core Layer", "red", 340, 60, 330, 100),
            _zone(short, "Distribution Layer", "orange", 140, 200, 730, 110),
            _zone(short, "Access Layer", "green", 40, 360, 930, 110),
            _heading(short, "Core Layer", "red", 430, 40),
            _heading(short, "Distribution Layer", "orange", 380, 180),
            _heading(short, "Access Layer", "green", 380, 340),
        ],
        [],
        True,
    )


def zones_spine_leaf(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Spine Tier", "red", 240, 60, 430, 90),
            _zone(short, "Leaf Tier", "orange", 40, 230, 740, 110),
            _zone(short, "Server Compute", "green", 40, 400, 740, 110),
            _badge(short, "BGP EVPN/VXLAN", 380, 10),
        ],
        [],
        True,
    )


def zones_sdwan(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Orchestration", "purple", 240, 20, 530, 100),
            _zone(short, "Control Plane", "blue", 240, 140, 530, 100),
            _zone(short, "Transport", "orange", 240, 310, 530, 100),
            _zone(short, "Branch Sites", "green", 140, 400, 730, 100),
        ],
        [],
        False,
    )


def zones_mpls_l3vpn(short: str) -> tuple[list, list, bool]:
    nodes = [
        _zone(short, "Customer A", "green", 20, 150, 120, 300),
        _zone(short, "POP-1 West", "blue", 160, 170, 250, 190),
        _zone(short, "MPLS Core", "orange", 460, 110, 150, 380),
        _zone(short, "POP-2 East", "blue", 650, 170, 250, 190),
        _zone(short, "Customer B", "green", 920, 150, 120, 300),
        _badge(short, "RFC 4364", 460, 10),
        _xconn(f"xconn-{short}-pop1", "XConn POP-1", 220, 260),
        _xconn(f"xconn-{short}-pop2", "XConn POP-2", 820, 260),
    ]
    edges = [
        _edge(f"e-xconn-{short}-pop1-fpp", f"xconn-{short}-pop1", "fpp1", "Patch", ""),
        _edge(f"e-xconn-{short}-pop1-pe", f"xconn-{short}-pop1", "pe1", "Patch", ""),
        _edge(f"e-xconn-{short}-pop2-fpp", f"xconn-{short}-pop2", "fpp2", "Patch", ""),
        _edge(f"e-xconn-{short}-pop2-pe", f"xconn-{short}-pop2", "pe2", "Patch", ""),
    ]
    return nodes, edges, False


def zones_sonet_dwdm(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "POP-A Hub", "red", 340, 10, 300, 260),
            _zone(short, "POP-B", "blue", 690, 260, 250, 160),
            _zone(short, "POP-C", "green", 430, 420, 250, 160),
            _zone(short, "POP-D", "orange", 80, 260, 230, 160),
            _badge(short, "BLSR Ring", 450, 580),
        ],
        [],
        False,
    )


def zones_sdn_openflow(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Control Plane", "purple", 290, 40, 380, 100),
            _zone(short, "Data Plane", "blue", 90, 230, 730, 110),
            _zone(short, "Hosts", "green", 90, 390, 730, 110),
            _badge(short, "OpenFlow 1.3", 430, 10),
        ],
        [],
        False,
    )


def zones_zero_trust(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Identity and Policy", "purple", 240, 20, 530, 230),
            _zone(short, "Policy Enforcement", "orange", 140, 290, 730, 110),
            _zone(short, "Resources", "green", 140, 430, 730, 110),
            _badge(short, "NIST 800-207", 430, 550),
        ],
        [],
        False,
    )


def zones_qkd_p2p(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Site A Alice", "blue", 80, 190, 200, 370),
            _zone(short, "Quantum Channel", "purple", 380, 190, 180, 250),
            _zone(short, "Site B Bob", "green", 680, 190, 200, 370),
            _badge(short, "BB84 QKD", 420, 10),
        ],
        [],
        False,
    )


def zones_qkd_relay(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "QKD Key Network", "purple", 240, 80, 540, 100),
            _zone(short, "Trusted Relay Chain", "blue", 40, 230, 920, 110),
            _badge(short, "Trusted-Node Relay", 400, 10),
        ],
        [],
        False,
    )


def zones_aiml_fabric(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "400G Spine", "red", 230, 40, 440, 100),
            _zone(short, "100G Leaf Tier", "orange", 30, 230, 750, 110),
            _zone(short, "GPU Compute and Storage", "green", 30, 410, 950, 110),
            _badge(short, "RDMA/RoCE v2", 400, 10),
        ],
        [],
        False,
    )


def zones_security_zones(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Internet Untrust", "red", 430, 10, 160, 90),
            _zone(short, "DMZ", "orange", 230, 200, 550, 220),
            _zone(short, "Trusted Application", "green", 280, 510, 450, 110),
            _zone(short, "Management", "purple", 830, 310, 160, 220),
            _badge(short, "Multi-Zone DMZ", 430, 630),
        ],
        [],
        False,
    )


def zones_campus_area(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "WAN Edge", "red", 380, 10, 300, 160),
            _zone(short, "Core", "orange", 330, 180, 450, 80),
            _zone(short, "Bldg-A", "blue", 20, 300, 260, 340),
            _zone(short, "Bldg-B", "blue", 300, 300, 260, 340),
            _zone(short, "Bldg-C", "blue", 600, 300, 260, 340),
            _zone(short, "Data Center", "green", 880, 70, 160, 340),
            _heading(short, "WAN Edge", "red", 470, 170),
            _heading(short, "Bldg-A", "blue", 100, 280),
            _heading(short, "Bldg-B", "blue", 380, 280),
            _heading(short, "Bldg-C", "blue", 680, 280),
            _heading(short, "Data Center", "green", 900, 50),
        ],
        [],
        True,
    )


def zones_tactical_ddil(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "SATCOM Uplink", "purple", 430, 10, 200, 160),
            _zone(short, "TOC", "blue", 280, 190, 450, 200),
            _zone(short, "MANET Mesh", "orange", 120, 420, 790, 90),
            _zone(short, "Forward Teams", "green", 40, 530, 600, 130),
            _zone(short, "ISR Sensors", "red", 860, 420, 120, 240),
            _badge(short, "DDIL Environment", 400, 670),
        ],
        [],
        False,
    )


def zones_east_west_fabric(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "N-S Border", "red", 20, 10, 280, 210),
            _zone(short, "Super-Spine", "purple", 310, 10, 680, 70),
            _zone(short, "Pod A Compute", "blue", 130, 130, 380, 330),
            _zone(short, "Pod B Storage", "green", 530, 130, 380, 330),
            _badge(short, "5-Stage Clos", 500, 480),
        ],
        [],
        False,
    )


def zones_microseg(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Spine Fabric", "purple", 290, 10, 430, 70),
            _zone(short, "Web Tier", "blue", 20, 160, 300, 430),
            _zone(short, "App Tier", "orange", 360, 160, 300, 430),
            _zone(short, "Data Tier", "green", 700, 160, 300, 430),
            _badge(short, "Distributed FW", 430, 600),
        ],
        [],
        False,
    )


def zones_hyperseg(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Ingress", "red", 380, 10, 160, 140),
            _zone(short, "Control Plane", "purple", 30, 70, 200, 280),
            _zone(short, "Service Mesh", "blue", 250, 170, 530, 360),
            _zone(short, "Data Stores", "green", 330, 540, 340, 220),
            _badge(short, "Service Mesh + SPIFFE", 400, 770),
        ],
        [],
        False,
    )


def zones_man(short: str) -> tuple[list, list, bool]:
    nodes = [
        _zone(short, "Metro Ring", "purple", 250, 20, 520, 440),
        _zone(short, "HQ Site", "blue", 10, 40, 180, 130),
        _zone(short, "DC Site", "green", 910, 40, 130, 130),
        _zone(short, "Colo", "orange", 910, 270, 130, 180),
        _zone(short, "Branch", "teal", 10, 270, 130, 180),
        _badge(short, "FIPS 140 Encrypted", 380, 470),
        # Cross-connects at each site handoff
        _xconn(f"xconn-{short}-hq", "XConn HQ", 200, 135),
        _xconn(f"xconn-{short}-dc", "XConn DC", 800, 135),
        _xconn(f"xconn-{short}-colo", "XConn Colo", 800, 325),
        _xconn(f"xconn-{short}-branch", "XConn Branch", 200, 325),
    ]
    edges = [
        # HQ: fiber -> xconn -> fips encryptor
        _edge(f"e-xconn-{short}-hq-fiber", "fiber-hq", f"xconn-{short}-hq", "Patch", ""),
        _edge(f"e-xconn-{short}-hq-fips", f"xconn-{short}-hq", "fips-hq", "Patch", ""),
        # DC
        _edge(f"e-xconn-{short}-dc-fiber", "fiber-dc", f"xconn-{short}-dc", "Patch", ""),
        _edge(f"e-xconn-{short}-dc-fips", f"xconn-{short}-dc", "fips-dc", "Patch", ""),
        # Colo
        _edge(f"e-xconn-{short}-colo-fiber", "fiber-colo", f"xconn-{short}-colo", "Patch", ""),
        _edge(f"e-xconn-{short}-colo-fips", f"xconn-{short}-colo", "fips-colo", "Patch", ""),
        # Branch
        _edge(f"e-xconn-{short}-branch-fiber", "fiber-branch", f"xconn-{short}-branch", "Patch", ""),
        _edge(f"e-xconn-{short}-branch-fips", f"xconn-{short}-branch", "fips-branch", "Patch", ""),
    ]
    return nodes, edges, False


def zones_wan(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Hub-A Primary DC", "blue", 120, 10, 370, 200),
            _zone(short, "Hub-B DR Site", "green", 520, 10, 370, 200),
            _zone(short, "WAN Transport", "orange", 240, 220, 540, 160),
            _zone(short, "Branch Sites", "teal", 40, 400, 610, 230),
            _zone(short, "Remote Tactical", "red", 840, 340, 130, 280),
            _badge(short, "Multi-Transport WAN", 400, 640),
        ],
        [],
        False,
    )


def zones_sonet_ring(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Central Office Hub", "red", 280, 10, 350, 150),
            _zone(short, "BLSR Backbone", "blue", 130, 120, 660, 410),
            _zone(short, "Access Ring A", "green", 840, 160, 230, 310),
            _zone(short, "Access Ring B", "orange", -90, 160, 200, 310),
            _badge(short, "BLSR + UPSR", 420, 540),
        ],
        [],
        False,
    )


def zones_aws_dx_can(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "AWS Cloud", "orange", 280, 20, 600, 350),
            _zone(short, "Meet-Me Rooms", "purple", 180, 520, 660, 200),
            _zone(short, "Campus Network", "green", 180, 870, 700, 580),
            _heading(short, "AWS Cloud", "orange", 500, 375),
            _heading(short, "Meet-Me Rooms", "purple", 420, 500),
            _heading(short, "Campus Network", "green", 440, 850),
            _badge(short, "AWS Direct Connect", 420, 1460),
        ],
        [],
        False,
    )


def zones_azure_er_man(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "Azure Cloud", "blue", 230, 20, 560, 380),
            _zone(short, "Meet-Me Rooms", "purple", 180, 510, 660, 200),
            _zone(short, "Metro Area Network", "green", 130, 700, 760, 420),
            _badge(short, "Azure ExpressRoute", 420, 1130),
        ],
        [],
        False,
    )


def zones_multicloud_dual_pop(short: str) -> tuple[list, list, bool]:
    return (
        [
            _zone(short, "AWS Cloud", "orange", 40, 20, 360, 340),
            _zone(short, "Azure Cloud", "blue", 600, 20, 360, 340),
            _zone(short, "West POP", "purple", 40, 420, 430, 310),
            _zone(short, "East POP", "purple", 530, 420, 430, 310),
            _zone(short, "MPLS Backbone", "orange", 180, 770, 650, 110),
            _zone(short, "Metro Sites", "green", 180, 930, 650, 260),
            _badge(short, "Multi-Cloud", 430, 1200),
        ],
        [],
        False,
    )


# ---------------------------------------------------------------------------
# Template ID -> (short name, zone builder function)
# ---------------------------------------------------------------------------
TEMPLATE_MAP: dict[str, tuple[str, callable]] = {
    "tpl-gre-ipsec": ("gre", zones_gre_ipsec),
    "tpl-three-tier": ("3tier", zones_three_tier),
    "tpl-spine-leaf": ("spine", zones_spine_leaf),
    "tpl-sdwan": ("sdwan", zones_sdwan),
    "tpl-mpls-l3vpn": ("mpls", zones_mpls_l3vpn),
    "tpl-sonet-dwdm": ("dwdm", zones_sonet_dwdm),
    "tpl-sdn-openflow": ("sdn", zones_sdn_openflow),
    "tpl-zero-trust": ("zt", zones_zero_trust),
    "tpl-qkd-p2p": ("qkdp2p", zones_qkd_p2p),
    "tpl-qkd-relay": ("qkdrelay", zones_qkd_relay),
    "tpl-aiml-fabric": ("aiml", zones_aiml_fabric),
    "tpl-security-zones": ("seczones", zones_security_zones),
    "tpl-campus-area": ("campus", zones_campus_area),
    "tpl-tactical-ddil": ("tactical", zones_tactical_ddil),
    "tpl-east-west-fabric": ("ewfabric", zones_east_west_fabric),
    "tpl-microseg": ("microseg", zones_microseg),
    "tpl-hyperseg": ("hyperseg", zones_hyperseg),
    "tpl-man": ("man", zones_man),
    "tpl-wan": ("wan", zones_wan),
    "tpl-sonet-ring": ("sonetring", zones_sonet_ring),
    "tpl-aws-dx-can": ("awsdx", zones_aws_dx_can),
    "tpl-azure-er-man": ("azureer", zones_azure_er_man),
    "tpl-multicloud-dual-pop": ("multicloud", zones_multicloud_dual_pop),
}


def _existing_zone_ids(nodes: list[dict]) -> set[str]:
    """Return IDs of any zone/heading/badge/label/xconn nodes already present."""
    prefixes = ("zone-", "heading-", "badge-", "label-", "xconn-")
    return {n["id"] for n in nodes if any(n["id"].startswith(p) for p in prefixes)}


def main() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = get_connection(str(DB_PATH))
    cur = conn.cursor()

    updated = 0
    skipped = 0

    for tpl_id, (short, builder) in TEMPLATE_MAP.items():
        cur.execute("SELECT graph_json FROM nc_templates WHERE id = %s", (tpl_id,))
        row = cur.fetchone()
        if row is None:
            print(f"  SKIP  {tpl_id} — not found in DB")
            skipped += 1
            continue

        graph = json.loads(row[0])
        existing_ids = _existing_zone_ids(graph["nodes"])

        new_nodes, new_edges, shift_down = builder(short)

        # Filter out already-present nodes (idempotency)
        to_add = [n for n in new_nodes if n["id"] not in existing_ids]

        # Filter edges that don't already exist
        existing_edge_ids = {e["id"] for e in graph["edges"]}
        edges_to_add = [e for e in new_edges if e["id"] not in existing_edge_ids]

        if not to_add and not edges_to_add:
            print(f"  SKIP  {tpl_id} — zones already present")
            skipped += 1
            continue

        # Shift existing device nodes if requested (only on first run)
        if shift_down and to_add:
            _shift_existing(graph["nodes"], dx=0, dy=60)

        # Prepend zone/heading/badge nodes so they render behind devices
        graph["nodes"] = to_add + graph["nodes"]
        graph["edges"].extend(edges_to_add)

        updated_json = json.dumps(graph, ensure_ascii=False)
        cur.execute(
            "UPDATE nc_templates SET graph_json = %s WHERE id = %s",
            (updated_json, tpl_id),
        )
        zone_count = sum(1 for n in to_add if n["type"] == "draw-rect")
        heading_count = sum(1 for n in to_add if n["type"] == "text-heading")
        badge_count = sum(1 for n in to_add if n["type"] == "text-badge")
        xconn_count = sum(1 for n in to_add if n["type"] == "cross-connect")
        edge_count = len(edges_to_add)

        parts = []
        if zone_count:
            parts.append(f"{zone_count} zones")
        if heading_count:
            parts.append(f"{heading_count} headings")
        if badge_count:
            parts.append(f"{badge_count} badges")
        if xconn_count:
            parts.append(f"{xconn_count} xconns")
        if edge_count:
            parts.append(f"{edge_count} edges")
        detail = ", ".join(parts)
        shifted = " (nodes shifted +60y)" if shift_down else ""
        print(f"  OK    {tpl_id} — added {detail}{shifted}")
        updated += 1

    conn.commit()
    conn.close()

    print(f"\nDone: {updated} updated, {skipped} skipped.")


if __name__ == "__main__":
    main()
