# [CUI // SP-CTI]
"""ICDEV™ Network Canvas — Topology Auto-Enricher.

Automatically enriches a raw topology with:
  1. Device type classification from naming convention patterns
  2. Site grouping boxes (dashed border, labeled)
  3. Rack infrastructure (PDU-A/B, UPS, fiber/copper patch panels)
  4. Label formatting (name at top, IP at bottom)

Run after ingestion to make any topology print-ready and template-quality.

Usage::

    python tools/network/topology_enricher.py --topology-id T1 --json
    python tools/network/topology_enricher.py --topology-id T1 --save-template "My Template"
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402

logger = get_logger("icdev.network.topology_enricher")

# ── Site colors for grouping boxes ────────────────────────────────────────

_SITE_COLORS = [
    "#1565c0", "#2e7d32", "#e65100", "#6a1b9a", "#00838f",
    "#ad1457", "#4e342e", "#37474f", "#1b5e20", "#b71c1c",
]


def enrich_topology(
    topology_id: str,
    add_infra: bool = True,
    add_groups: bool = True,
    save: bool = True,
) -> dict[str, Any]:
    """Auto-enrich a topology with infrastructure and site grouping.

    Args:
        topology_id: Topology ID in network_canvas.db.
        add_infra: Add PDU, UPS, patch panels per rack.
        add_groups: Add site grouping boxes.
        save: Save enriched graph back to DB.

    Returns:
        Dict with enrichment stats.
    """
    from tools.network.blueprint_helpers import get_parsed_graph, invalidate_parsed_graph

    conn = get_connection(db_path=str(BASE_DIR / "data" / "network_canvas.db"))

    name_row = conn.execute("SELECT name FROM topologies WHERE id = %s", (topology_id,)).fetchone()
    if not name_row:
        conn.close()
        return {"error": f"Topology {topology_id} not found"}
    topo_name = name_row["name"] if hasattr(name_row, "keys") else name_row[0]

    # This function MUTATES the graph (adds nodes, rewrites device config), so it
    # must own a private copy — never the shared read-only cache entry (ndc-perf-02).
    graph = get_parsed_graph(conn, topology_id, copy=True)
    if graph is None:
        conn.close()
        return {"error": f"Topology {topology_id} not found"}

    # Separate devices from existing groups
    devices = [n for n in graph["nodes"] if n.get("type") != "group-site"]

    infra_added = 0
    groups_added = 0

    # ── Add rack infrastructure ───────────────────────────────────────
    infra_nodes = []
    if add_infra:
        rack_devices = defaultdict(list)
        for d in devices:
            rack = d.get("config", {}).get("rack", "")
            if rack:
                rack_name = rack.split("/")[0].strip()
                rack_devices[rack_name].append(d)

        # Determine connection types per rack
        rack_has_fiber = defaultdict(bool)
        rack_has_copper = defaultdict(bool)
        for rname, rdevs in rack_devices.items():
            for d in rdevs:
                dt = d.get("type", "")
                if dt in ("router", "firewall", "load_balancer"):
                    rack_has_fiber[rname] = True
                if dt in ("switch", "access_point", "server"):
                    rack_has_copper[rname] = True

        for e in graph.get("edges", []):
            label = (e.get("label", "") or "").lower()
            src_id, dst_id = e.get("source", ""), e.get("target", "")
            for d in devices:
                if d["id"] in (src_id, dst_id):
                    rack = d.get("config", {}).get("rack", "")
                    if rack:
                        rn = rack.split("/")[0].strip()
                        if any(s in label for s in ["100g", "40g", "25g", "10g", "fiber"]):
                            rack_has_fiber[rn] = True
                        if any(s in label for s in ["1g", "copper"]):
                            rack_has_copper[rn] = True

        for rname in sorted(rack_devices.keys()):
            rdevs = rack_devices[rname]
            if not rdevs:
                continue

            # Check if infra already exists for this rack
            existing_infra = [d for d in devices if d.get("id", "").startswith("infra-") and rname.lower().replace(" ", "").replace("-", "") in d.get("id", "")]
            if existing_infra:
                continue

            max_x = max(d["x"] for d in rdevs)
            min_y = min(d["y"] for d in rdevs)
            site = rdevs[0].get("config", {}).get("site", "")
            infra_x = max_x + 140
            y = min_y
            rkey = rname.lower().replace(" ", "").replace("-", "")

            # PDU-A
            infra_nodes.append({"id": f"infra-pdu-a-{rkey}", "label": "PDU-A", "type": "pdu",
                "x": infra_x, "y": y, "config": {"_width": 90, "_height": 50, "site": site, "rack": f"{rname} / U41-U42",
                "description": "Power Distribution Unit — Feed A (utility/generator)", "vendor": "APC", "model": "AP8886"}})
            y += 55
            # PDU-B
            infra_nodes.append({"id": f"infra-pdu-b-{rkey}", "label": "PDU-B", "type": "pdu",
                "x": infra_x, "y": y, "config": {"_width": 90, "_height": 50, "site": site, "rack": f"{rname} / U39-U40",
                "description": "Power Distribution Unit — Feed B (redundant)", "vendor": "APC", "model": "AP8886"}})
            y += 55
            # UPS
            infra_nodes.append({"id": f"infra-ups-{rkey}", "label": "UPS", "type": "ups",
                "x": infra_x, "y": y, "config": {"_width": 90, "_height": 50, "site": site, "rack": f"{rname} / U1-U3",
                "description": "Uninterruptible Power Supply — battery backup", "vendor": "APC", "model": "SRT3000RMXLI"}})
            y += 55
            # Fiber PP
            if rack_has_fiber.get(rname):
                infra_nodes.append({"id": f"infra-fpp-{rkey}", "label": "Fiber PP", "type": "patch-panel",
                    "x": infra_x, "y": y, "config": {"_width": 90, "_height": 50, "site": site, "rack": f"{rname} / U38",
                    "description": "Fiber patch panel — LC/SC duplex for SFP+/QSFP28 uplinks", "vendor": "Corning", "model": "CCH-04U", "media": "fiber"}})
                y += 55
            # Copper PP
            if rack_has_copper.get(rname):
                infra_nodes.append({"id": f"infra-cpp-{rkey}", "label": "Copper PP", "type": "patch-panel",
                    "x": infra_x, "y": y, "config": {"_width": 90, "_height": 50, "site": site, "rack": f"{rname} / U37",
                    "description": "Copper patch panel — Cat6a RJ45 for access ports", "vendor": "Panduit", "model": "CP48WSBLY", "media": "copper"}})

        infra_added = len(infra_nodes)

    # ── Sync site info from ni_devices into graph_json nodes ─────────
    # (ni_devices may have site set by metadata assignment after ingestion)
    try:
        dev_sites = {}
        rows = conn.execute(
            "SELECT node_id, site FROM ni_devices WHERE topology_id = %s AND site IS NOT NULL AND site != ''",
            (topology_id,),
        ).fetchall()
        for r in rows:
            nid = r["node_id"] if isinstance(r, dict) else r[0]
            site = r["site"] if isinstance(r, dict) else r[1]
            dev_sites[nid] = site
        for d in devices:
            if not d.get("config", {}).get("site") and d["id"] in dev_sites:
                if "config" not in d:
                    d["config"] = {}
                d["config"]["site"] = dev_sites[d["id"]]
    except Exception:
        pass  # Best-effort: ni_devices table may not exist

    # ── Add site grouping boxes ───────────────────────────────────────
    new_groups = []
    if add_groups:
        all_devs = devices + infra_nodes
        site_devs = defaultdict(list)
        for d in all_devs:
            site = d.get("config", {}).get("site", "")
            if site:
                site_devs[site].append(d)

        NODE_W, NODE_H = 110, 70
        PAD_X, PAD_TOP, PAD_BOT = 50, 45, 60

        color_idx = 0
        for site in sorted(site_devs.keys()):
            sdevs = site_devs[site]
            min_x = min(d["x"] for d in sdevs) - PAD_X
            min_y = min(d["y"] for d in sdevs) - PAD_TOP
            max_x = max(d["x"] + NODE_W for d in sdevs) + PAD_X
            max_y = max(d["y"] + NODE_H for d in sdevs) + PAD_BOT
            color = _SITE_COLORS[color_idx % len(_SITE_COLORS)]
            color_idx += 1

            new_groups.append({
                "id": f"site-{site.lower().replace('-', '')}",
                "label": site,
                "type": "group-site",
                "x": min_x, "y": min_y,
                "config": {
                    "_width": max_x - min_x, "_height": max_y - min_y,
                    "_fill": color + "12", "_stroke": color,
                    "_textColor": color, "_strokeWidth": 2,
                },
            })
        groups_added = len(new_groups)

    # ── Auto-create facilities + racks per site ─────────────────────
    facilities_created = 0
    racks_created = 0
    if save and add_groups:
        # Collect unique sites and their device types
        site_device_types: dict[str, set[str]] = defaultdict(set)
        for d in devices:
            site = d.get("config", {}).get("site", "")
            if site:
                site_device_types[site].add(d.get("type", "unknown"))

        for site in sorted(site_device_types.keys()):
            dtypes = site_device_types[site]
            site_key = site.lower().replace("-", "").replace(" ", "")
            fac_id = f"fac-{site_key}"
            rack_id = f"rack-{site_key}-r1"

            # Create facility if not exists
            existing_fac = conn.execute("SELECT id FROM nc_facilities WHERE id = %s", (fac_id,)).fetchone()
            if not existing_fac:
                conn.execute(
                    "INSERT INTO nc_facilities (id, name, facility_type, city, operator) VALUES (%s,%s,%s,%s,%s)",
                    (fac_id, site, "colocation", site, "Auto-generated"),
                )
                facilities_created += 1

            # Create rack if not exists
            existing_rack = conn.execute("SELECT id FROM nc_racks WHERE id = %s", (rack_id,)).fetchone()
            if not existing_rack:
                has_fiber = bool(dtypes & {"router", "firewall", "load_balancer"})
                has_copper = bool(dtypes & {"switch", "access_point", "server"})
                infra_items = [
                    {"type": "PDU-A", "model": "APC AP8886", "ru": "U41-U42", "feed": "A"},
                    {"type": "PDU-B", "model": "APC AP8886", "ru": "U39-U40", "feed": "B"},
                    {"type": "UPS", "model": "APC SRT3000RMXLI", "ru": "U1-U3", "runtime_min": 15},
                ]
                if has_fiber:
                    infra_items.append({"type": "Fiber PP", "model": "Corning CCH-04U", "ru": "U38", "ports": 48, "media": "LC duplex"})
                if has_copper:
                    infra_items.append({"type": "Copper PP", "model": "Panduit CP48WSBLY", "ru": "U37", "ports": 48, "media": "Cat6a RJ45"})

                conn.execute(
                    "INSERT INTO nc_racks (id, facility_id, rack_name, total_ru, notes) VALUES (%s,%s,%s,%s,%s)",
                    (rack_id, fac_id, f"{site}-R1", 42, json.dumps({"infra": infra_items})),
                )
                racks_created += 1

        if facilities_created or racks_created:
            conn.commit()

        # ── Auto-assign rack positions to devices ─────────────────────
        # Assign each device to its site's rack with sequential RU positions
        _RU_HEIGHT = {"router": 2, "firewall": 2, "load_balancer": 1, "switch": 1,
                      "server": 2, "vpn_gateway": 1, "access_point": 1, "wan_link": 0}
        for site in sorted(site_device_types.keys()):
            site_key = site.lower().replace("-", "").replace(" ", "")
            rack_name = f"{site}-R1"
            # Start assigning from U10 upward (leave U1-U9 for infra/cable mgmt)
            ru_cursor = 10
            site_nodes = [d for d in devices if d.get("config", {}).get("site") == site]
            for d in site_nodes:
                dtype = d.get("type", "unknown")
                ru_h = _RU_HEIGHT.get(dtype, 1)
                if ru_h == 0:
                    continue  # WAN links don't go in racks
                rack_pos = f"{rack_name} / U{ru_cursor}" if ru_h == 1 else f"{rack_name} / U{ru_cursor}-U{ru_cursor + ru_h - 1}"
                if "config" not in d:
                    d["config"] = {}
                d["config"]["rack"] = rack_pos
                ru_cursor += ru_h

    # ── Rebuild graph ─────────────────────────────────────────────────
    graph["nodes"] = new_groups + devices + infra_nodes

    if save:
        conn.execute("UPDATE topologies SET graph_json = %s WHERE id = %s", (json.dumps(graph), topology_id))
        conn.commit()
        invalidate_parsed_graph(topology_id)  # ndc-perf-02

    conn.close()

    # ── Auto-validate and fix unknown types ───────────────────────────
    validation = {}
    try:
        from tools.network.topology_validator import validate_topology

        validation = validate_topology(topology_id, fix=True)
    except Exception:
        pass

    return {
        "topology_id": topology_id,
        "topology_name": topo_name,
        "devices": len(devices),
        "infra_added": infra_added,
        "groups_added": groups_added,
        "facilities_created": facilities_created,
        "racks_created": racks_created,
        "total_nodes": len(graph["nodes"]),
        "validation": {
            "issues_found": validation.get("issues_found", 0),
            "fixes_applied": validation.get("fixes_applied", 0),
            "passed": validation.get("passed", True),
        },
    }


def save_as_template(topology_id: str, name: str, description: str = "") -> dict:
    """Save an enriched topology as a reusable template."""
    import uuid

    conn = get_connection(db_path=str(BASE_DIR / "data" / "network_canvas.db"))
    row = conn.execute("SELECT graph_json FROM topologies WHERE id = %s", (topology_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": "Topology not found"}

    tpl_id = "tpl-" + str(uuid.uuid4())[:8]
    desc = description or f"Template from topology {topology_id}"

    conn.execute(
        "INSERT OR REPLACE INTO nc_templates (id, name, category, description, graph_json, tags) "
        "VALUES (%s, %s, 'Enterprise', %s, %s, %s)",
        (tpl_id, name, desc, row[0], json.dumps(["auto-enriched", "template"])),
    )
    conn.commit()
    conn.close()
    return {"template_id": tpl_id, "name": name}


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV Network Topology Enricher")
    parser.add_argument("--topology-id", required=True)
    parser.add_argument("--no-infra", action="store_true", help="Skip rack infrastructure")
    parser.add_argument("--no-groups", action="store_true", help="Skip site grouping")
    parser.add_argument("--save-template", metavar="NAME", help="Save as template with given name")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = enrich_topology(
        args.topology_id,
        add_infra=not args.no_infra,
        add_groups=not args.no_groups,
    )

    if args.save_template:
        tpl = save_as_template(args.topology_id, args.save_template)
        result["template"] = tpl

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("error"):
            print(f"ERROR: {result['error']}")
            sys.exit(1)
        print(f"Enriched: {result['devices']} devices + {result['infra_added']} infra + {result['groups_added']} groups = {result['total_nodes']} total")
        if result.get("template"):
            print(f"Template saved: {result['template']['template_id']}")


if __name__ == "__main__":
    main()
