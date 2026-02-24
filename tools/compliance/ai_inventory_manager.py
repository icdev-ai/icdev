#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""AI Use Case Inventory Manager — OMB M-25-21 compliance.

Maintains a public AI use case inventory as required by OMB M-25-21.
Registers AI components, classifies risk level (minimal, high-impact,
safety-impacting), tracks oversight roles and appeal mechanisms.

ADR D312: Follows OMB M-25-21 schema for government reporting.

Usage:
    python tools/compliance/ai_inventory_manager.py --project-id proj-123 --register --name "Claude Sonnet" --json
    python tools/compliance/ai_inventory_manager.py --project-id proj-123 --list --json
    python tools/compliance/ai_inventory_manager.py --project-id proj-123 --export --json
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

VALID_RISK_LEVELS = ("minimal_risk", "high_impact", "safety_impacting")


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ai_use_case_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            purpose TEXT,
            risk_level TEXT DEFAULT 'minimal_risk'
                CHECK(risk_level IN ('minimal_risk', 'high_impact', 'safety_impacting')),
            classification TEXT DEFAULT 'CUI',
            deployment_status TEXT DEFAULT 'development',
            responsible_official TEXT,
            oversight_role TEXT,
            appeal_mechanism TEXT,
            last_assessed TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_ai_inventory_project
            ON ai_use_case_inventory(project_id);
    """)
    conn.commit()


def register_ai_component(
    project_id: str,
    name: str,
    purpose: str = "",
    risk_level: str = "minimal_risk",
    classification: str = "CUI",
    deployment_status: str = "development",
    responsible_official: str = "",
    oversight_role: str = "",
    appeal_mechanism: str = "",
    db_path: Path = DB_PATH,
) -> Dict:
    """Register an AI component in the inventory."""
    if risk_level not in VALID_RISK_LEVELS:
        raise ValueError(f"Invalid risk_level: {risk_level}. Must be one of {VALID_RISK_LEVELS}")

    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT OR REPLACE INTO ai_use_case_inventory
               (project_id, name, purpose, risk_level, classification,
                deployment_status, responsible_official, oversight_role,
                appeal_mechanism, last_assessed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, name, purpose, risk_level, classification,
                deployment_status, responsible_official, oversight_role,
                appeal_mechanism, now, now,
            ),
        )
        conn.commit()

        # Audit trail
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details, classification)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    project_id, "ai_inventory_registered",
                    "icdev-compliance-engine",
                    f"Registered AI component: {name}",
                    json.dumps({"name": name, "risk_level": risk_level}),
                    "CUI",
                ),
            )
            conn.commit()
        except Exception:
            pass

        return {
            "status": "registered",
            "project_id": project_id,
            "name": name,
            "risk_level": risk_level,
            "classification": classification,
        }
    finally:
        conn.close()


def list_inventory(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """List all AI components in the inventory."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT * FROM ai_use_case_inventory WHERE project_id = ? ORDER BY name",
            (project_id,),
        ).fetchall()

        items = [dict(r) for r in rows]
        risk_counts = {}
        for item in items:
            rl = item.get("risk_level", "minimal_risk")
            risk_counts[rl] = risk_counts.get(rl, 0) + 1

        return {
            "project_id": project_id,
            "total": len(items),
            "risk_counts": risk_counts,
            "items": items,
        }
    finally:
        conn.close()


def export_inventory(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """Export inventory in OMB M-25-21 reporting format."""
    inventory = list_inventory(project_id, db_path)

    export = {
        "report_type": "OMB M-25-21 AI Use Case Inventory",
        "classification": "CUI // SP-CTI",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "summary": {
            "total_ai_components": inventory["total"],
            "high_impact_count": inventory["risk_counts"].get("high_impact", 0),
            "safety_impacting_count": inventory["risk_counts"].get("safety_impacting", 0),
            "minimal_risk_count": inventory["risk_counts"].get("minimal_risk", 0),
        },
        "use_cases": [
            {
                "name": item["name"],
                "purpose": item.get("purpose", ""),
                "risk_level": item.get("risk_level", ""),
                "deployment_status": item.get("deployment_status", ""),
                "responsible_official": item.get("responsible_official", ""),
                "oversight_role": item.get("oversight_role", ""),
                "appeal_mechanism": item.get("appeal_mechanism", ""),
                "last_assessed": item.get("last_assessed", ""),
            }
            for item in inventory["items"]
        ],
    }

    return export


def main():
    parser = argparse.ArgumentParser(description="AI Use Case Inventory Manager (OMB M-25-21)")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--register", action="store_true", help="Register an AI component")
    parser.add_argument("--name", help="AI component name")
    parser.add_argument("--purpose", default="", help="Component purpose")
    parser.add_argument("--risk-level", default="minimal_risk", choices=VALID_RISK_LEVELS)
    parser.add_argument("--responsible-official", default="")
    parser.add_argument("--oversight-role", default="")
    parser.add_argument("--appeal-mechanism", default="")
    parser.add_argument("--list", action="store_true", help="List inventory")
    parser.add_argument("--export", action="store_true", help="Export for OMB reporting")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        if args.register:
            if not args.name:
                print("ERROR: --name required for registration", file=sys.stderr)
                sys.exit(1)
            result = register_ai_component(
                args.project_id, args.name, args.purpose, args.risk_level,
                responsible_official=args.responsible_official,
                oversight_role=args.oversight_role,
                appeal_mechanism=args.appeal_mechanism,
                db_path=db,
            )
        elif args.export:
            result = export_inventory(args.project_id, db)
        elif args.list:
            result = list_inventory(args.project_id, db)
        else:
            result = list_inventory(args.project_id, db)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if args.register:
                print(f"Registered: {result['name']} ({result['risk_level']})")
            else:
                total = result.get("total", result.get("summary", {}).get("total_ai_components", 0))
                print(f"AI Inventory for {args.project_id}: {total} components")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
