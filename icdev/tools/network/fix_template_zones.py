"""
Fix template zone positions — recalculate zone boxes to actually encompass
the devices they're meant to contain. Also fix headings/badges positioning.
"""

import json
from tools.db.storage import get_connection
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "network_canvas.db"

# For each template, define which device node IDs belong to which zone,
# then recalculate the zone box to encompass those devices with padding.

PADDING = 20  # px padding around devices inside zone
DEVICE_W = 110  # default device width
DEVICE_H = 60  # default device height
HEADING_OFFSET_Y = -28  # heading placed above zone top


def get_bounds(nodes_list, all_nodes_map):
    """Get bounding box of a list of node IDs."""
    xs, ys = [], []
    for nid in nodes_list:
        n = all_nodes_map.get(nid)
        if not n:
            continue
        w = n.get("config", {}).get("_width", DEVICE_W)
        h = n.get("config", {}).get("_height", DEVICE_H)
        xs.append(n["x"])
        ys.append(n["y"])
        xs.append(n["x"] + w)
        ys.append(n["y"] + h)
    if not xs:
        return None
    return {
        "x": min(xs) - PADDING,
        "y": min(ys) - PADDING,
        "w": max(xs) - min(xs) + 2 * PADDING,
        "h": max(ys) - min(ys) + 2 * PADDING,
    }


# Zone definitions: template_id -> list of zone specs
# Each zone: (zone_node_id, heading_node_id, device_ids, color_scheme, badge_info)
# color_scheme: "blue", "green", "orange", "red", "purple", "teal"
COLORS = {
    "blue": {"_fill": "#0a1628", "_stroke": "#74b9ff", "text": "#74b9ff"},
    "green": {"_fill": "#0a180a", "_stroke": "#27ae60", "text": "#27ae60"},
    "orange": {"_fill": "#1a1500", "_stroke": "#f39c12", "text": "#f39c12"},
    "red": {"_fill": "#1a0a0a", "_stroke": "#e74c3c", "text": "#e74c3c"},
    "purple": {"_fill": "#120a20", "_stroke": "#9b59b6", "text": "#9b59b6"},
    "teal": {"_fill": "#0a1a1a", "_stroke": "#00cec9", "text": "#00cec9"},
}

TEMPLATE_ZONES = {
    "tpl-gre-ipsec": {
        "zones": [
            ("zone-hq", "HQ Site", ["r1", "fw1"], "blue"),
            ("zone-wan", "WAN / ISP", ["isp1"], "orange"),
            ("zone-branch", "Branch Site", ["fw2", "r2"], "green"),
        ],
        "badge": ("GRE/IPSec", "purple"),
    },
    "tpl-three-tier": {
        "zones": [
            ("zone-core", "Core Layer", ["core1", "core2"], "red"),
            ("zone-dist", "Distribution Layer", ["dist1", "dist2", "dist3"], "orange"),
            ("zone-access", "Access Layer", ["acc1", "acc2", "acc3", "acc4", "acc5", "acc6"], "green"),
        ],
        "badge": ("Three-Tier", "blue"),
    },
    "tpl-spine-leaf": {
        "zones": [
            ("zone-spine", "Spine Tier", ["sp1", "sp2"], "red"),
            ("zone-leaf", "Leaf Tier", ["lf1", "lf2", "lf3", "lf4"], "orange"),
            ("zone-server", "Server / Compute", ["srv1", "srv2", "srv3", "srv4"], "green"),
        ],
        "badge": ("BGP EVPN/VXLAN", "purple"),
    },
    "tpl-sdwan": {
        "zones": [
            ("zone-orch", "Orchestration", ["orch"], "purple"),
            ("zone-ctrl", "Control Plane", ["vsmart1", "vsmart2"], "blue"),
            ("zone-transport", "WAN Transport", ["mpls", "inet", "hub"], "orange"),
            ("zone-branch", "Branch Sites", ["br1", "br2", "br3"], "green"),
        ],
        "badge": ("SD-WAN", "teal"),
    },
    "tpl-mpls-l3vpn": {
        "zones": [
            ("zone-cust-a", "Customer A", ["ce-a1", "ce-a2"], "green"),
            ("zone-pop1", "POP-1 (West)", ["pop1", "fpp1", "pe1", "vrf-a"], "blue"),
            ("zone-core", "MPLS Core", ["p1", "p2", "rr"], "orange"),
            ("zone-pop2", "POP-2 (East)", ["pop2", "fpp2", "pe2", "vrf-b"], "blue"),
            ("zone-cust-b", "Customer B", ["ce-b1", "ce-b2"], "green"),
        ],
        "badge": ("RFC 4364", "purple"),
    },
    "tpl-sonet-dwdm": {
        "zones": [
            ("zone-popa", "POP-A Hub", ["pop-a", "odf-a", "roadm-a", "txp-a1", "txp-a2", "adm-a"], "red"),
            ("zone-popb", "POP-B", ["pop-b", "odf-b", "oadm-b", "fpp-b", "adm-b"], "blue"),
            ("zone-popc", "POP-C", ["pop-c", "odf-c", "oadm-c", "adm-c"], "green"),
            ("zone-popd", "POP-D", ["pop-d", "odf-d", "oadm-d", "fpp-d", "adm-d"], "orange"),
        ],
        "badge": ("BLSR Ring", "purple"),
    },
    "tpl-sdn-openflow": {
        "zones": [
            ("zone-ctrl", "Control Plane", ["ctrl1", "ctrl2"], "purple"),
            ("zone-data", "Data Plane", ["of1", "of2", "of3", "of4"], "blue"),
            ("zone-hosts", "Hosts", ["h1", "h2", "h3", "h4"], "green"),
        ],
        "badge": ("OpenFlow 1.3", "orange"),
    },
    "tpl-zero-trust": {
        "zones": [
            ("zone-policy", "Identity & Policy", ["idp", "pe", "pa", "siem"], "purple"),
            ("zone-pep", "Policy Enforcement", ["pep1", "pep2", "pep3"], "orange"),
            ("zone-res", "Resources", ["res1", "res2", "usr1"], "green"),
        ],
        "badge": ("NIST 800-207", "red"),
    },
    "tpl-qkd-p2p": {
        "zones": [
            ("zone-alice", "Site A (Alice)", ["qkd1", "kms1", "enc1"], "blue"),
            ("zone-channel", "Quantum Channel", ["fiber", "classic"], "purple"),
            ("zone-bob", "Site B (Bob)", ["qkd2", "kms2", "enc2"], "green"),
        ],
        "badge": ("BB84 QKD", "purple"),
    },
    "tpl-qkd-relay": {
        "zones": [
            ("zone-kmgmt", "QKD Key Network", ["kms_net"], "purple"),
            ("zone-chain", "Trusted Relay Chain", ["site_a", "tn1", "tn2", "tn3", "site_b"], "blue"),
        ],
        "badge": ("Trusted-Node Relay", "green"),
    },
    "tpl-aiml-fabric": {
        "zones": [
            ("zone-spine", "400G Spine (Shared Fabric)", ["sp400_1", "sp400_2"], "red"),
            ("zone-leaf", "AI/ML Leaf Tier", ["lf1", "lf2", "lf3", "lf4"], "orange"),
            ("zone-gpu", "GPU Compute + Storage", ["gpu1", "gpu2", "gpu3", "gpu4", "stor"], "green"),
            ("zone-hpc-spine", "HPC Spine Tier", ["hpc-sp1", "hpc-sp2", "hpc-login", "hpc-sched"], "blue"),
            ("zone-hpc-leaf", "HPC Leaf Tier", ["hpc-lf1", "hpc-lf2", "hpc-lf3", "hpc-pfs"], "teal"),
            (
                "zone-hpc-compute",
                "HPC Compute + Management",
                ["hpc-cn1", "hpc-cn2", "hpc-cn3", "hpc-cn4", "hpc-mgmt"],
                "purple",
            ),
        ],
        "badge": ("AI/ML + HPC Fabric", "red"),
    },
    "tpl-security-zones": {
        "zones": [
            ("zone-inet", "Untrust / Internet", ["internet", "waf"], "red"),
            ("zone-dmz", "DMZ", ["fw_outer", "dmz_web", "dmz_dns", "ids", "fw_inner"], "orange"),
            ("zone-trust", "Trusted / Application", ["app_tier", "db_tier"], "green"),
            ("zone-mgmt", "Management", ["jump", "siem"], "purple"),
        ],
        "badge": ("Multi-Zone DMZ", "blue"),
    },
    "tpl-campus-area": {
        "zones": [
            ("zone-wan", "WAN Edge", ["wan-gw1", "wan-gw2", "fw-edge"], "red"),
            ("zone-core", "Core", ["core1", "core2"], "orange"),
            ("zone-blda", "Building A", ["dist-a", "acc-a1", "acc-a2", "wap-a"], "blue"),
            ("zone-bldb", "Building B", ["dist-b", "acc-b1", "acc-b2", "wap-b"], "blue"),
            ("zone-bldc", "Building C", ["dist-c", "acc-c1", "acc-c2", "wap-c"], "blue"),
            ("zone-dc", "Data Center", ["dc-sw", "srv-cluster", "lb"], "green"),
        ],
        "badge": ("Campus LAN", "teal"),
    },
    "tpl-tactical-ddil": {
        "zones": [
            ("zone-sat", "SATCOM Uplink", ["satcom", "crypto-sat"], "purple"),
            ("zone-toc", "TOC", ["toc-rtr", "toc-sw", "toc-srv", "toc-fw", "toc-kg", "relay"], "blue"),
            ("zone-mesh", "MANET Mesh", ["mesh-1", "mesh-2", "mesh-3", "mesh-4"], "orange"),
            ("zone-fwd", "Forward Teams", ["fwd-1", "kg-fwd1", "fwd-2", "fwd-3", "fwd-4"], "green"),
            ("zone-isr", "ISR Sensors", ["sensor-uav", "sensor-gnd"], "red"),
        ],
        "badge": ("DDIL", "orange"),
    },
    "tpl-east-west-fabric": {
        "zones": [
            ("zone-border", "N-S Border", ["border-lf1", "border-lf2", "edge-fw", "wan-rtr"], "red"),
            ("zone-sspine", "Super-Spine", ["ss1", "ss2", "ss3", "ss4"], "purple"),
            (
                "zone-poda",
                "Pod A — Compute",
                ["sp-a1", "sp-a2", "lf-a1", "lf-a2", "lf-a3", "srv-a1", "srv-a2", "srv-a3", "vrf-app"],
                "blue",
            ),
            (
                "zone-podb",
                "Pod B — Storage",
                ["sp-b1", "sp-b2", "lf-b1", "lf-b2", "lf-b3", "srv-b1", "srv-b2", "srv-b3", "vrf-data"],
                "green",
            ),
        ],
        "badge": ("5-Stage Clos", "orange"),
    },
    "tpl-microseg": {
        "zones": [
            ("zone-spine", "Spine Fabric", ["spine1", "spine2", "perim-fw"], "purple"),
            (
                "zone-web",
                "Web Tier",
                ["leaf-web", "zone-web", "dfw-web", "web-1", "web-2", "vlan-web-t1", "vlan-web-t2"],
                "blue",
            ),
            (
                "zone-app",
                "App Tier",
                ["leaf-app", "zone-app", "dfw-app", "app-1", "app-2", "vlan-app-t1", "vlan-app-t2"],
                "orange",
            ),
            (
                "zone-db",
                "Data Tier",
                ["leaf-db", "zone-db", "dfw-db", "db-1", "db-2", "vlan-db-t1", "vlan-db-t2"],
                "green",
            ),
        ],
        "badge": ("Distributed FW", "red"),
    },
    "tpl-hyperseg": {
        "zones": [
            ("zone-ingress", "Ingress", ["ingress-gw", "waf"], "red"),
            ("zone-ctrl", "Control Plane", ["mesh-cp", "spiffe", "opa"], "purple"),
            (
                "zone-mesh",
                "Service Mesh",
                [
                    "ns-frontend",
                    "svc-fe1",
                    "sidecar-fe1",
                    "ns-order",
                    "svc-order1",
                    "sidecar-order1",
                    "ns-payment",
                    "svc-pay1",
                    "sidecar-pay1",
                    "ns-inventory",
                    "svc-inv1",
                    "sidecar-inv1",
                ],
                "blue",
            ),
            (
                "zone-data",
                "Data Stores",
                ["ns-data", "db-orders", "sidecar-db-ord", "db-inventory", "sidecar-db-inv"],
                "green",
            ),
        ],
        "badge": ("Service Mesh + SPIFFE", "purple"),
    },
    "tpl-man": {
        "zones": [
            ("zone-ring", "Metro DWDM Ring", ["opt-1", "opt-2", "opt-3", "opt-4"], "purple"),
            ("zone-hq", "HQ Site", ["pe-hq", "fips-hq", "fiber-hq", "hq-core", "hq-users"], "blue"),
            ("zone-dc", "DC Site", ["pe-dc", "fips-dc", "fiber-dc", "dc-spine", "dc-srv"], "green"),
            ("zone-colo", "Colo", ["pe-colo", "fips-colo", "fiber-colo", "colo-sw", "colo-cloud"], "orange"),
            (
                "zone-branch",
                "Branch",
                ["pe-branch", "fips-branch", "fiber-branch", "branch-sw", "branch-users"],
                "teal",
            ),
        ],
        "badge": ("FIPS 140 Encrypted", "red"),
    },
    "tpl-wan": {
        "zones": [
            (
                "zone-huba",
                "Hub-A (Primary DC)",
                ["hub-a-rtr", "hub-a-fw", "hub-a-hsm", "hub-a-fips", "hub-a-fiber"],
                "blue",
            ),
            (
                "zone-hubb",
                "Hub-B (DR Site)",
                ["hub-b-rtr", "hub-b-fw", "hub-b-hsm", "hub-b-fips", "hub-b-fiber"],
                "green",
            ),
            ("zone-transport", "WAN Transport", ["mpls-cloud", "inet-cloud", "lte-cloud", "optical-ll"], "orange"),
            (
                "zone-branches",
                "Branch Sites",
                [
                    "br-a-ce",
                    "br-a-fips",
                    "br-a-ge",
                    "br-a-sw",
                    "br-b-ce",
                    "br-b-fips",
                    "br-b-ge",
                    "br-b-sw",
                    "br-c-ce",
                    "br-c-fips",
                    "br-c-ge",
                    "br-c-sw",
                ],
                "teal",
            ),
            ("zone-remote", "Remote / Tactical", ["remote-ce", "remote-fips", "remote-srv"], "red"),
        ],
        "badge": ("Multi-Transport WAN", "purple"),
    },
    "tpl-sonet-ring": {
        "zones": [
            ("zone-hub", "Central Office / Hub", ["dcs", "fpp-hub", "pop-hub", "ds3-1"], "red"),
            (
                "zone-blsr",
                "BLSR Backbone",
                ["adm-1", "adm-2", "adm-3", "adm-4", "fiber-ne", "fiber-se", "fiber-sw", "fiber-nw", "oc3-1"],
                "blue",
            ),
            ("zone-acca", "Access Ring A", ["acc-a1", "acc-a2", "ge-a1", "ge-a2"], "green"),
            ("zone-accb", "Access Ring B", ["acc-b1", "acc-b2", "ge-b1", "ge-b2"], "orange"),
        ],
        "badge": ("BLSR + UPSR", "purple"),
    },
    "tpl-aws-dx-can": {
        "zones": [
            (
                "zone-aws",
                "AWS Cloud",
                ["aws-tgw", "aws-vpc1", "aws-vpc2", "aws-sub1", "aws-sub2", "aws-nfw", "aws-r53"],
                "orange",
            ),
            (
                "zone-mmr",
                "Meet-Me Rooms (Customer Cross-Connects)",
                ["dx-a", "dx-b", "mmr-a", "mmr-b", "xx-a", "xx-b"],
                "purple",
            ),
            (
                "zone-campus",
                "Campus Network",
                ["br1", "br2", "fw1", "fw2", "core1", "core2", "dist1", "dist2", "acc1", "acc2", "acc3"],
                "green",
            ),
        ],
        "badge": ("AWS Direct Connect", "orange"),
    },
    "tpl-azure-er-man": {
        "zones": [
            (
                "zone-azure",
                "Azure Cloud",
                ["az-vwan", "az-vnet1", "az-vnet2", "az-fw", "az-sub1", "az-sub2", "az-fd", "az-dns"],
                "blue",
            ),
            (
                "zone-mmr",
                "Meet-Me Rooms (Customer Cross-Connects)",
                ["er-a", "er-b", "mmr-a", "mmr-b", "xx-a", "xx-b"],
                "purple",
            ),
            (
                "zone-man",
                "Metro Area Network",
                ["pe1", "pe2", "p1", "site-fw1", "site-fw2", "site-fw3", "sw-a", "sw-b", "sw-c"],
                "green",
            ),
        ],
        "badge": ("Azure ExpressRoute", "blue"),
    },
    "tpl-multicloud-dual-pop": {
        "zones": [
            ("zone-aws", "AWS Cloud", ["aws-tgw", "aws-vpc", "aws-nfw", "aws-dx-w", "aws-dx-e"], "orange"),
            ("zone-azure", "Azure Cloud", ["az-vwan", "az-vnet", "az-fw", "az-er-w", "az-er-e"], "blue"),
            (
                "zone-pops",
                "POP Sites + Meet-Me Rooms",
                [
                    "mmr-w",
                    "mmr-e",
                    "xx-w-aws",
                    "xx-w-az",
                    "xx-e-aws",
                    "xx-e-az",
                    "pop-w-rtr1",
                    "pop-w-rtr2",
                    "pop-w-fw",
                    "pop-e-rtr1",
                    "pop-e-rtr2",
                    "pop-e-fw",
                ],
                "purple",
            ),
            ("zone-mpls", "MPLS Backbone", ["mpls-pe-w", "mpls-p1", "mpls-p2", "mpls-pe-e"], "orange"),
            ("zone-metro", "Metro Sites", ["man-fw1", "man-fw2", "man-fw3", "man-sw1", "man-sw2", "man-sw3"], "green"),
        ],
        "badge": ("Multi-Cloud", "purple"),
    },
}


def fix_template(conn, tpl_id, spec):
    row = conn.execute("SELECT graph_json FROM nc_templates WHERE id=%s", (tpl_id,)).fetchone()
    if not row:
        print(f"  SKIP {tpl_id} — not found in DB")
        return False

    gj = json.loads(row[0])
    nodes = gj.get("nodes", [])

    # Build node map (only non-zone, non-heading, non-badge nodes)
    node_map = {n["id"]: n for n in nodes}

    # Remove all old zone/heading/badge nodes
    nodes = [
        n
        for n in nodes
        if not (n["id"].startswith("zone-") or n["id"].startswith("heading-") or n["id"].startswith("badge-"))
    ]

    # Rebuild node map after stripping
    node_map = {n["id"]: n for n in nodes}

    new_zone_nodes = []
    new_heading_nodes = []

    for zone_id, zone_label, device_ids, color_key in spec["zones"]:
        color = COLORS[color_key]
        bounds = get_bounds(device_ids, node_map)
        if not bounds:
            print(f"    WARN: no devices found for zone '{zone_label}' in {tpl_id}")
            continue

        # Zone rectangle
        new_zone_nodes.append(
            {
                "id": zone_id,
                "label": "",
                "type": "draw-rect",
                "x": bounds["x"],
                "y": bounds["y"],
                "config": {
                    "_fill": color["_fill"],
                    "_stroke": color["_stroke"],
                    "_width": bounds["w"],
                    "_height": bounds["h"],
                    "_fillOpacity": 0.5,
                },
            }
        )

        # Heading above zone
        new_heading_nodes.append(
            {
                "id": f"heading-{zone_id}",
                "label": zone_label,
                "type": "text-heading",
                "x": bounds["x"] + 10,
                "y": bounds["y"] + HEADING_OFFSET_Y,
                "config": {"_textColor": color["text"]},
            }
        )

    # Badge
    badge_nodes = []
    if "badge" in spec:
        badge_text, badge_color_key = spec["badge"]
        bc = COLORS[badge_color_key]
        # Place badge at top-center of canvas
        all_xs = [n["x"] for n in nodes]
        center_x = (min(all_xs) + max(all_xs)) // 2 if all_xs else 400
        min_y = min(n["y"] for n in nodes) if nodes else 100
        badge_nodes.append(
            {
                "id": f"badge-{tpl_id}",
                "label": badge_text,
                "type": "text-badge",
                "x": center_x - 30,
                "y": min_y - 60,
                "config": {"_fill": bc["_fill"], "_stroke": bc["_stroke"]},
            }
        )

    # Assemble: zones first (back), then headings, then badge, then devices
    gj["nodes"] = new_zone_nodes + new_heading_nodes + badge_nodes + nodes
    conn.execute("UPDATE nc_templates SET graph_json=%s WHERE id=%s", (json.dumps(gj), tpl_id))
    zone_count = len(new_zone_nodes)
    print(f"  OK {tpl_id:35s} — {zone_count} zones, {len(new_heading_nodes)} headings, {len(badge_nodes)} badges")
    return True


def main():
    conn = get_connection(str(DB_PATH))

    updated = 0
    for tpl_id, spec in TEMPLATE_ZONES.items():
        if fix_template(conn, tpl_id, spec):
            updated += 1

    conn.commit()
    conn.close()
    print(f"\nDone — updated {updated} templates")


if __name__ == "__main__":
    main()
