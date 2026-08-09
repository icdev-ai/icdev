"""Migration Planner — DDC Workflow Step 3.

Generates a data migration plan based on canvas designs: wave sequencing,
dependency ordering, estimated effort, rollback steps, and NIST contingency notes.
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

# Migration wave classification by node type
_WAVE_MAP = {
    "ctrl": 1,      # Controls/policies — set up first
    "bnd": 1,       # Boundaries/zones — set up first
    "ent": 2,       # Entities/stores — core data layer
    "flow": 3,      # Data flows — connect after stores exist
    "col": 3,       # Columns/fields — after entity schemas
    "twin": 4,      # Digital twins — last (depend on everything)
}

_EFFORT_MAP = {
    "ent-rds": 3, "ent-s3": 1, "ent-db": 3, "ent-warehouse": 5,
    "ent-kafka": 4, "ent-redis": 2, "ent-elasticsearch": 3,
    "flow": 1, "ctrl": 1, "bnd": 1, "col": 1, "twin": 2,
}


def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


def build_migration_plan(project_id: str) -> dict:
    conn = _get_conn()
    try:
        nodes = conn.execute(
            "SELECT design_id, node_id, node_type, label, classification FROM data_nodes ORDER BY design_id"
        ).fetchall()
        edges = conn.execute(
            "SELECT design_id, source_id, target_id, edge_type FROM data_edges"
        ).fetchall()
        kg_nodes = conn.execute(
            "SELECT canvas, design_id, node_id, node_type, label FROM canvas_kg_nodes WHERE canvas='ddc'"
        ).fetchall()
    finally:
        conn.close()

    designs: dict = {}
    for row in (nodes or []):
        did = row[0]
        designs.setdefault(did, {"nodes": [], "edges": []})
        designs[did]["nodes"].append({"id": row[1], "type": row[2], "label": row[3], "classification": row[4]})
    for row in (edges or []):
        did = row[0]
        designs.setdefault(did, {"nodes": [], "edges": []})
        designs[did]["edges"].append({"source": row[1], "target": row[2], "type": row[3]})
    if not nodes and kg_nodes:
        for row in (kg_nodes or []):
            did = row[1]
            designs.setdefault(did, {"nodes": [], "edges": []})
            designs[did]["nodes"].append({"id": row[2], "type": row[3], "label": row[4], "classification": "CUI"})

    plans = []
    for did, d in designs.items():
        waves: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
        total_effort = 0
        for n in d["nodes"]:
            ntype = n.get("type") or ""
            prefix = ntype.split("-")[0] if "-" in ntype else ntype
            wave = _WAVE_MAP.get(prefix, 2)
            effort = _EFFORT_MAP.get(ntype, _EFFORT_MAP.get(prefix, 1))
            total_effort += effort
            waves[wave].append({"label": n.get("label") or n["id"], "type": ntype,
                                 "effort_days": effort, "classification": n.get("classification")})

        plans.append({
            "design_id": did,
            "waves": {k: v for k, v in waves.items() if v},
            "total_nodes": len(d["nodes"]),
            "total_edges": len(d["edges"]),
            "total_effort_days": total_effort,
        })

    return {"designs": plans, "project_id": project_id}


def build_report(plan: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Data Migration Plan",
        f"**Generated:** {ts}  ",
        f"**Project:** {plan['project_id']}  ",
        f"**Designs:** {len(plan['designs'])}",
        "",
    ]

    if not plan["designs"]:
        lines += [
            "## No Designs Found",
            "",
            "No data canvas designs exist yet. Create and save a DDC design to generate a migration plan.",
            "",
            "## Template Migration Sequence",
            "",
            "When designs are available, migration will follow this sequence:",
            "",
            "| Wave | Scope | Typical Components |",
            "|------|-------|-------------------|",
            "| 1 | Foundation | Boundaries, Access Controls, KMS keys |",
            "| 2 | Core Data | Entities, Databases, Object Stores |",
            "| 3 | Connective | Data Flows, ETL pipelines, Column schemas |",
            "| 4 | Observability | Digital Twins, Monitoring, Audit logs |",
            "",
            "## NIST SP 800-34 Contingency Notes",
            "",
            "- All migration waves require a documented rollback procedure",
            "- RTO target: restore previous wave within 4 hours",
            "- RPO target: no data loss beyond last committed transaction",
            "- Human approval (DBA) required before wave promotion in production",
        ]
        return "\n".join(lines)

    for design_plan in plan["designs"]:
        did = design_plan["design_id"]
        effort = design_plan["total_effort_days"]
        lines += [
            f"## Design `{did}`",
            f"**Total nodes:** {design_plan['total_nodes']}  "
            f"**Estimated effort:** {effort} dev-day(s)",
            "",
        ]

        for wave_num in sorted(design_plan["waves"]):
            items = design_plan["waves"][wave_num]
            wave_effort = sum(i["effort_days"] for i in items)
            lines += [
                f"### Wave {wave_num} — {['Foundation', 'Core Data', 'Connective Layer', 'Observability'][wave_num-1]}",
                f"*{len(items)} component(s), ~{wave_effort} day(s)*",
                "",
                "| Component | Type | Effort | Classification |",
                "|-----------|------|--------|----------------|",
            ]
            for item in items:
                lines.append(
                    f"| {item['label']} | `{item['type']}` | {item['effort_days']}d | {item.get('classification') or '—'} |"
                )
            lines += [
                "",
                "**Rollback:** Restore from wave snapshot before promoting to next wave.",
                "",
            ]

        lines += [
            "### NIST SP 800-34 Contingency Notes",
            "- Obtain DBA approval gate before each wave promotion in production",
            "- Validate data integrity checksums after each wave",
            "- Maintain parallel-run window of ≥24h before cutover",
            "",
        ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Migration Planner")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        plan = build_migration_plan(args.project_id)
        report_md = build_report(plan)

        artifacts_dir = _ROOT / "data" / "studio_artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"migration_plan_{uuid.uuid4().hex[:8]}.md"
        fpath = artifacts_dir / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        output = {
            "status": "success",
            "designs_planned": len(plan["designs"]),
            "total_effort_days": sum(d["total_effort_days"] for d in plan["designs"]),
            "artifacts": [
                {"name": "Migration Plan", "path": f"data/studio_artifacts/{fname}", "type": "md"}
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
