#!/usr/bin/env python3
# CUI // SP-CTI
"""Query the immutable audit trail. Read-only operations only."""

import argparse
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def query_by_project(project_id: str, limit: int = 50, db_path: Path = None) -> list:
    """Get audit entries for a project."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        """SELECT * FROM audit_trail WHERE project_id = ?
           ORDER BY created_at DESC LIMIT ?""",
        (project_id, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def query_by_type(event_type: str, limit: int = 50, db_path: Path = None) -> list:
    """Get audit entries by event type."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        """SELECT * FROM audit_trail WHERE event_type = ?
           ORDER BY created_at DESC LIMIT ?""",
        (event_type, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def query_by_actor(actor: str, limit: int = 50, db_path: Path = None) -> list:
    """Get audit entries by actor."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        """SELECT * FROM audit_trail WHERE actor = ?
           ORDER BY created_at DESC LIMIT ?""",
        (actor, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def query_recent(limit: int = 50, db_path: Path = None) -> list:
    """Get most recent audit entries."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM audit_trail ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def verify_completeness(project_id: str, db_path: Path = None) -> dict:
    """Verify audit trail completeness for a project.
    Checks that key lifecycle events exist."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()

    required_events = [
        "project_created",
        "test_written",
        "test_executed",
        "security_scan",
        "compliance_check",
    ]

    results = {}
    for event in required_events:
        c.execute(
            "SELECT COUNT(*) FROM audit_trail WHERE project_id = ? AND event_type = ?",
            (project_id, event),
        )
        count = c.fetchone()[0]
        results[event] = {"present": count > 0, "count": count}

    conn.close()
    all_present = all(r["present"] for r in results.values())
    return {"complete": all_present, "events": results}


def format_entries(entries: list) -> str:
    """Format audit entries for display."""
    lines = []
    for e in entries:
        ts = e.get("created_at", "")
        lines.append(f"[{ts}] ({e['event_type']}) {e['actor']}: {e['action']}")
        if e.get("details"):
            lines.append(f"  Details: {e['details']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Query audit trail")
    parser.add_argument("--project", help="Filter by project ID")
    parser.add_argument("--type", help="Filter by event type")
    parser.add_argument("--actor", help="Filter by actor")
    parser.add_argument("--limit", type=int, default=50, help="Max results")
    parser.add_argument("--verify-completeness", action="store_true", help="Verify audit completeness for project")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    if args.verify_completeness:
        if not args.project:
            print("Error: --project required with --verify-completeness")
            return
        result = verify_completeness(args.project)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            status = "COMPLETE" if result["complete"] else "INCOMPLETE"
            print(f"Audit Trail Completeness: {status}")
            for event, info in result["events"].items():
                mark = "+" if info["present"] else "X"
                print(f"  [{mark}] {event}: {info['count']} entries")
        return

    if args.project:
        entries = query_by_project(args.project, args.limit)
    elif args.type:
        entries = query_by_type(args.type, args.limit)
    elif args.actor:
        entries = query_by_actor(args.actor, args.limit)
    else:
        entries = query_recent(args.limit)

    if args.format == "json":
        print(json.dumps(entries, indent=2))
    else:
        print(format_entries(entries) if entries else "No audit entries found.")


if __name__ == "__main__":
    main()
