#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""AI Incident Response — Phase 49.

Tracks AI-specific incidents requiring corrective action. Provides evidence
for OMB M-25-21 (M25-RISK-4) and GAO-21-519SP (GAO-MON-3) assessors.

ADR D318: Separate from audit_trail — incidents are AI-specific events
requiring corrective action, not generic audit events.

Usage:
    python tools/compliance/ai_incident_response.py --project-id proj-123 --log --type bias_detected --severity high --description "Bias found in classifier" --json
    python tools/compliance/ai_incident_response.py --project-id proj-123 --update --incident-id 1 --corrective-action "Retrained model" --status mitigated --json
    python tools/compliance/ai_incident_response.py --project-id proj-123 --open --json
    python tools/compliance/ai_incident_response.py --project-id proj-123 --stats --json
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

VALID_INCIDENT_TYPES = (
    "confabulation", "bias_detected", "unauthorized_access",
    "model_drift", "data_breach", "safety_violation",
    "appeal_escalation", "other",
)
VALID_SEVERITIES = ("critical", "high", "medium", "low")
VALID_STATUSES = ("open", "investigating", "mitigated", "resolved", "closed")


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ai_incident_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            ai_system TEXT,
            severity TEXT DEFAULT 'medium',
            description TEXT NOT NULL,
            corrective_action TEXT,
            status TEXT DEFAULT 'open',
            reported_by TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ai_incident_project
            ON ai_incident_log(project_id);
    """)
    conn.commit()


def log_incident(
    project_id: str,
    incident_type: str,
    description: str,
    ai_system: str = "",
    severity: str = "medium",
    reported_by: str = "",
    db_path: Path = DB_PATH,
) -> Dict:
    """Log an AI incident."""
    if incident_type not in VALID_INCIDENT_TYPES:
        raise ValueError(f"Invalid incident_type: {incident_type}. Must be one of {VALID_INCIDENT_TYPES}")
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Invalid severity: {severity}. Must be one of {VALID_SEVERITIES}")

    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        conn.execute(
            """INSERT INTO ai_incident_log
               (project_id, incident_type, ai_system, severity,
                description, status, reported_by)
               VALUES (?, ?, ?, ?, ?, 'open', ?)""",
            (project_id, incident_type, ai_system, severity,
             description, reported_by),
        )
        conn.commit()
        incident_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Audit trail
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details, classification)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (project_id, "ai_incident_logged",
                 reported_by or "icdev-compliance-engine",
                 f"AI incident logged: {incident_type} ({severity})",
                 json.dumps({"incident_id": incident_id, "type": incident_type, "severity": severity}),
                 "CUI"),
            )
            conn.commit()
        except Exception:
            pass

        return {
            "status": "logged",
            "incident_id": incident_id,
            "project_id": project_id,
            "incident_type": incident_type,
            "severity": severity,
        }
    finally:
        conn.close()


def update_incident(
    incident_id: int,
    corrective_action: str = "",
    status: str = "",
    db_path: Path = DB_PATH,
) -> Dict:
    """Update an incident with corrective action or status change."""
    if status and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Must be one of {VALID_STATUSES}")

    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT project_id, incident_type FROM ai_incident_log WHERE id = ?",
            (incident_id,),
        ).fetchone()
        if not row:
            return {"error": f"Incident {incident_id} not found"}

        updates = []
        params = []
        if corrective_action:
            updates.append("corrective_action = ?")
            params.append(corrective_action)
        if status:
            updates.append("status = ?")
            params.append(status)

        if updates:
            params.append(incident_id)
            conn.execute(
                f"UPDATE ai_incident_log SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()

        return {
            "status": "updated",
            "incident_id": incident_id,
            "new_status": status or "unchanged",
            "corrective_action": corrective_action or "unchanged",
        }
    finally:
        conn.close()


def get_open_incidents(
    project_id: str, severity: str = "", db_path: Path = DB_PATH,
) -> Dict:
    """List open incidents, optionally filtered by severity."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        query = """SELECT * FROM ai_incident_log
                   WHERE project_id = ? AND status IN ('open', 'investigating')"""
        params: list = [project_id]
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        query += " ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, created_at DESC"

        rows = conn.execute(query, params).fetchall()
        return {
            "project_id": project_id,
            "total": len(rows),
            "incidents": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def get_incident_stats(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """Get incident statistics for dashboard."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)

        def _count(where, params):
            try:
                return conn.execute(
                    f"SELECT COUNT(*) as cnt FROM ai_incident_log WHERE {where}",
                    params,
                ).fetchone()["cnt"]
            except Exception:
                return 0

        pid = project_id
        total = _count("project_id = ?", (pid,))
        open_count = _count("project_id = ? AND status IN ('open', 'investigating')", (pid,))
        critical = _count("project_id = ? AND severity = 'critical' AND status != 'closed'", (pid,))
        resolved = _count("project_id = ? AND status IN ('resolved', 'closed')", (pid,))

        # By type
        type_counts = {}
        try:
            rows = conn.execute(
                """SELECT incident_type, COUNT(*) as cnt FROM ai_incident_log
                   WHERE project_id = ? GROUP BY incident_type""",
                (pid,),
            ).fetchall()
            type_counts = {r["incident_type"]: r["cnt"] for r in rows}
        except Exception:
            pass

        return {
            "project_id": project_id,
            "total": total,
            "open": open_count,
            "critical_unresolved": critical,
            "resolved": resolved,
            "by_type": type_counts,
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="AI Incident Response (Phase 49)")
    parser.add_argument("--project-id", required=True)

    parser.add_argument("--log", action="store_true", help="Log a new incident")
    parser.add_argument("--update", action="store_true", help="Update an incident")
    parser.add_argument("--open", action="store_true", help="List open incidents")
    parser.add_argument("--stats", action="store_true", help="Get incident statistics")

    parser.add_argument("--incident-id", type=int)
    parser.add_argument("--type", dest="incident_type", default="other", choices=VALID_INCIDENT_TYPES)
    parser.add_argument("--ai-system", default="")
    parser.add_argument("--severity", default="medium", choices=VALID_SEVERITIES)
    parser.add_argument("--description", default="")
    parser.add_argument("--corrective-action", default="")
    parser.add_argument("--status", default="", choices=[""] + list(VALID_STATUSES))
    parser.add_argument("--reported-by", default="")

    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        if args.log:
            if not args.description:
                print("ERROR: --description required", file=sys.stderr)
                sys.exit(1)
            result = log_incident(
                args.project_id, args.incident_type, args.description,
                args.ai_system, args.severity, args.reported_by, db)
        elif args.update:
            if not args.incident_id:
                print("ERROR: --incident-id required", file=sys.stderr)
                sys.exit(1)
            result = update_incident(
                args.incident_id, args.corrective_action, args.status, db)
        elif args.open:
            result = get_open_incidents(args.project_id, db_path=db)
        else:
            result = get_incident_stats(args.project_id, db)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if "incidents" in result:
                print(f"Open Incidents: {result['total']}")
                for inc in result["incidents"]:
                    print(f"  [{inc['severity']}] {inc['incident_type']}: {inc['description'][:80]}")
            elif "total" in result and "by_type" in result:
                print(f"Incident Stats: {result['total']} total, {result['open']} open, {result['critical_unresolved']} critical")
            else:
                print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
