"""Infrastructure Scanner — IDC Workflow Step 1.

Scans infrastructure blueprint design from DB and produces an infrastructure
assessment report covering compute, storage, network, and CSP targets.
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

_CANVAS = "idc"
_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / _CANVAS

# Required component types that must be present in a valid infra design
_REQUIRED_COMPONENTS = {
    "vpc": ["vpc", "network", "vnet"],
    "compute": ["ec2", "compute", "instance", "vm", "server", "app-server"],
    "iam": ["iam", "role", "identity", "permission"],
}

# CSP target patterns
_CSP_PATTERNS = {
    "AWS": ["aws", "ec2", "s3", "rds", "lambda", "eks", "ecs"],
    "Azure": ["azure", "vm", "blob", "aks", "cosmos"],
    "GCP": ["gcp", "gke", "bigquery", "cloud-run"],
    "On-Prem": ["on-prem", "onprem", "bare-metal", "vmware"],
}

# Default designs used when no DB designs exist
_DEFAULT_DESIGNS = {
    "default-idc-design": {
        "nodes": [
            {"id": "n1", "type": "compute", "label": "App Server", "classification": "CUI"},
            {"id": "n2", "type": "storage", "label": "Data Volume", "classification": "CUI"},
            {"id": "n3", "type": "vpc", "label": "VPC", "classification": ""},
            {"id": "n4", "type": "iam", "label": "IAM Role", "classification": ""},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "type": "attaches"},
            {"source": "n3", "target": "n1", "type": "hosts"},
            {"source": "n4", "target": "n1", "type": "grants"},
        ],
    }
}


def _load_designs(project_id: str) -> dict:
    """Load infrastructure designs from DB; fall back to defaults if empty."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, design_json FROM infra_canvas_designs "
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
        pass
    return _DEFAULT_DESIGNS


def _detect_csp(nodes: list) -> dict:
    """Detect CSP distribution based on node type labels."""
    csp_counts: dict = {}
    for n in nodes:
        label_lower = (n.get("label") or "").lower()
        ntype_lower = (n.get("type") or "").lower()
        matched = False
        for csp, patterns in _CSP_PATTERNS.items():
            if any(p in label_lower or p in ntype_lower for p in patterns):
                csp_counts[csp] = csp_counts.get(csp, 0) + 1
                matched = True
                break
        if not matched:
            csp_counts["AWS"] = csp_counts.get("AWS", 0) + 1  # default assumption
    return csp_counts


def _check_required_components(nodes: list) -> list:
    """Return list of missing required component categories."""
    missing = []
    all_types_labels = [
        (n.get("type") or "").lower() + " " + (n.get("label") or "").lower()
        for n in nodes
    ]
    for component, keywords in _REQUIRED_COMPONENTS.items():
        found = any(
            any(kw in tl for kw in keywords)
            for tl in all_types_labels
        )
        if not found:
            missing.append(component)
    return missing


def _categorize_nodes(nodes: list) -> dict:
    """Group nodes into compute, storage, network, and other."""
    categories: dict = {"compute": [], "storage": [], "network": [], "iam": [], "other": []}
    for n in nodes:
        tl = ((n.get("type") or "") + " " + (n.get("label") or "")).lower()
        if any(k in tl for k in ["ec2", "compute", "instance", "server", "vm", "lambda", "ecs", "eks"]):
            categories["compute"].append(n)
        elif any(k in tl for k in ["s3", "ebs", "storage", "volume", "rds", "db", "database", "bucket"]):
            categories["storage"].append(n)
        elif any(k in tl for k in ["vpc", "subnet", "network", "gateway", "route", "sg", "security-group", "nacl", "lb", "alb", "nlb"]):
            categories["network"].append(n)
        elif any(k in tl for k in ["iam", "role", "policy", "identity", "permission"]):
            categories["iam"].append(n)
        else:
            categories["other"].append(n)
    return categories


def build_report(designs: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Infrastructure Scan Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Designs scanned:** {len(designs)}",
        "",
    ]

    if not designs:
        lines += [
            "## No Infrastructure Designs Found",
            "",
            "No designs found. Create and save an IDC canvas design first.",
        ]
        return "\n".join(lines)

    for did, d in designs.items():
        nodes = d["nodes"]
        edges = d["edges"]
        csp_dist = _detect_csp(nodes)
        missing = _check_required_components(nodes)
        categories = _categorize_nodes(nodes)

        lines.append(f"## Design `{did}`")
        lines.append(f"**Total Nodes:** {len(nodes)}  **Edges:** {len(edges)}")
        lines.append("")

        lines.append("### Node Inventory by Category")
        lines.append("| Category | Count | Labels |")
        lines.append("|----------|-------|--------|")
        for cat, cat_nodes in sorted(categories.items()):
            labels = [n.get("label") or n.get("id", "") for n in cat_nodes]
            if labels:
                lines.append(f"| `{cat}` | {len(labels)} | {', '.join(labels[:5])}{'...' if len(labels) > 5 else ''} |")

        lines.append("")
        lines.append("### CSP Distribution")
        lines.append("| CSP Target | Node Count |")
        lines.append("|------------|------------|")
        for csp, count in sorted(csp_dist.items(), key=lambda x: -x[1]):
            lines.append(f"| {csp} | {count} |")

        lines.append("")
        lines.append("### Required Component Check")
        if missing:
            for m in missing:
                lines.append(f"- ⚠ Missing required component: **{m}** (VPC, compute, and IAM are mandatory)")
        else:
            lines.append("- ✓ All required components present (VPC, compute, IAM)")

        lines.append("")
        lines.append("### Connectivity")
        orphans = [n for n in nodes if not any(
            e["source"] == n["id"] or e["target"] == n["id"] for e in edges
        )]
        if orphans:
            orphan_labels = [n.get("label") or n.get("id", "") for n in orphans]
            lines.append(f"- ⚠ {len(orphans)} orphaned node(s): {', '.join(orphan_labels[:5])}")
        else:
            lines.append("- ✓ All nodes connected")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Infrastructure Scanner — IDC Step 1")
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
        fname = f"infra_scan_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        total_nodes = sum(len(d["nodes"]) for d in designs.values())
        all_missing = []
        for d in designs.values():
            all_missing.extend(_check_required_components(d["nodes"]))

        result = {
            "status": "success",
            "canvas": args.canvas,
            "designs_scanned": len(designs),
            "total_nodes": total_nodes,
            "missing_components": list(set(all_missing)),
            "artifacts": [
                {
                    "name": "Infrastructure Scan Report",
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
