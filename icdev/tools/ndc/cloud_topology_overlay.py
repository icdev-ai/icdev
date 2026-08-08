#!/usr/bin/env python3
# CUI // SP-CTI
"""NDC Cloud Topology Overlay Generator.

Reads a topology from the NDC database and enriches it with cloud-provider
overlay nodes (AWS Direct Connect, Azure ExpressRoute, GCP Interconnect)
to produce a hybrid-connectivity visualization for executive demos.

Usage:
    python tools/ndc/cloud_topology_overlay.py --topology-id <id> --json
    python tools/ndc/cloud_topology_overlay.py --topology-id <id> --format markdown
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_NC_DB = BASE_DIR / "data" / "network_canvas.db"


# Deterministic cloud attachment metadata per site type
_CLOUD_ATTACHMENTS = {
    "WAM": [
        {
            "provider": "aws",
            "service": "Direct Connect",
            "node_id_suffix": "aws-dx",
            "asn": 64512,
            "bandwidth": "10G",
            "monthly_cost": 4200.0,
            "il_level": "IL4",
            "region": "us-gov-west-1",
            "encrypted": True,
        },
        {
            "provider": "azure",
            "service": "ExpressRoute",
            "node_id_suffix": "azure-er",
            "asn": 12076,
            "bandwidth": "10G",
            "monthly_cost": 5500.0,
            "il_level": "IL5",
            "region": "usgovvirginia",
            "encrypted": True,
        },
        {
            "provider": "gcp",
            "service": "Interconnect",
            "node_id_suffix": "gcp-interconnect",
            "asn": 16550,
            "bandwidth": "10G",
            "monthly_cost": 3800.0,
            "il_level": "IL4",
            "region": "us-gov-west-1",
            "encrypted": True,
        },
    ],
    "MAN": [
        {
            "provider": "aws",
            "service": "Direct Connect",
            "node_id_suffix": "aws-dx",
            "asn": 64512,
            "bandwidth": "1G",
            "monthly_cost": 1200.0,
            "il_level": "IL4",
            "region": "us-gov-east-1",
            "encrypted": True,
        },
        {
            "provider": "azure",
            "service": "ExpressRoute",
            "node_id_suffix": "azure-er",
            "asn": 12076,
            "bandwidth": "1G",
            "monthly_cost": 1800.0,
            "il_level": "IL4",
            "region": "usgovtexas",
            "encrypted": True,
        },
    ],
    "LAN": [
        {
            "provider": "aws",
            "service": "Site-to-Site VPN",
            "node_id_suffix": "aws-vpn",
            "asn": 64512,
            "bandwidth": "500M",
            "monthly_cost": 0.0,
            "il_level": "IL4",
            "region": "us-gov-west-1",
            "encrypted": True,
        },
    ],
    "DCAM": [
        {
            "provider": "aws",
            "service": "Direct Connect",
            "node_id_suffix": "aws-dx",
            "asn": 64512,
            "bandwidth": "100G",
            "monthly_cost": 28000.0,
            "il_level": "IL5",
            "region": "us-gov-west-1",
            "encrypted": True,
        },
        {
            "provider": "azure",
            "service": "ExpressRoute",
            "node_id_suffix": "azure-er",
            "asn": 12076,
            "bandwidth": "100G",
            "monthly_cost": 32000.0,
            "il_level": "IL5",
            "region": "usgovvirginia",
            "encrypted": True,
        },
        {
            "provider": "gcp",
            "service": "Interconnect",
            "node_id_suffix": "gcp-interconnect",
            "asn": 16550,
            "bandwidth": "100G",
            "monthly_cost": 25000.0,
            "il_level": "IL5",
            "region": "us-gov-west-1",
            "encrypted": True,
        },
    ],
}


def _nc_conn():
    # PG-primary via the Network Canvas helper (NC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.network.db.init_db import get_connection

    return get_connection()


def _infer_site_type(topology_name: str) -> str:
    """Infer topology category from name for demo purposes."""
    name_upper = (topology_name or "").upper()
    if "WAM" in name_upper:
        return "WAM"
    if "MAN" in name_upper:
        return "MAN"
    if "LAN" in name_upper:
        return "LAN"
    if "DCAM" in name_upper or "DC" in name_upper:
        return "DCAM"
    return "WAM"  # default


def _find_edge_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Identify edge router / gateway nodes for cloud attachment."""
    edges: List[Dict[str, Any]] = []
    for n in nodes:
        nid = n.get("id", "")
        nlabel = n.get("label", "")
        ntype = n.get("type", "")
        if "edge" in nid.lower() or "edge" in nlabel.lower():
            edges.append(n)
        elif ntype in ("router", "gateway", "firewall") and ("isp" in nid.lower() or nlabel.startswith("ISP")):
            continue  # Skip ISP nodes
        elif ntype in ("router", "gateway") and ("edge" in nid.lower() or "border" in nid.lower()):
            edges.append(n)
    if not edges:
        # Fallback: any router that connects to ISP nodes
        for n in nodes:
            if n.get("type") == "router":
                edges.append(n)
                break
    return edges[:3]  # Limit to first 3 edge nodes


def generate_cloud_overlay(topology_id: str) -> Dict[str, Any]:
    """Enrich a topology with cloud-provider overlay nodes."""
    conn = _nc_conn()
    row = conn.execute(
        "SELECT id, name, graph_json FROM topologies WHERE id=%s", (topology_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"error": f"Topology not found: {topology_id}"}

    topo_name = row["name"]
    try:
        graph = json.loads(row["graph_json"] or "{}")
    except Exception:
        graph = {"nodes": [], "edges": []}

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    site_type = _infer_site_type(topo_name)
    cloud_specs = _CLOUD_ATTACHMENTS.get(site_type, [])
    edge_nodes = _find_edge_nodes(nodes)

    if not edge_nodes:
        return {
            "classification": "CUI // SP-CTI",
            "topology_id": topology_id,
            "topology_name": topo_name,
            "site_type": site_type,
            "nodes_original": len(nodes),
            "edges_original": len(edges),
            "cloud_nodes_added": 0,
            "cloud_edges_added": 0,
            "error": "No edge/gateway nodes found for cloud attachment overlay.",
        }

    new_nodes: List[Dict[str, Any]] = []
    new_edges: List[Dict[str, Any]] = []
    site_slug = edge_nodes[0].get("id", "site").split("-")[-1] if edge_nodes else "site"

    for spec in cloud_specs:
        node_id = f"cloud-{spec['node_id_suffix']}-{site_slug}"
        # Avoid duplicates
        if any(n.get("id") == node_id for n in nodes):
            continue

        new_nodes.append({
            "id": node_id,
            "label": f"{spec['provider'].upper()} {spec['service']}",
            "type": "cloud-gateway",
            "provider": spec["provider"],
            "service": spec["service"],
            "config": {
                "asn": spec["asn"],
                "bandwidth": spec["bandwidth"],
                "region": spec["region"],
                "il_level": spec["il_level"],
                "encrypted": spec["encrypted"],
                "monthly_cost": spec["monthly_cost"],
            },
        })

        # Attach to each edge node (or just the first one for clarity)
        for edge in edge_nodes[:1]:
            edge_id = edge["id"]
            edge_asn = edge.get("config", {}).get("asn", 65000)
            new_edges.append({
                "id": f"e-{edge_id}-{node_id}",
                "source": edge_id,
                "target": node_id,
                "label": f"eBGP ({spec['bandwidth']})",
                "protocol": "bgp",
                "style": "dashed",
                "config": {
                    "asn_on_prem": edge_asn,
                    "asn_cloud": spec["asn"],
                    "bandwidth": spec["bandwidth"],
                    "il_level": spec["il_level"],
                    "encrypted": spec["encrypted"],
                    "monthly_cost": spec["monthly_cost"],
                    "region": spec["region"],
                },
            })

    enriched = {
        "nodes": nodes + new_nodes,
        "edges": edges + new_edges,
    }

    return {
        "classification": "CUI // SP-CTI",
        "topology_id": topology_id,
        "topology_name": topo_name,
        "site_type": site_type,
        "nodes_original": len(nodes),
        "edges_original": len(edges),
        "cloud_nodes_added": len(new_nodes),
        "cloud_edges_added": len(new_edges),
        "cloud_monthly_cost": round(sum(s["monthly_cost"] for s in cloud_specs), 2),
        "graph": enriched,
    }


def _render_markdown(data: Dict[str, Any]) -> str:
    lines = [
        f"# Cloud Topology Overlay: {data['topology_name']}",
        f"**Site Type:** {data['site_type']}  ",
        f"**Cloud Nodes Added:** {data['cloud_nodes_added']}  ",
        f"**Cloud Edges Added:** {data['cloud_edges_added']}  ",
        f"**Monthly Cloud Cost:** ${data['cloud_monthly_cost']:,.0f}",
        "",
        "## Cloud Attachments",
        "",
        "| Provider | Service | ASN | Bandwidth | Region | IL Level | Monthly Cost |",
        "|----------|---------|-----|-----------|--------|----------|-------------|",
    ]
    graph = data.get("graph", {})
    for node in graph.get("nodes", []):
        if node.get("type") == "cloud-gateway":
            cfg = node.get("config", {})
            lines.append(
                f"| {node['provider'].upper()} | {node['service']} | {cfg.get('asn', '—')} | {cfg.get('bandwidth', '—')} | {cfg.get('region', '—')} | {cfg.get('il_level', '—')} | ${cfg.get('monthly_cost', 0):,.0f} |"
            )
    lines += ["", "---", "*Generated by ICDEV™ Network Design Canvas — Cloud Topology Overlay*"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="NDC Cloud Topology Overlay Generator")
    parser.add_argument("--topology-id", type=str, required=True, help="Topology ID")
    parser.add_argument("--format", type=str, default="json", choices=["json", "markdown"], help="Output format")
    parser.add_argument("--output", type=str, default="", help="Output file path")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    result = generate_cloud_overlay(args.topology_id)

    if args.format == "markdown":
        body = _render_markdown(result)
    else:
        body = json.dumps(result, indent=2)

    if args.output:
        Path(args.output).write_text(body, encoding="utf-8", newline="")
        print(f"Written to {args.output}")
    else:
        print(body)


if __name__ == "__main__":
    main()
