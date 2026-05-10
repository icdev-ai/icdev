"""Schema Checker — DDC Workflow Step 2.

Validates data canvas schema: node types, required fields, edge integrity,
data classification coverage, and NIST 800-53 SC-28 encryption checks.
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

_REQUIRED_NODE_FIELDS = ["label", "node_type"]
_SENSITIVE_TYPES = {"ent-pii", "ent-phi", "ent-cui", "ent-classified"}
_ENCRYPTION_TYPES = {"ctrl-encrypt", "ctrl-kms", "ctrl-cmk", "ctrl-fips"}
_BACKUP_TYPES = {"ctrl-backup", "ctrl-snapshot", "ctrl-dr"}

_VALID_EDGE_TYPES = {
    "flow", "data-flow", "reads", "writes", "streams", "transforms",
    "depends-on", "provision", "monitors", "classifies",
}


def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


def check_schema(project_id: str) -> dict:
    conn = _get_conn()
    try:
        nodes = conn.execute(
            "SELECT design_id, node_id, node_type, label, classification, metadata FROM data_nodes"
        ).fetchall()
        edges = conn.execute(
            "SELECT design_id, source_id, target_id, edge_type FROM data_edges"
        ).fetchall()
        kg_nodes = conn.execute(
            "SELECT canvas, design_id, node_id, node_type, label, metadata_json FROM canvas_kg_nodes WHERE canvas='ddc'"
        ).fetchall()
    finally:
        conn.close()

    findings: list[dict] = []
    stats = {"total_nodes": 0, "total_edges": 0, "designs": 0, "checks_passed": 0, "checks_failed": 0}

    # Aggregate nodes per design
    designs: dict = {}
    for row in (nodes or []):
        did = row[0]
        designs.setdefault(did, {"nodes": [], "edges": []})
        designs[did]["nodes"].append({"id": row[1], "type": row[2], "label": row[3],
                                       "classification": row[4], "metadata": row[5]})
    for row in (edges or []):
        did = row[0]
        designs.setdefault(did, {"nodes": [], "edges": []})
        designs[did]["edges"].append({"source": row[1], "target": row[2], "type": row[3]})

    if not nodes and kg_nodes:
        for row in (kg_nodes or []):
            did = row[1]
            designs.setdefault(did, {"nodes": [], "edges": []})
            designs[did]["nodes"].append({"id": row[2], "type": row[3], "label": row[4],
                                           "classification": "CUI", "metadata": row[5]})

    stats["designs"] = len(designs)
    for did, d in designs.items():
        node_ids = {n["id"] for n in d["nodes"]}
        stats["total_nodes"] += len(d["nodes"])
        stats["total_edges"] += len(d["edges"])

        # Check 1: required fields
        for n in d["nodes"]:
            for f in _REQUIRED_NODE_FIELDS:
                if not n.get(f):
                    findings.append({"severity": "warn", "design": did,
                                     "check": "required_fields",
                                     "message": f"Node `{n['id'][:8]}` missing required field `{f}`"})
                    stats["checks_failed"] += 1

        # Check 2: edge integrity
        for e in d["edges"]:
            if e["source"] not in node_ids or e["target"] not in node_ids:
                findings.append({"severity": "fail", "design": did,
                                 "check": "edge_integrity",
                                 "message": f"Broken edge: {e['source'][:8]} → {e['target'][:8]} (node missing)"})
                stats["checks_failed"] += 1

        # Check 3: encryption for sensitive data
        types = {n["type"] for n in d["nodes"]}
        has_sensitive = bool(types & _SENSITIVE_TYPES)
        has_encryption = bool(types & _ENCRYPTION_TYPES)
        if has_sensitive and not has_encryption:
            findings.append({"severity": "fail", "design": did,
                             "check": "encryption_coverage",
                             "message": "Sensitive data nodes (PII/PHI/CUI) found with no encryption control — NIST SC-28 violation"})
            stats["checks_failed"] += 1
        elif has_sensitive:
            stats["checks_passed"] += 1

        # Check 4: backup for storage nodes
        has_db = any("db" in (n.get("type") or "").lower() or "storage" in (n.get("type") or "").lower()
                     for n in d["nodes"])
        has_backup = bool(types & _BACKUP_TYPES)
        if has_db and not has_backup:
            findings.append({"severity": "warn", "design": did,
                             "check": "backup_policy",
                             "message": "Storage/DB nodes found with no backup control node"})
            stats["checks_failed"] += 1
        elif has_db:
            stats["checks_passed"] += 1

        # Check 5: data classification tagging
        unclassified = [n for n in d["nodes"] if not n.get("classification")]
        if unclassified:
            findings.append({"severity": "warn", "design": did,
                             "check": "classification_tagging",
                             "message": f"{len(unclassified)} node(s) missing data classification (CUI/PUBLIC/SECRET)"})

        stats["checks_passed"] += max(0, 5 - sum(1 for f in findings if f["design"] == did))

    if not designs:
        findings.append({"severity": "info", "design": "n/a", "check": "no_designs",
                         "message": "No data canvas designs found — schema check skipped"})

    return {"findings": findings, "stats": stats}


def build_report(result: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    stats = result["stats"]

    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]
    infos = [f for f in findings if f["severity"] == "info"]

    gate = "PASS" if not fails else "FAIL"
    lines = [
        "# Schema Check Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '✗ FAIL'}  ",
        f"**Designs checked:** {stats['designs']}  ",
        f"**Nodes:** {stats['total_nodes']}  **Edges:** {stats['total_edges']}",
        f"**Checks passed:** {stats['checks_passed']}  **Failed:** {stats['checks_failed']}",
        "",
    ]

    if fails:
        lines.append("## ✗ Failures (blocking)")
        for f in fails:
            lines.append(f"- **[{f['check']}]** `{f['design'][:8]}` — {f['message']}")
        lines.append("")

    if warns:
        lines.append("## ⚠ Warnings")
        for f in warns:
            lines.append(f"- **[{f['check']}]** `{f['design'][:8]}` — {f['message']}")
        lines.append("")

    if infos:
        lines.append("## ℹ Info")
        for f in infos:
            lines.append(f"- {f['message']}")
        lines.append("")

    if not findings:
        lines += ["## ✓ All Checks Passed", "", "No schema issues detected."]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Schema Checker")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = check_schema(args.project_id)
        report_md = build_report(result, args.project_id)

        artifacts_dir = _ROOT / "data" / "studio_artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"schema_check_{uuid.uuid4().hex[:8]}.md"
        fpath = artifacts_dir / fname
        fpath.write_text(report_md, encoding="utf-8")

        fails = [f for f in result["findings"] if f["severity"] == "fail"]
        output = {
            "status": "success" if not fails else "failed",
            "gate": "PASS" if not fails else "FAIL",
            "findings": len(result["findings"]),
            "failures": len(fails),
            "stats": result["stats"],
            "artifacts": [
                {"name": "Schema Check Report", "path": f"data/studio_artifacts/{fname}", "type": "md"}
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if not fails else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
