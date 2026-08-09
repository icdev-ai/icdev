"""Schema Checker — DDC Workflow Step 2.

Validates data canvas schema: node types, edge integrity, classification
coverage, and NIST 800-53 SC-28 encryption checks.
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

_SENSITIVE_TYPES = {"ent-pii", "ent-phi", "ent-cui", "ent-classified", "ent-vector", "ent-file"}
_ENCRYPTION_TYPES = {"ctrl-encrypt", "ctrl-kms", "ctrl-cmk", "ctrl-fips", "ctrl-encryption"}
_BACKUP_TYPES = {"ctrl-backup", "ctrl-snapshot", "ctrl-dr", "ctrl-backup-policy"}
_VALID_EDGE_TYPES = {
    "flow", "data-flow", "reads", "writes", "streams", "transforms",
    "depends-on", "provision", "monitors", "classifies", "ingest", "query",
}


def check_schema(project_id: str) -> dict:
    from tools.data.canvas_reader import load_designs
    designs = load_designs(project_id)

    findings: list[dict] = []
    stats = {"total_nodes": 0, "total_edges": 0, "designs": len(designs),
             "checks_passed": 0, "checks_failed": 0}

    for did, d in designs.items():
        nodes = d["nodes"]
        edges = d["edges"]
        node_ids = {n["id"] for n in nodes}
        types = {n["type"] for n in nodes}
        stats["total_nodes"] += len(nodes)
        stats["total_edges"] += len(edges)

        # Check 1: required fields
        for n in nodes:
            for f in ("label", "type"):
                if not n.get(f):
                    findings.append({"severity": "warn", "design": did, "check": "required_fields",
                                     "message": f"Node `{n['id'][:12]}` missing `{f}`"})
                    stats["checks_failed"] += 1

        # Check 2: edge integrity
        for e in edges:
            if e["source"] not in node_ids or e["target"] not in node_ids:
                findings.append({"severity": "fail", "design": did, "check": "edge_integrity",
                                 "message": f"Broken edge: {e['source'][:12]} → {e['target'][:12]}"})
                stats["checks_failed"] += 1

        # Check 3: encryption for sensitive data
        has_sensitive = bool(types & _SENSITIVE_TYPES)
        has_encryption = bool(types & _ENCRYPTION_TYPES)
        if has_sensitive and not has_encryption:
            findings.append({"severity": "fail", "design": did, "check": "encryption_coverage",
                             "message": "Sensitive data nodes found with no encryption control — NIST SC-28 violation"})
            stats["checks_failed"] += 1
        elif has_sensitive:
            stats["checks_passed"] += 1

        # Check 4: backup for storage nodes
        has_storage = any(
            any(k in (n.get("type") or "") for k in ("db", "storage", "file", "ent-", "vector", "cache", "graph"))
            for n in nodes
        )
        has_backup = bool(types & _BACKUP_TYPES)
        if has_storage and not has_backup:
            findings.append({"severity": "warn", "design": did, "check": "backup_policy",
                             "message": "Storage nodes found with no backup control node"})
            stats["checks_failed"] += 1
        elif has_storage:
            stats["checks_passed"] += 1

        # Check 5: orphaned nodes
        if nodes and not edges:
            findings.append({"severity": "warn", "design": did, "check": "connectivity",
                             "message": "No edges defined — data flow relationships are missing"})

        stats["checks_passed"] += max(0, 3 - sum(1 for f in findings if f["design"] == did and f["severity"] == "fail"))

    if not designs:
        findings.append({"severity": "info", "design": "n/a", "check": "no_designs",
                         "message": "No canvas designs found — schema check skipped"})

    return {"findings": findings, "stats": stats}


def build_report(result: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    stats = result["stats"]
    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]
    gate = "PASS" if not fails else "FAIL"

    lines = [
        "# Schema Check Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '✗ FAIL'}  ",
        f"**Designs:** {stats['designs']}  **Nodes:** {stats['total_nodes']}  **Edges:** {stats['total_edges']}",
        f"**Checks passed:** {stats['checks_passed']}  **Failed:** {stats['checks_failed']}",
        "",
    ]
    if fails:
        lines.append("## ✗ Failures (blocking)")
        for f in fails:
            lines.append(f"- **[{f['check']}]** `{f['design'][:12]}` — {f['message']}")
        lines.append("")
    if warns:
        lines.append("## ⚠ Warnings")
        for f in warns:
            lines.append(f"- **[{f['check']}]** `{f['design'][:12]}` — {f['message']}")
        lines.append("")
    if not findings:
        lines += ["## ✓ All Checks Passed", "", "No schema issues detected."]
    return "\n".join(lines)


def build_schema_json(result: dict) -> dict:
    return {
        "gate": "PASS" if not [f for f in result["findings"] if f["severity"] == "fail"] else "FAIL",
        "stats": result["stats"],
        "findings": result["findings"],
    }


def main():
    parser = argparse.ArgumentParser(description="Schema Checker")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = check_schema(args.project_id)
        report_md = build_report(result, args.project_id)
        schema_json = build_schema_json(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        md_path = _ARTIFACTS_DIR / f"schema_report_{uid}.md"
        json_path = _ARTIFACTS_DIR / f"schema_{uid}.json"
        md_path.write_text(report_md, encoding="utf-8", newline="")
        json_path.write_text(json.dumps(schema_json, indent=2), encoding="utf-8", newline="")

        fails = [f for f in result["findings"] if f["severity"] == "fail"]
        output = {
            "status": "success" if not fails else "failed",
            "gate": "PASS" if not fails else "FAIL",
            "findings": len(result["findings"]),
            "failures": len(fails),
            "stats": result["stats"],
            "artifacts": [
                {"name": "Schema Check Report", "path": md_path.relative_to(_ROOT).as_posix(), "type": "md"},
                {"name": "Schema JSON", "path": json_path.relative_to(_ROOT).as_posix(), "type": "json"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if not fails else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
