"""Data Lineage Scanner — DDC Workflow Step 1.

Scans data canvas nodes and edges to produce a lineage report.
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


def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


def scan_lineage(project_id: str) -> dict:
    conn = _get_conn()
    try:
        nodes = conn.execute(
            "SELECT design_id, node_id, node_type, label, classification FROM data_nodes ORDER BY design_id, created_at"
        ).fetchall()
        edges = conn.execute(
            "SELECT design_id, source_id, target_id, edge_type, label FROM data_edges ORDER BY design_id"
        ).fetchall()
        kg_nodes = conn.execute(
            "SELECT canvas, design_id, node_id, node_type, label FROM canvas_kg_nodes WHERE canvas='ddc' ORDER BY design_id"
        ).fetchall()
    finally:
        conn.close()

    designs: dict = {}
    for row in (nodes or []):
        did = row[0]
        designs.setdefault(did, {"nodes": [], "edges": []})
        designs[did]["nodes"].append({
            "id": row[1], "type": row[2], "label": row[3], "classification": row[4]
        })
    for row in (edges or []):
        did = row[0]
        designs.setdefault(did, {"nodes": [], "edges": []})
        designs[did]["edges"].append({
            "source": row[1], "target": row[2], "type": row[3], "label": row[4]
        })

    # Fall back to canvas KG nodes if data_nodes is empty
    if not nodes and kg_nodes:
        for row in (kg_nodes or []):
            did = row[1]
            designs.setdefault(did, {"nodes": [], "edges": []})
            designs[did]["nodes"].append({
                "id": row[2], "type": row[3], "label": row[4], "classification": "CUI"
            })

    return designs


def build_report(designs: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Data Lineage Scan Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Designs scanned:** {len(designs)}",
        "",
    ]
    if not designs:
        lines += [
            "## No Data Canvas Designs Found",
            "",
            "No nodes or edges found in `data_nodes` / `canvas_kg_nodes` for canvas type `ddc`.",
            "Create a data canvas design and save it before running this workflow.",
        ]
        return "\n".join(lines)

    for did, d in designs.items():
        lines.append(f"## Design `{did}`")
        nodes = d["nodes"]
        edges = d["edges"]

        # Classify nodes by type
        by_type: dict = {}
        for n in nodes:
            t = n.get("type") or "unknown"
            by_type.setdefault(t, []).append(n.get("label") or n.get("id"))

        lines.append(f"**Nodes:** {len(nodes)}  **Edges:** {len(edges)}")
        lines.append("")
        lines.append("### Node Inventory")
        lines.append("| Type | Count | Labels |")
        lines.append("|------|-------|--------|")
        for ntype, labels in sorted(by_type.items()):
            lines.append(f"| `{ntype}` | {len(labels)} | {', '.join(labels[:5])}{'...' if len(labels)>5 else ''} |")

        if edges:
            lines.append("")
            lines.append("### Data Flow Edges")
            lines.append("| Source | Target | Type | Label |")
            lines.append("|--------|--------|------|-------|")
            for e in edges[:20]:
                lines.append(f"| `{e['source'][:8]}` | `{e['target'][:8]}` | {e.get('type','')} | {e.get('label','')} |")
            if len(edges) > 20:
                lines.append(f"| *(+{len(edges)-20} more)* | | | |")

        # Detect lineage issues
        issues = []
        node_ids = {n["id"] for n in nodes}
        orphans = [n for n in nodes if not any(
            e["source"] == n["id"] or e["target"] == n["id"] for e in edges
        )]
        if orphans:
            issues.append(f"{len(orphans)} orphaned node(s) with no lineage connections")
        cui_untagged = [n for n in nodes if not n.get("classification")]
        if cui_untagged:
            issues.append(f"{len(cui_untagged)} node(s) missing data classification")

        if issues:
            lines.append("")
            lines.append("### Lineage Issues")
            for issue in issues:
                lines.append(f"- ⚠ {issue}")
        else:
            lines.append("")
            lines.append("### Lineage Issues")
            lines.append("- ✓ No lineage issues detected")

        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Data Lineage Scanner")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        designs = scan_lineage(args.project_id)
        report_md = build_report(designs, args.project_id)

        artifacts_dir = _ROOT / "data" / "studio_artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"lineage_report_{uuid.uuid4().hex[:8]}.md"
        fpath = artifacts_dir / fname
        fpath.write_text(report_md, encoding="utf-8")

        result = {
            "status": "success",
            "designs_scanned": len(designs),
            "total_nodes": sum(len(d["nodes"]) for d in designs.values()),
            "total_edges": sum(len(d["edges"]) for d in designs.values()),
            "artifacts": [
                {"name": "Lineage Report", "path": f"data/studio_artifacts/{fname}", "type": "md"}
            ],
        }
        print(json.dumps(result))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
