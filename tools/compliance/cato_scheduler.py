#!/usr/bin/env python3
# ////////////////////////////////////////////////////////////////////
# CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
# Distribution: Distribution D -- Authorized DoD Personnel Only
# ////////////////////////////////////////////////////////////////////
"""Schedule-based evidence collection manager for Continuous ATO (cATO).

Manages the cadence of evidence collection by generating schedules,
identifying overdue collections, executing due collections, and
producing calendar views. Works in concert with cato_monitor.py
to maintain continuous compliance evidence freshness.

Queries the cato_evidence table for collection history and uses
automation_frequency to determine next-due dates. Delegates actual
evidence collection to cato_monitor.collect_evidence().
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Import cato_monitor functions for evidence collection
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from cato_monitor import (
        collect_evidence,
        check_evidence_freshness,
        _get_connection,
        _verify_project,
        _log_audit_event,
        EXPIRY_WINDOWS,
        AUTOMATION_FREQUENCIES,
    )
except ImportError:
    # Fallback: define minimal versions if import fails
    def _get_connection(db_path=None):
        path = db_path or DB_PATH
        if not Path(path).exists():
            raise FileNotFoundError(
                f"Database not found: {path}\n"
                "Run: python tools/db/init_icdev_db.py"
            )
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn

    def _verify_project(conn, project_id):
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Project '{project_id}' not found in database.")
        return dict(row)

    def _log_audit_event(conn, project_id, action, details):
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details, classification)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (project_id, "cato_evidence_collected", "icdev-cato-scheduler",
                 action, json.dumps(details, default=str), "CUI"),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: Could not log audit event: {e}", file=sys.stderr)

    EXPIRY_WINDOWS = {
        "continuous": 1, "daily": 2, "weekly": 14,
        "monthly": 45, "per_change": 30, "manual": 90,
    }
    AUTOMATION_FREQUENCIES = (
        "continuous", "daily", "weekly", "monthly", "per_change", "manual",
    )

    collect_evidence = None
    check_evidence_freshness = None


# Frequency to collection interval (how often we should collect, in days)
COLLECTION_INTERVALS = {
    "continuous": 0.5,    # every 12 hours
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "per_change": 0,      # triggered by change events, not scheduled
    "manual": 0,          # not auto-scheduled
}


def _compute_next_due(last_collected_str, automation_frequency):
    """Compute the next collection due date from last collected time.

    Args:
        last_collected_str: ISO-format datetime string of last collection.
        automation_frequency: One of AUTOMATION_FREQUENCIES.

    Returns:
        datetime object for next due date, or None if not schedulable.
    """
    interval_days = COLLECTION_INTERVALS.get(automation_frequency, 0)
    if interval_days <= 0:
        return None  # Not auto-scheduled

    try:
        last_collected = datetime.fromisoformat(last_collected_str)
    except (ValueError, TypeError):
        last_collected = datetime.utcnow() - timedelta(days=365)

    return last_collected + timedelta(days=interval_days)


def schedule_collections(project_id, db_path=None):
    """Generate a collection schedule based on evidence records.

    Examines all cato_evidence records for the project and computes
    when each item is next due for re-collection based on its
    automation_frequency.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        List of dicts with control_id, evidence_type, evidence_source,
        frequency, next_due, last_collected, is_overdue.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        rows = conn.execute(
            """SELECT id, control_id, evidence_type, evidence_source,
                      collected_at, automation_frequency, status
               FROM cato_evidence
               WHERE project_id = ?
               ORDER BY control_id, evidence_type""",
            (project_id,),
        ).fetchall()

        now = datetime.utcnow()
        schedule = []

        for row in rows:
            freq = row["automation_frequency"] or "manual"
            collected_at = row["collected_at"]
            next_due = _compute_next_due(collected_at, freq)

            is_overdue = False
            next_due_str = None
            if next_due is not None:
                next_due_str = next_due.isoformat()
                is_overdue = now >= next_due

            schedule.append({
                "evidence_id": row["id"],
                "control_id": row["control_id"],
                "evidence_type": row["evidence_type"],
                "evidence_source": row["evidence_source"],
                "frequency": freq,
                "last_collected": collected_at,
                "next_due": next_due_str,
                "is_overdue": is_overdue,
                "current_status": row["status"],
            })

        print(f"cATO schedule: {len(schedule)} items for project {project_id}")
        schedulable = sum(1 for s in schedule if s["next_due"] is not None)
        overdue = sum(1 for s in schedule if s["is_overdue"])
        print(f"  Schedulable: {schedulable}  Overdue: {overdue}")

        return schedule

    finally:
        conn.close()


def run_scheduled_collections(project_id, project_dir=None, db_path=None):
    """Execute all evidence collections that are currently due.

    Iterates over the schedule, identifies overdue items, and calls
    collect_evidence() for each one to refresh the evidence.

    Args:
        project_id: Project identifier.
        project_dir: Optional project directory for file-based evidence.
        db_path: Optional database path override.

    Returns:
        Dict with total_due, collected, failed, and details list.
    """
    if collect_evidence is None:
        raise RuntimeError(
            "cato_monitor.collect_evidence not available. "
            "Ensure cato_monitor.py is importable."
        )

    schedule = schedule_collections(project_id, db_path=db_path)
    due_items = [s for s in schedule if s["is_overdue"]]

    result = {
        "total_due": len(due_items),
        "collected": 0,
        "failed": 0,
        "skipped": 0,
        "details": [],
    }

    for item in due_items:
        try:
            # Determine evidence path
            evidence_path = None
            if project_dir:
                # Try to find a relevant file in the project directory
                scan_dir = Path(project_dir)
                if scan_dir.is_dir():
                    # Search common locations for evidence artifacts
                    for sub in ["security", "compliance", "reports", "test-results"]:
                        sub_dir = scan_dir / sub
                        if sub_dir.is_dir():
                            for f in sorted(sub_dir.iterdir(), reverse=True):
                                if f.is_file():
                                    evidence_path = str(f)
                                    break
                        if evidence_path:
                            break

            evidence_result = collect_evidence(
                project_id=project_id,
                control_id=item["control_id"],
                evidence_type=item["evidence_type"],
                evidence_source=item["evidence_source"],
                evidence_path=evidence_path,
                automation_frequency=item["frequency"],
                db_path=db_path,
            )

            result["collected"] += 1
            result["details"].append({
                "control_id": item["control_id"],
                "evidence_type": item["evidence_type"],
                "evidence_source": item["evidence_source"],
                "status": "collected",
                "collected_at": evidence_result.get("collected_at"),
            })

        except Exception as e:
            result["failed"] += 1
            result["details"].append({
                "control_id": item["control_id"],
                "evidence_type": item["evidence_type"],
                "evidence_source": item["evidence_source"],
                "status": "failed",
                "error": str(e),
            })

    # Audit trail
    conn = _get_connection(db_path)
    try:
        _log_audit_event(conn, project_id, "Scheduled collections executed", {
            "total_due": result["total_due"],
            "collected": result["collected"],
            "failed": result["failed"],
        })
    finally:
        conn.close()

    print(f"cATO scheduled run: {result['total_due']} due, "
          f"{result['collected']} collected, {result['failed']} failed")

    return result


def get_upcoming_collections(project_id, days=30, db_path=None):
    """List evidence collections due in the next N days.

    Args:
        project_id: Project identifier.
        days: Number of days to look ahead (default 30).
        db_path: Optional database path override.

    Returns:
        List of dicts with evidence details and days until due.
    """
    schedule = schedule_collections(project_id, db_path=db_path)
    now = datetime.utcnow()
    cutoff = now + timedelta(days=days)

    upcoming = []
    for item in schedule:
        if item["next_due"] is None:
            continue

        try:
            next_due = datetime.fromisoformat(item["next_due"])
        except (ValueError, TypeError):
            continue

        if now <= next_due <= cutoff:
            days_until = (next_due - now).days
            upcoming.append({
                "evidence_id": item["evidence_id"],
                "control_id": item["control_id"],
                "evidence_type": item["evidence_type"],
                "evidence_source": item["evidence_source"],
                "frequency": item["frequency"],
                "next_due": item["next_due"],
                "days_until_due": days_until,
                "current_status": item["current_status"],
            })

    # Sort by soonest due
    upcoming.sort(key=lambda x: x["days_until_due"])

    print(f"cATO upcoming ({days}d): {len(upcoming)} collections scheduled")

    return upcoming


def get_overdue_collections(project_id, db_path=None):
    """List evidence collections that are past due.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        List of dicts with overdue evidence details and days overdue.
    """
    schedule = schedule_collections(project_id, db_path=db_path)
    now = datetime.utcnow()

    overdue = []
    for item in schedule:
        if not item["is_overdue"] or item["next_due"] is None:
            continue

        try:
            next_due = datetime.fromisoformat(item["next_due"])
            days_overdue = (now - next_due).days
        except (ValueError, TypeError):
            days_overdue = -1

        overdue.append({
            "evidence_id": item["evidence_id"],
            "control_id": item["control_id"],
            "evidence_type": item["evidence_type"],
            "evidence_source": item["evidence_source"],
            "frequency": item["frequency"],
            "next_due": item["next_due"],
            "days_overdue": days_overdue,
            "last_collected": item["last_collected"],
            "current_status": item["current_status"],
        })

    # Sort by most overdue first
    overdue.sort(key=lambda x: x["days_overdue"], reverse=True)

    print(f"cATO overdue: {len(overdue)} collections past due")
    for item in overdue[:10]:
        print(f"  {item['control_id']:<10} {item['evidence_type']:<16} "
              f"{item['days_overdue']}d overdue  [{item['frequency']}]")
    if len(overdue) > 10:
        print(f"  ... and {len(overdue) - 10} more")

    return overdue


def generate_collection_calendar(project_id, db_path=None):
    """Generate a calendar view of upcoming collections grouped by week.

    Produces a 12-week lookahead calendar showing which evidence
    collections are due each week.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with weeks list, each containing start_date, end_date,
        and collections list.
    """
    schedule = schedule_collections(project_id, db_path=db_path)
    now = datetime.utcnow()

    # Build 12-week calendar
    weeks = []
    for week_num in range(12):
        week_start = now + timedelta(weeks=week_num)
        # Align to Monday
        week_start = week_start - timedelta(days=week_start.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)

        week_collections = []
        for item in schedule:
            if item["next_due"] is None:
                continue

            try:
                next_due = datetime.fromisoformat(item["next_due"])
            except (ValueError, TypeError):
                continue

            if week_start <= next_due <= week_end:
                week_collections.append({
                    "evidence_id": item["evidence_id"],
                    "control_id": item["control_id"],
                    "evidence_type": item["evidence_type"],
                    "evidence_source": item["evidence_source"],
                    "frequency": item["frequency"],
                    "due_date": item["next_due"],
                })

        weeks.append({
            "week_number": week_num + 1,
            "start_date": week_start.strftime("%Y-%m-%d"),
            "end_date": week_end.strftime("%Y-%m-%d"),
            "collection_count": len(week_collections),
            "collections": week_collections,
        })

    # Also include overdue items as a separate section
    overdue = get_overdue_collections(project_id, db_path=db_path)

    calendar = {
        "project_id": project_id,
        "generated_at": datetime.utcnow().isoformat(),
        "lookahead_weeks": 12,
        "overdue_count": len(overdue),
        "overdue": overdue,
        "weeks": weeks,
    }

    print(f"cATO calendar generated: 12 weeks, {len(overdue)} overdue items")
    for w in weeks:
        if w["collection_count"] > 0:
            print(f"  Week {w['week_number']} ({w['start_date']} - {w['end_date']}): "
                  f"{w['collection_count']} collections")

    return calendar


# --------------------------------------------------------------------------
# CLI formatting helpers
# --------------------------------------------------------------------------

def _format_calendar_report(calendar):
    """Format collection calendar as a console report."""
    lines = [
        "=" * 70,
        "  cATO EVIDENCE COLLECTION CALENDAR",
        "=" * 70,
        f"  Project: {calendar.get('project_id', 'N/A')}",
        f"  Generated: {calendar.get('generated_at', 'N/A')}",
        "",
    ]

    # Overdue section
    overdue = calendar.get("overdue", [])
    if overdue:
        lines.append(f"  !!! OVERDUE COLLECTIONS: {len(overdue)} !!!")
        lines.append("")
        lines.append(f"  {'Control':<10} {'Type':<16} {'Source':<20} {'Days Overdue'}")
        lines.append(f"  {'-' * 10} {'-' * 16} {'-' * 20} {'-' * 12}")
        for item in overdue[:20]:
            lines.append(
                f"  {item['control_id']:<10} {item['evidence_type']:<16} "
                f"{item['evidence_source']:<20} {item['days_overdue']}d"
            )
        if len(overdue) > 20:
            lines.append(f"  ... and {len(overdue) - 20} more")
        lines.append("")

    # Weekly calendar
    lines.append("  --- 12-Week Lookahead ---")
    lines.append("")

    for week in calendar.get("weeks", []):
        count = week["collection_count"]
        marker = f"  [{count:>3} items]" if count > 0 else "  [       ]"
        lines.append(
            f"  Week {week['week_number']:>2}  "
            f"{week['start_date']} - {week['end_date']}  {marker}"
        )

        if count > 0:
            for coll in week["collections"][:5]:
                lines.append(
                    f"           {coll['control_id']:<10} "
                    f"{coll['evidence_type']:<16} "
                    f"[{coll['frequency']}]"
                )
            if count > 5:
                lines.append(f"           ... and {count - 5} more")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def _format_overdue_report(overdue):
    """Format overdue collections as a console report."""
    if not overdue:
        return "No overdue evidence collections."

    lines = [
        "=" * 70,
        "  cATO OVERDUE COLLECTIONS",
        "=" * 70,
        f"  Total overdue: {len(overdue)}",
        "",
        f"  {'Control':<10} {'Type':<16} {'Source':<20} {'Frequency':<12} {'Days Overdue'}",
        f"  {'-' * 10} {'-' * 16} {'-' * 20} {'-' * 12} {'-' * 12}",
    ]

    for item in overdue:
        lines.append(
            f"  {item['control_id']:<10} {item['evidence_type']:<16} "
            f"{item['evidence_source']:<20} {item['frequency']:<12} "
            f"{item['days_overdue']}d"
        )

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def _format_upcoming_report(upcoming, days):
    """Format upcoming collections as a console report."""
    if not upcoming:
        return f"No evidence collections due in the next {days} days."

    lines = [
        "=" * 70,
        f"  cATO UPCOMING COLLECTIONS (next {days} days)",
        "=" * 70,
        f"  Total upcoming: {len(upcoming)}",
        "",
        f"  {'Control':<10} {'Type':<16} {'Source':<20} {'Frequency':<12} {'Days Until'}",
        f"  {'-' * 10} {'-' * 16} {'-' * 20} {'-' * 12} {'-' * 10}",
    ]

    for item in upcoming:
        lines.append(
            f"  {item['control_id']:<10} {item['evidence_type']:<16} "
            f"{item['evidence_source']:<20} {item['frequency']:<12} "
            f"{item['days_until_due']}d"
        )

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------

def main():
    """CLI entry point for cATO evidence collection scheduler."""
    parser = argparse.ArgumentParser(
        description="cATO evidence collection scheduler"
    )
    parser.add_argument(
        "--project-id", required=True,
        help="Project ID in ICDEV database"
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override database path"
    )
    parser.add_argument(
        "--project-dir", type=Path, default=None,
        help="Project directory for file-based evidence collection"
    )

    # Action flags (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run-due", action="store_true",
        help="Execute all collections that are currently due"
    )
    group.add_argument(
        "--upcoming", action="store_true",
        help="List collections due in the next N days"
    )
    group.add_argument(
        "--overdue", action="store_true",
        help="List overdue collections"
    )
    group.add_argument(
        "--calendar", action="store_true",
        help="Generate 12-week collection calendar"
    )
    group.add_argument(
        "--schedule", action="store_true",
        help="Show full collection schedule"
    )

    # Optional parameters
    parser.add_argument(
        "--days", type=int, default=30,
        help="Number of days to look ahead for --upcoming (default 30)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    try:
        if args.run_due:
            result = run_scheduled_collections(
                project_id=args.project_id,
                project_dir=args.project_dir,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print("\nCollection Summary:")
                print(f"  Due:       {result['total_due']}")
                print(f"  Collected: {result['collected']}")
                print(f"  Failed:    {result['failed']}")

        elif args.upcoming:
            result = get_upcoming_collections(
                project_id=args.project_id,
                days=args.days,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(_format_upcoming_report(result, args.days))

        elif args.overdue:
            result = get_overdue_collections(
                project_id=args.project_id,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(_format_overdue_report(result))

        elif args.calendar:
            result = generate_collection_calendar(
                project_id=args.project_id,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(_format_calendar_report(result))

        elif args.schedule:
            result = schedule_collections(
                project_id=args.project_id,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                if not result:
                    print("No evidence records found for scheduling.")
                else:
                    print(f"{'Control':<10} {'Type':<16} {'Source':<20} "
                          f"{'Freq':<12} {'Next Due':<22} {'Status'}")
                    print("-" * 90)
                    for item in result:
                        next_due = item["next_due"] or "N/A (not scheduled)"
                        overdue_flag = " [OVERDUE]" if item["is_overdue"] else ""
                        print(
                            f"{item['control_id']:<10} "
                            f"{item['evidence_type']:<16} "
                            f"{item['evidence_source']:<20} "
                            f"{item['frequency']:<12} "
                            f"{next_due:<22} "
                            f"{item['current_status']}{overdue_flag}"
                        )

    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
