"""Boundary Scanner — BDC Workflow Step 1.

Scans authorization boundary design from DB and produces a boundary assessment
report covering boundary nodes, ISA connections, classified data flows, and
missing required boundary controls.
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

_CANVAS = "bdc"
_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / _CANVAS

# Required boundary controls
_REQUIRED_CONTROLS = {
    "pep": ["pep", "policy-enforcement", "enforcement-point", "access-control"],
    "vpc_endpoint": ["vpc-endpoint", "endpoint", "privatelink", "interface-endpoint", "gateway-endpoint"],
    "data_classification": ["classification", "cui", "secret", "pii", "phi", "classified"],
}

# Node categories for boundary design
_CATEGORY_KEYWORDS = {
    "boundary_node": ["boundary", "perimeter", "dmz", "zone", "enclave"],
    "isa_connection": ["isa", "interconnection", "agreement", "mou", "cross-domain"],
    "data_flow": ["flow", "data-flow", "transfer", "transmission", "pipeline"],
    "control": ["kms", "nacl", "acl", "pep", "enforcement", "firewall", "waf", "guard"],
    "endpoint": ["endpoint", "vpc-endpoint", "privatelink", "interface", "gateway-ep"],
    "classification": ["cui", "secret", "pii", "phi", "classification", "data-class"],
}

_DEFAULT_DESIGNS = {
    "default-bdc-design": {
        "nodes": [
            {"id": "n1", "type": "boundary", "label": "Authorization Boundary", "classification": "CUI"},
            {"id": "n2", "type": "isa_connection", "label": "ISA-001 External Agency", "classification": "CUI"},
            {"id": "n3", "type": "data_flow", "label": "CUI Data Flow Outbound", "classification": "CUI"},
            {"id": "n4", "type": "control", "label": "PEP - Policy Enforcement Point", "classification": ""},
            {"id": "n5", "type": "endpoint", "label": "VPC Endpoint S3", "classification": ""},
            {"id": "n6", "type": "classification", "label": "CUI Classification Mark", "classification": "CUI"},
            {"id": "n7", "type": "control", "label": "NACL Boundary Rule", "classification": ""},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "type": "has-isa"},
            {"source": "n1", "target": "n3", "type": "permits"},
            {"source": "n4", "target": "n3", "type": "enforces"},
            {"source": "n5", "target": "n1", "type": "secures"},
            {"source": "n6", "target": "n3", "type": "marks"},
            {"source": "n7", "target": "n1", "type": "protects"},
        ],
    }
}


def _load_designs(project_id: str) -> dict:
    """Load boundary designs from DB; fall back to defaults."""
    for table in ("bdc_boundaries", "boundary_designs"):
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
    """Group nodes into boundary categories."""
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


def _check_missing_controls(nodes: list) -> list:
    """Return list of missing required boundary controls."""
    missing = []
    all_tl = [
        ((n.get("type") or "") + " " + (n.get("label") or "")).lower()
        for n in nodes
    ]
    for control, keywords in _REQUIRED_CONTROLS.items():
        found = any(any(kw in tl for kw in keywords) for tl in all_tl)
        if not found:
            missing.append(control)
    return missing


def _detect_classified_flows(nodes: list, edges: list) -> list:
    """Identify classified data flows."""
    classified_node_ids = {
        n["id"]
        for n in nodes
        if any(kw in ((n.get("type") or "") + " " + (n.get("label") or "")).lower()
               for kw in ["cui", "secret", "classified", "pii", "phi"])
    }
    classified_flows = []
    for e in edges:
        if e["source"] in classified_node_ids or e["target"] in classified_node_ids:
            classified_flows.append(e)
    return classified_flows


def build_report(designs: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Authorization Boundary Scan Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Designs scanned:** {len(designs)}",
        "",
    ]

    if not designs:
        lines += [
            "## No Boundary Designs Found",
            "",
            "No designs found. Create and save a BDC canvas design first.",
        ]
        return "\n".join(lines)

    for did, d in designs.items():
        nodes = d["nodes"]
        edges = d["edges"]
        categories = _categorize_nodes(nodes)
        missing_controls = _check_missing_controls(nodes)
        classified_flows = _detect_classified_flows(nodes, edges)

        isa_nodes = categories["isa_connection"]

        lines.append(f"## Design `{did}`")
        lines.append(f"**Total Nodes:** {len(nodes)}  **Edges:** {len(edges)}")
        lines.append("")

        lines.append("### Boundary Component Summary")
        lines.append("| Component Category | Count |")
        lines.append("|-------------------|-------|")
        for cat, cat_nodes in categories.items():
            if cat_nodes:
                lines.append(f"| `{cat}` | {len(cat_nodes)} |")
        lines.append("")

        lines.append("### ISA Connections")
        if isa_nodes:
            lines.append("| ISA Label | Classification |")
            lines.append("|-----------|----------------|")
            for n in isa_nodes:
                lines.append(
                    f"| {n.get('label', '')} | {n.get('classification', 'Unclassified')} |"
                )
        else:
            lines.append("- ⚠ No ISA connection nodes found")
        lines.append("")

        lines.append("### Classified Data Flows")
        if classified_flows:
            lines.append(f"**{len(classified_flows)} classified flow(s) detected**")
            lines.append("| Source Node | Target Node | Flow Type |")
            lines.append("|-------------|-------------|-----------|")
            for flow in classified_flows[:10]:
                lines.append(
                    f"| `{flow['source'][:12]}` | `{flow['target'][:12]}` | {flow.get('type', '')} |"
                )
            if len(classified_flows) > 10:
                lines.append(f"| *(+{len(classified_flows) - 10} more)* | | |")
        else:
            lines.append("- No explicitly classified data flow nodes detected in design")
        lines.append("")

        lines.append("### Required Boundary Control Check")
        if missing_controls:
            for m in missing_controls:
                control_map = {
                    "pep": "Policy Enforcement Point (PEP) — required for all boundary crossings",
                    "vpc_endpoint": "VPC Endpoint — required for private AWS service access",
                    "data_classification": "Data Classification Mark — all flows must have classification",
                }
                lines.append(f"- ⚠ Missing: **{m}** — {control_map.get(m, m)}")
        else:
            lines.append("- ✓ All required boundary controls present (PEP, VPC endpoints, classification marks)")
        lines.append("")

        orphans = [
            n for n in nodes
            if not any(e["source"] == n["id"] or e["target"] == n["id"] for e in edges)
        ]
        if orphans:
            orphan_labels = [n.get("label") or n.get("id", "") for n in orphans]
            lines.append(
                f"### ⚠ Orphaned Boundary Nodes ({len(orphans)}): "
                f"{', '.join(orphan_labels[:5])}"
            )
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Boundary Scanner — BDC Step 1")
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
        fpath = _ARTIFACTS_DIR / f"boundary_scan_{uid}.md"
        fpath.write_text(report_md, encoding="utf-8")

        total_nodes = sum(len(d["nodes"]) for d in designs.values())
        all_missing: list = []
        for d in designs.values():
            all_missing.extend(_check_missing_controls(d["nodes"]))

        result = {
            "status": "success",
            "canvas": args.canvas,
            "designs_scanned": len(designs),
            "total_nodes": total_nodes,
            "missing_controls": list(set(all_missing)),
            "artifacts": [
                {
                    "name": "Boundary Scan Report",
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
