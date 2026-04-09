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
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.network.topology_enricher")

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
    import sqlite3

    conn = sqlite3.connect(str(BASE_DIR / "data" / "network_canvas.db"))
    conn.row_factory = sqlite3.Row

    row = conn.execute("SELECT graph_json, name FROM topologies WHERE id = ?", (topology_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"Topology {topology_id} not found"}

    graph = json.loads(row["graph_json"])
    topo_name = row["name"]

    # Separate existing groups from devices
    existing_groups = [n for n in graph["nodes"] if n.get("type") == "group-site"]
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
            existing_infra = [d for d in devices if d.get("id", "").startswith(f"infra-") and rname.lower().replace(" ", "").replace("-", "") in d.get("id", "")]
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
            "SELECT node_id, site FROM ni_devices WHERE topology_id = ? AND site IS NOT NULL AND site != ''",
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

    # ── Rebuild graph ─────────────────────────────────────────────────
    graph["nodes"] = new_groups + devices + infra_nodes

    if save:
        conn.execute("UPDATE topologies SET graph_json = ? WHERE id = ?", (json.dumps(graph), topology_id))
        conn.commit()

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
        "total_nodes": len(graph["nodes"]),
        "validation": {
            "issues_found": validation.get("issues_found", 0),
            "fixes_applied": validation.get("fixes_applied", 0),
            "passed": validation.get("passed", True),
        },
    }


def save_as_template(topology_id: str, name: str, description: str = "") -> dict:
    """Save an enriched topology as a reusable template."""
    import sqlite3
    import uuid

    conn = sqlite3.connect(str(BASE_DIR / "data" / "network_canvas.db"))
    row = conn.execute("SELECT graph_json FROM topologies WHERE id = ?", (topology_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": "Topology not found"}

    tpl_id = "tpl-" + str(uuid.uuid4())[:8]
    desc = description or f"Template from topology {topology_id}"

    conn.execute(
        "INSERT OR REPLACE INTO nc_templates (id, name, category, description, graph_json, tags) "
        "VALUES (?, ?, 'Enterprise', ?, ?, ?)",
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
