"""Topology Scanner — NDC Workflow Step 1.

Scans network topology design from DB and produces a topology assessment report
covering devices, links, VPCs, subnets, security groups, and missing required
components.
Outputs JSON with artifact paths to stdout.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_CANVAS = "ndc"
_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / _CANVAS

# Required network components
_REQUIRED_COMPONENTS = {
    "vpc": ["vpc", "virtual-private-cloud", "vnet"],
    "subnets": ["subnet", "sub-net", "cidr"],
    "security_group": ["security-group", "sg", "firewall", "acl", "nacl"],
    "routing": ["route", "routing", "gateway", "igw", "nat"],
}

# Node type categories
_CATEGORY_KEYWORDS = {
    "device": ["router", "switch", "firewall", "host", "server", "instance", "ec2", "vm"],
    "vpc": ["vpc", "vnet", "virtual-private"],
    "subnet": ["subnet", "sub-net", "cidr-block"],
    "security_group": ["security-group", "sg", "acl", "nacl", "firewall-rule"],
    "gateway": ["gateway", "igw", "nat", "tgw", "transit", "vpn"],
    "route": ["route-table", "route table", "routing-table", "rtb", " route"],
    "link": ["link", "connection", "peering", "direct-connect", "vpn-tunnel"],
}

_DEFAULT_DESIGNS = {
    "default-ndc-design": {
        "nodes": [
            {"id": "n1", "type": "vpc", "label": "Production VPC", "classification": "CUI"},
            {"id": "n2", "type": "subnet", "label": "Public Subnet 10.0.1.0/24", "classification": ""},
            {"id": "n3", "type": "subnet", "label": "Private Subnet 10.0.2.0/24", "classification": ""},
            {"id": "n4", "type": "security-group", "label": "App Security Group", "classification": ""},
            {"id": "n5", "type": "gateway", "label": "Internet Gateway", "classification": ""},
            {"id": "n6", "type": "gateway", "label": "NAT Gateway", "classification": ""},
            {"id": "n7", "type": "route", "label": "Public Route Table", "classification": ""},
            {"id": "n8", "type": "route", "label": "Private Route Table", "classification": ""},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "type": "contains"},
            {"source": "n1", "target": "n3", "type": "contains"},
            {"source": "n5", "target": "n2", "type": "routes"},
            {"source": "n6", "target": "n3", "type": "routes"},
            {"source": "n7", "target": "n2", "type": "associates"},
            {"source": "n8", "target": "n3", "type": "associates"},
        ],
    }
}


def _load_designs(project_id: str) -> dict:
    """Load network topology designs from DB; fall back to defaults."""
    for table in ("ndc_topologies", "network_designs"):
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            try:
                rows = conn.execute(
                    f"SELECT id, name, design_json FROM {table} "
                    "ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()
            finally:
                conn.close()
            if rows:
                designs = {}
                for row in rows:
                    did = row[0]
                    try:
                        g = json.loads(row[2]) if row[2] else {}
                    except (json.JSONDecodeError, TypeError):
                        g = {}
                    raw_nodes = g.get("nodes", [])
                    raw_edges = g.get("edges", [])
                    designs[did] = {
                        "nodes": [
                            {
                                "id": n.get("id", ""),
                                "type": n.get("type", ""),
                                "label": n.get("label") or n.get("id", ""),
                                "classification": n.get("classification") or "",
                            }
                            for n in raw_nodes
                        ],
                        "edges": [
                            {
                                "source": e.get("source") or e.get("from", ""),
                                "target": e.get("target") or e.get("to", ""),
                                "type": e.get("type") or e.get("label", ""),
                            }
                            for e in raw_edges
                        ],
                    }
                return designs
        except Exception:
            continue
    return _DEFAULT_DESIGNS


def _categorize_nodes(nodes: list) -> dict:
    """Group nodes into topology categories."""
    categories: dict = {k: [] for k in _CATEGORY_KEYWORDS}
    categories["other"] = []
    for n in nodes:
        tl = ((n.get("type") or "") + " " + (n.get("label") or "")).lower()
        matched = False
        for cat, keywords in _CATEGORY_KEYWORDS.items():
            if any(kw in tl for kw in keywords):
                categories[cat].append(n)
                matched = True
                break
        if not matched:
            categories["other"].append(n)
    return categories


def _check_required_components(nodes: list) -> list:
    """Return list of missing required network components."""
    missing = []
    all_tl = [
        ((n.get("type") or "") + " " + (n.get("label") or "")).lower()
        for n in nodes
    ]
    for component, keywords in _REQUIRED_COMPONENTS.items():
        found = any(any(kw in tl for kw in keywords) for tl in all_tl)
        if not found:
            missing.append(component)
    return missing


def build_report(designs: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Network Topology Scan Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Designs scanned:** {len(designs)}",
        "",
    ]

    if not designs:
        lines += [
            "## No Network Topology Designs Found",
            "",
            "No designs found. Create and save an NDC canvas design first.",
        ]
        return "\n".join(lines)

    for did, d in designs.items():
        nodes = d["nodes"]
        edges = d["edges"]
        categories = _categorize_nodes(nodes)
        missing = _check_required_components(nodes)

        # Compute specific counts
        vpc_count = len(categories["vpc"])
        subnet_count = len(categories["subnet"])
        sg_count = len(categories["security_group"])
        device_count = len(categories["device"])
        link_count = len(edges)

        lines.append(f"## Design `{did}`")
        lines.append(f"**Total Nodes:** {len(nodes)}  **Links/Edges:** {link_count}")
        lines.append("")

        lines.append("### Topology Summary")
        lines.append("| Component | Count |")
        lines.append("|-----------|-------|")
        lines.append(f"| Devices (EC2, VM, etc.) | {device_count} |")
        lines.append(f"| VPCs / VNets | {vpc_count} |")
        lines.append(f"| Subnets | {subnet_count} |")
        lines.append(f"| Security Groups / ACLs | {sg_count} |")
        lines.append(f"| Gateways (IGW, NAT, TGW) | {len(categories['gateway'])} |")
        lines.append(f"| Route Tables | {len(categories['route'])} |")
        lines.append(f"| Links / Connections | {link_count} |")
        lines.append("")

        lines.append("### Node Inventory")
        lines.append("| Category | Labels |")
        lines.append("|----------|--------|")
        for cat, cat_nodes in categories.items():
            if cat_nodes:
                labels = [n.get("label") or n.get("id", "") for n in cat_nodes]
                lines.append(
                    f"| `{cat}` | {', '.join(labels[:6])}{'...' if len(labels) > 6 else ''} |"
                )

        lines.append("")
        lines.append("### Required Component Check")
        if missing:
            for m in missing:
                lines.append(f"- ⚠ Missing required network component: **{m}**")
        else:
            lines.append("- ✓ All required components present (VPC, subnets, SG, routing)")

        lines.append("")
        lines.append("### Connectivity")
        orphans = [
            n for n in nodes
            if not any(e["source"] == n["id"] or e["target"] == n["id"] for e in edges)
        ]
        if orphans:
            orphan_labels = [n.get("label") or n.get("id", "") for n in orphans]
            lines.append(f"- ⚠ {len(orphans)} unconnected node(s): {', '.join(orphan_labels[:5])}")
        else:
            lines.append("- ✓ All nodes have at least one connection")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Topology Scanner — NDC Step 1")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default=_CANVAS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        designs = _load_designs(args.project_id)
        report_md = build_report(designs, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fpath = _ARTIFACTS_DIR / f"topology_scan_{uid}.md"
        fpath.write_text(report_md, encoding="utf-8", newline="")

        total_nodes = sum(len(d["nodes"]) for d in designs.values())
        total_links = sum(len(d["edges"]) for d in designs.values())
        all_missing: list = []
        for d in designs.values():
            all_missing.extend(_check_required_components(d["nodes"]))

        result = {
            "status": "success",
            "canvas": args.canvas,
            "designs_scanned": len(designs),
            "total_nodes": total_nodes,
            "total_links": total_links,
            "missing_components": list(set(all_missing)),
            "artifacts": [
                {
                    "name": "Topology Scan Report",
                    "path": fpath.relative_to(_ROOT).as_posix(),
                    "type": "md",
                }
            ],
        }
        print(json.dumps(result))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
