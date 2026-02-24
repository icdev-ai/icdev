#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""AI Reassessment Scheduler — Phase 49.

Tracks periodic reassessment schedules for AI systems as required by
OMB M-25-21 (M25-INV-3) and GAO-21-519SP (GAO-MON-4).

Usage:
    python tools/compliance/ai_reassessment_scheduler.py --project-id proj-123 --create --ai-system "Classifier" --frequency annual --json
    python tools/compliance/ai_reassessment_scheduler.py --project-id proj-123 --overdue --json
    python tools/compliance/ai_reassessment_scheduler.py --project-id proj-123 --complete --schedule-id 1 --json
    python tools/compliance/ai_reassessment_scheduler.py --project-id proj-123 --summary --json
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

VALID_FREQUENCIES = ("quarterly", "semi_annual", "annual", "biennial")
FREQUENCY_DAYS = {"quarterly": 90, "semi_annual": 182, "annual": 365, "biennial": 730}


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ai_reassessment_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            ai_system TEXT NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'annual',
            next_due TEXT,
            last_completed TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, ai_system)
        );
        CREATE INDEX IF NOT EXISTS idx_ai_reassess_project
            ON ai_reassessment_schedule(project_id);
    """)
    conn.commit()


def create_schedule(
    project_id: str,
    ai_system: str,
    frequency: str = "annual",
    next_due: str = "",
    db_path: Path = DB_PATH,
) -> Dict:
    """Create or update a reassessment schedule."""
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(f"Invalid frequency: {frequency}. Must be one of {VALID_FREQUENCIES}")

    if not next_due:
        next_due = (datetime.now(timezone.utc) + timedelta(days=FREQUENCY_DAYS[frequency])).strftime("%Y-%m-%d")

    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        conn.execute(
            """INSERT OR REPLACE INTO ai_reassessment_schedule
               (project_id, ai_system, frequency, next_due)
               VALUES (?, ?, ?, ?)""",
            (project_id, ai_system, frequency, next_due),
        )
        conn.commit()

        return {
            "status": "scheduled",
            "project_id": project_id,
            "ai_system": ai_system,
            "frequency": frequency,
            "next_due": next_due,
        }
    finally:
        conn.close()


def check_overdue(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """Find overdue reassessments."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT * FROM ai_reassessment_schedule
               WHERE project_id = ? AND next_due < ?
               ORDER BY next_due ASC""",
            (project_id, now),
        ).fetchall()

        overdue = []
        for r in rows:
            days_overdue = (datetime.now(timezone.utc) - datetime.strptime(r["next_due"], "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
            overdue.append({
                "id": r["id"],
                "ai_system": r["ai_system"],
                "frequency": r["frequency"],
                "next_due": r["next_due"],
                "days_overdue": days_overdue,
                "last_completed": r["last_completed"],
            })

        return {
            "project_id": project_id,
            "total_overdue": len(overdue),
            "overdue": overdue,
        }
    finally:
        conn.close()


def complete_reassessment(
    schedule_id: int, db_path: Path = DB_PATH,
) -> Dict:
    """Mark a reassessment as completed and set next due date."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM ai_reassessment_schedule WHERE id = ?",
            (schedule_id,),
        ).fetchone()
        if not row:
            return {"error": f"Schedule {schedule_id} not found"}

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        frequency = row["frequency"]
        next_due = (datetime.now(timezone.utc) + timedelta(days=FREQUENCY_DAYS.get(frequency, 365))).strftime("%Y-%m-%d")

        conn.execute(
            """UPDATE ai_reassessment_schedule
               SET last_completed = ?, next_due = ?
               WHERE id = ?""",
            (now, next_due, schedule_id),
        )
        conn.commit()

        return {
            "status": "completed",
            "schedule_id": schedule_id,
            "ai_system": row["ai_system"],
            "completed_date": now,
            "next_due": next_due,
        }
    finally:
        conn.close()


def get_schedule_summary(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """Get reassessment schedule summary."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        rows = conn.execute(
            """SELECT * FROM ai_reassessment_schedule
               WHERE project_id = ? ORDER BY next_due ASC""",
            (project_id,),
        ).fetchall()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        schedules = []
        overdue_count = 0
        for r in rows:
            is_overdue = r["next_due"] and r["next_due"] < now
            if is_overdue:
                overdue_count += 1
            schedules.append({
                "id": r["id"],
                "ai_system": r["ai_system"],
                "frequency": r["frequency"],
                "next_due": r["next_due"],
                "last_completed": r["last_completed"],
                "overdue": is_overdue,
            })

        return {
            "project_id": project_id,
            "total_schedules": len(schedules),
            "overdue_count": overdue_count,
            "schedules": schedules,
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="AI Reassessment Scheduler (Phase 49)")
    parser.add_argument("--project-id", required=True)

    parser.add_argument("--create", action="store_true", help="Create schedule")
    parser.add_argument("--overdue", action="store_true", help="Check overdue")
    parser.add_argument("--complete", action="store_true", help="Complete reassessment")
    parser.add_argument("--summary", action="store_true", help="Schedule summary")

    parser.add_argument("--ai-system", default="")
    parser.add_argument("--frequency", default="annual", choices=VALID_FREQUENCIES)
    parser.add_argument("--next-due", default="")
    parser.add_argument("--schedule-id", type=int)

    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        if args.create:
            if not args.ai_system:
                print("ERROR: --ai-system required", file=sys.stderr)
                sys.exit(1)
            result = create_schedule(args.project_id, args.ai_system,
                                     args.frequency, args.next_due, db)
        elif args.overdue:
            result = check_overdue(args.project_id, db)
        elif args.complete:
            if not args.schedule_id:
                print("ERROR: --schedule-id required", file=sys.stderr)
                sys.exit(1)
            result = complete_reassessment(args.schedule_id, db)
        else:
            result = get_schedule_summary(args.project_id, db)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if "overdue" in result and "total_overdue" in result:
                print(f"Overdue Reassessments: {result['total_overdue']}")
                for item in result["overdue"]:
                    print(f"  {item['ai_system']}: {item['days_overdue']} days overdue (due {item['next_due']})")
            elif "schedules" in result:
                print(f"Schedules: {result['total_schedules']} ({result['overdue_count']} overdue)")
            else:
                print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
