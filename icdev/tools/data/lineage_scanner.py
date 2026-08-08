"""Data Lineage Scanner — DDC Workflow Step 1.

Reads canvas design from data_canvas.db and produces a lineage report.
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "ddc"


def scan_lineage(project_id: str) -> dict:
    from tools.data.canvas_reader import load_designs
    return load_designs(project_id)


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
            "No designs found. Create and save a DDC canvas design first.",
        ]
        return "\n".join(lines)

    for did, d in designs.items():
        nodes = d["nodes"]
        edges = d["edges"]

        by_type: dict = {}
        for n in nodes:
            t = n.get("type") or "unknown"
            by_type.setdefault(t, []).append(n.get("label") or n.get("id"))

        lines.append(f"## Design `{did}`")
        lines.append(f"**Nodes:** {len(nodes)}  **Edges:** {len(edges)}")
        lines.append("")
        lines.append("### Node Inventory")
        lines.append("| Type | Count | Labels |")
        lines.append("|------|-------|--------|")
        for ntype, labels in sorted(by_type.items()):
            lines.append(f"| `{ntype}` | {len(labels)} | {', '.join(labels[:5])}{'...' if len(labels) > 5 else ''} |")

        if edges:
            lines.append("")
            lines.append("### Data Flow Edges")
            lines.append("| Source | Target | Type |")
            lines.append("|--------|--------|------|")
            for e in edges[:20]:
                lines.append(f"| `{e['source'][:12]}` | `{e['target'][:12]}` | {e.get('type', '')} |")
            if len(edges) > 20:
                lines.append(f"| *(+{len(edges)-20} more)* | | |")

        issues = []
        orphans = [n for n in nodes if not any(
            e["source"] == n["id"] or e["target"] == n["id"] for e in edges
        )]
        if orphans:
            issues.append(f"{len(orphans)} orphaned node(s) with no lineage connections")
        unclassified = [n for n in nodes if not n.get("classification")]
        if unclassified:
            issues.append(f"{len(unclassified)} node(s) missing data classification tag")

        lines.append("")
        lines.append("### Lineage Issues")
        if issues:
            for issue in issues:
                lines.append(f"- ⚠ {issue}")
        else:
            lines.append("- ✓ No lineage issues detected")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Data Lineage Scanner")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        designs = scan_lineage(args.project_id)
        report_md = build_report(designs, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"lineage_report_{uuid.uuid4().hex[:8]}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        result = {
            "status": "success",
            "designs_scanned": len(designs),
            "total_nodes": sum(len(d["nodes"]) for d in designs.values()),
            "total_edges": sum(len(d["edges"]) for d in designs.values()),
            "artifacts": [
                {"name": "Lineage Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"}
            ],
        }
        print(json.dumps(result))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
