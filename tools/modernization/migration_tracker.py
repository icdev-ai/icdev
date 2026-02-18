# CUI // SP-CTI
#!/usr/bin/env python3
"""SAFe PI-Cadenced Migration Progress Tracker for ICDEV DoD Modernization.

Tracks migration progress across Program Increments (PIs) with velocity
metrics, burndown projections, compliance gate checks, and detailed PI
reporting.  Integrates with the ICDEV operational database to snapshot
migration state, compute task-completion velocity, project remaining work,
and enforce ATO compliance gates at PI boundaries.

All computation is deterministic — no LLM calls, no external network access.
Uses Python stdlib only.

Usage:
    # Create a PI snapshot
    python tools/modernization/migration_tracker.py \\
        --plan-id MP-001 --snapshot --pi PI-25.3 --type pi_start

    # View velocity metrics
    python tools/modernization/migration_tracker.py --plan-id MP-001 --velocity

    # View burndown projection
    python tools/modernization/migration_tracker.py --plan-id MP-001 --burndown

    # Compare two PIs
    python tools/modernization/migration_tracker.py \\
        --plan-id MP-001 --compare --from-pi PI-25.2 --to-pi PI-25.3

    # Assign tasks to a PI
    python tools/modernization/migration_tracker.py \\
        --plan-id MP-001 --assign --pi PI-25.3 --tasks T-001,T-002,T-003

    # Generate PI migration report
    python tools/modernization/migration_tracker.py \\
        --plan-id MP-001 --pi-report --pi PI-25.3 --output-dir /tmp/reports

    # Check compliance gate
    python tools/modernization/migration_tracker.py \\
        --plan-id MP-001 --gate --pi PI-25.3

    # Update a task
    python tools/modernization/migration_tracker.py \\
        --plan-id MP-001 --update-task --task-id T-001 --status completed --hours 8

    # Show dashboard
    python tools/modernization/migration_tracker.py --plan-id MP-001 --dashboard

Classification: CUI // SP-CTI
Environment:    AWS GovCloud (us-gov-west-1)
Compliance:     NIST 800-53 Rev 5 / RMF
"""

import argparse
import json
import math
import os
import sqlite3
import sys
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_SNAPSHOT_TYPES = ("pi_start", "pi_end", "milestone", "manual")
VALID_TASK_STATUSES = ("pending", "in_progress", "completed", "blocked", "skipped")
COMPLIANCE_THRESHOLD = 0.95


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------

def _get_db(db_path=None):
    """Return a sqlite3 connection with Row factory enabled.

    Args:
        db_path: Optional path override for the database file.

    Returns:
        sqlite3.Connection with row_factory = sqlite3.Row.
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now_iso():
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _log_audit(conn, event_type, actor, action, project_id=None, details=None):
    """Write an append-only audit trail entry via the provided connection.

    This function writes directly to the audit_trail table to avoid
    circular imports with the audit_logger module.

    Args:
        conn:       Active sqlite3 connection.
        event_type: Audit event type string.
        actor:      Identity of the acting agent/user.
        action:     Human-readable description of the action.
        project_id: Optional project identifier.
        details:    Optional dict of additional detail data.
    """
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                event_type,
                actor,
                action,
                json.dumps(details) if details else None,
                "CUI",
            ),
        )
    except sqlite3.OperationalError:
        # Audit table may not exist in test environments — silently skip
        pass


# ============================================================================
# 1. create_pi_migration_snapshot
# ============================================================================

def create_pi_migration_snapshot(plan_id, pi_number, snapshot_type="manual",
                                  notes=None, db_path=None):
    """Snapshot the current migration state for a given plan and PI.

    Queries migration_tasks for the plan, counts statuses, counts migrated
    components/APIs/tables, computes test coverage and compliance score,
    then INSERTs a row into migration_progress.

    Args:
        plan_id:       Migration plan identifier.
        pi_number:     PI label (e.g. 'PI-25.3').
        snapshot_type: One of 'pi_start', 'pi_end', 'milestone', 'manual'.
        notes:         Optional free-text notes for the snapshot.
        db_path:       Optional database path override.

    Returns:
        Dict containing all snapshot fields.
    """
    if snapshot_type not in VALID_SNAPSHOT_TYPES:
        raise ValueError(
            f"Invalid snapshot_type '{snapshot_type}'. "
            f"Valid: {VALID_SNAPSHOT_TYPES}"
        )

    conn = _get_db(db_path)
    try:
        # -- Task status counts --
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM migration_tasks "
            "WHERE plan_id = ? GROUP BY status",
            (plan_id,),
        ).fetchall()

        status_counts = {s: 0 for s in VALID_TASK_STATUSES}
        for r in rows:
            status_counts[r["status"]] = r["cnt"]

        tasks_total = sum(status_counts.values())
        tasks_completed = status_counts.get("completed", 0)
        tasks_in_progress = status_counts.get("in_progress", 0)
        tasks_blocked = status_counts.get("blocked", 0)

        # -- Components migrated (extract_service tasks completed) --
        components_migrated = conn.execute(
            "SELECT COUNT(*) as cnt FROM migration_tasks "
            "WHERE plan_id = ? AND task_type = 'extract_service' "
            "AND status = 'completed'",
            (plan_id,),
        ).fetchone()["cnt"]

        # -- Get total components from legacy_components for plan's app --
        plan_row = conn.execute(
            "SELECT legacy_app_id FROM migration_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        legacy_app_id = plan_row["legacy_app_id"] if plan_row else None

        components_remaining = 0
        if legacy_app_id:
            total_comps = conn.execute(
                "SELECT COUNT(*) as cnt FROM legacy_components "
                "WHERE legacy_app_id = ?",
                (legacy_app_id,),
            ).fetchone()["cnt"]
            components_remaining = max(0, total_comps - components_migrated)

        # -- APIs migrated (create_api tasks completed) --
        apis_migrated = conn.execute(
            "SELECT COUNT(*) as cnt FROM migration_tasks "
            "WHERE plan_id = ? AND task_type = 'create_api' "
            "AND status = 'completed'",
            (plan_id,),
        ).fetchone()["cnt"]

        # -- Tables migrated (migrate_schema tasks completed) --
        tables_migrated = conn.execute(
            "SELECT COUNT(*) as cnt FROM migration_tasks "
            "WHERE plan_id = ? AND task_type = 'migrate_schema' "
            "AND status = 'completed'",
            (plan_id,),
        ).fetchone()["cnt"]

        # -- Hours spent (sum of actual_hours from completed tasks) --
        hours_row = conn.execute(
            "SELECT COALESCE(SUM(actual_hours), 0) as total "
            "FROM migration_tasks WHERE plan_id = ? AND status = 'completed'",
            (plan_id,),
        ).fetchone()
        hours_spent = hours_row["total"]

        # -- Test coverage estimate --
        total_test_tasks = conn.execute(
            "SELECT COUNT(*) as cnt FROM migration_tasks "
            "WHERE plan_id = ? AND task_type IN ('write_tests', 'integration_test', 'e2e_test')",
            (plan_id,),
        ).fetchone()["cnt"]

        completed_test_tasks = conn.execute(
            "SELECT COUNT(*) as cnt FROM migration_tasks "
            "WHERE plan_id = ? AND task_type IN ('write_tests', 'integration_test', 'e2e_test') "
            "AND status = 'completed'",
            (plan_id,),
        ).fetchone()["cnt"]

        test_coverage = 0.0
        if total_test_tasks > 0:
            test_coverage = round(completed_test_tasks / total_test_tasks, 4)

        # -- Compliance score: latest snapshot or compute from compliance tasks --
        compliance_score = 0.0
        latest_compliance = conn.execute(
            "SELECT compliance_score FROM migration_progress "
            "WHERE plan_id = ? AND compliance_score > 0 "
            "ORDER BY created_at DESC LIMIT 1",
            (plan_id,),
        ).fetchone()
        if latest_compliance:
            compliance_score = latest_compliance["compliance_score"]
        else:
            # Estimate: ratio of compliance-type tasks completed
            total_comp_tasks = conn.execute(
                "SELECT COUNT(*) as cnt FROM migration_tasks "
                "WHERE plan_id = ? AND task_type IN "
                "('compliance_check', 'stig_remediation', 'ssp_update', 'cui_marking')",
                (plan_id,),
            ).fetchone()["cnt"]
            completed_comp_tasks = conn.execute(
                "SELECT COUNT(*) as cnt FROM migration_tasks "
                "WHERE plan_id = ? AND task_type IN "
                "('compliance_check', 'stig_remediation', 'ssp_update', 'cui_marking') "
                "AND status = 'completed'",
                (plan_id,),
            ).fetchone()["cnt"]
            if total_comp_tasks > 0:
                compliance_score = round(completed_comp_tasks / total_comp_tasks, 4)

        # -- INSERT into migration_progress --
        now = _now_iso()
        conn.execute(
            """INSERT INTO migration_progress
               (plan_id, pi_number, snapshot_type,
                tasks_total, tasks_completed, tasks_in_progress, tasks_blocked,
                components_migrated, components_remaining,
                apis_migrated, tables_migrated,
                test_coverage, compliance_score,
                hours_spent, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan_id, pi_number, snapshot_type,
                tasks_total, tasks_completed, tasks_in_progress, tasks_blocked,
                components_migrated, components_remaining,
                apis_migrated, tables_migrated,
                test_coverage, compliance_score,
                hours_spent, notes, now,
            ),
        )

        _log_audit(
            conn,
            event_type="compliance_check",
            actor="migration-tracker",
            action=f"Created {snapshot_type} snapshot for {plan_id} at {pi_number}",
            project_id=plan_id,
            details={
                "pi_number": pi_number,
                "snapshot_type": snapshot_type,
                "tasks_total": tasks_total,
                "tasks_completed": tasks_completed,
                "components_migrated": components_migrated,
            },
        )

        conn.commit()

        snapshot = {
            "plan_id": plan_id,
            "pi_number": pi_number,
            "snapshot_type": snapshot_type,
            "tasks_total": tasks_total,
            "tasks_completed": tasks_completed,
            "tasks_in_progress": tasks_in_progress,
            "tasks_blocked": tasks_blocked,
            "components_migrated": components_migrated,
            "components_remaining": components_remaining,
            "apis_migrated": apis_migrated,
            "tables_migrated": tables_migrated,
            "test_coverage": test_coverage,
            "compliance_score": compliance_score,
            "hours_spent": hours_spent,
            "notes": notes,
            "created_at": now,
        }
        return snapshot

    finally:
        conn.close()


# ============================================================================
# 2. get_migration_velocity
# ============================================================================

def get_migration_velocity(plan_id, db_path=None):
    """Compute tasks/components completed per PI and velocity trends.

    Queries all pi_end snapshots for the plan, computes deltas between
    consecutive PIs, and calculates averages and trend direction.

    Args:
        plan_id: Migration plan identifier.
        db_path: Optional database path override.

    Returns:
        Dict with keys: snapshots (list of per-PI deltas), averages, trend.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM migration_progress "
            "WHERE plan_id = ? AND snapshot_type = 'pi_end' "
            "ORDER BY pi_number ASC",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()

    snapshots = [dict(r) for r in rows]

    if len(snapshots) < 1:
        return {
            "snapshots": [],
            "averages": {"tasks": 0, "components": 0, "hours": 0},
            "trend": "insufficient_data",
        }

    # Compute deltas between consecutive snapshots
    deltas = []
    for i in range(len(snapshots)):
        if i == 0:
            # First PI: delta is from zero baseline
            delta = {
                "pi": snapshots[i]["pi_number"],
                "tasks_completed_delta": snapshots[i]["tasks_completed"],
                "components_delta": snapshots[i]["components_migrated"],
                "hours_delta": snapshots[i]["hours_spent"],
            }
        else:
            prev = snapshots[i - 1]
            curr = snapshots[i]
            delta = {
                "pi": curr["pi_number"],
                "tasks_completed_delta": curr["tasks_completed"] - prev["tasks_completed"],
                "components_delta": curr["components_migrated"] - prev["components_migrated"],
                "hours_delta": curr["hours_spent"] - prev["hours_spent"],
            }
        deltas.append(delta)

    # Compute averages
    num_pis = len(deltas)
    avg_tasks = sum(d["tasks_completed_delta"] for d in deltas) / num_pis
    avg_components = sum(d["components_delta"] for d in deltas) / num_pis
    avg_hours = sum(d["hours_delta"] for d in deltas) / num_pis

    # Determine trend from last 3 PIs (or fewer if not enough data)
    trend = "stable"
    recent = deltas[-3:] if len(deltas) >= 3 else deltas
    if len(recent) >= 2:
        recent_tasks = [d["tasks_completed_delta"] for d in recent]
        # Check if trending up or down
        increasing = all(
            recent_tasks[j] >= recent_tasks[j - 1]
            for j in range(1, len(recent_tasks))
        )
        decreasing = all(
            recent_tasks[j] <= recent_tasks[j - 1]
            for j in range(1, len(recent_tasks))
        )
        if increasing and recent_tasks[-1] > recent_tasks[0]:
            trend = "improving"
        elif decreasing and recent_tasks[-1] < recent_tasks[0]:
            trend = "declining"
        else:
            trend = "stable"

    return {
        "snapshots": deltas,
        "averages": {
            "tasks": round(avg_tasks, 2),
            "components": round(avg_components, 2),
            "hours": round(avg_hours, 2),
        },
        "trend": trend,
    }


# ============================================================================
# 3. get_migration_burndown
# ============================================================================

def get_migration_burndown(plan_id, db_path=None):
    """Project remaining work vs velocity to estimate completion PI.

    Uses current task counts and average velocity to project how many
    PIs remain and whether the plan is on track.

    Args:
        plan_id: Migration plan identifier.
        db_path: Optional database path override.

    Returns:
        Dict with burndown data, projected completion, and on_track flag.
    """
    conn = _get_db(db_path)
    try:
        # Current state from migration_plans
        plan_row = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if not plan_row:
            return {"error": f"Plan {plan_id} not found"}

        total_tasks = plan_row["total_tasks"] or 0
        completed_tasks = plan_row["completed_tasks"] or 0
        remaining_tasks = max(0, total_tasks - completed_tasks)

        # Get all pi_end snapshots for burndown data points
        snapshots = conn.execute(
            "SELECT pi_number, tasks_completed, tasks_total "
            "FROM migration_progress "
            "WHERE plan_id = ? AND snapshot_type = 'pi_end' "
            "ORDER BY pi_number ASC",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()

    # Get velocity
    velocity = get_migration_velocity(plan_id, db_path=db_path)
    avg_tasks_per_pi = velocity["averages"]["tasks"]

    # Project remaining PIs
    remaining_pis = None
    projected_completion_pi = None
    if avg_tasks_per_pi > 0:
        remaining_pis = math.ceil(remaining_tasks / avg_tasks_per_pi)
        # Estimate projected PI label based on last snapshot
        if snapshots:
            last_pi = dict(snapshots[-1])["pi_number"]
            projected_completion_pi = _project_pi_label(last_pi, remaining_pis)
        else:
            projected_completion_pi = f"+{remaining_pis} PIs"

    # Build burndown data points
    burndown_data = []
    ideal_per_pi = total_tasks / max(len(snapshots) + (remaining_pis or 1), 1)
    for idx, snap in enumerate(snapshots):
        snap_dict = dict(snap)
        tasks_remaining = total_tasks - snap_dict["tasks_completed"]
        ideal_remaining = max(0, total_tasks - ideal_per_pi * (idx + 1))
        burndown_data.append({
            "pi_number": snap_dict["pi_number"],
            "tasks_remaining": tasks_remaining,
            "ideal_remaining": round(ideal_remaining, 1),
        })

    # On-track: remaining work is at or below ideal line
    on_track = True
    if burndown_data:
        last = burndown_data[-1]
        on_track = last["tasks_remaining"] <= last["ideal_remaining"] + 1

    return {
        "plan_id": plan_id,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "remaining_tasks": remaining_tasks,
        "avg_tasks_per_pi": avg_tasks_per_pi,
        "remaining_pis": remaining_pis,
        "projected_completion_pi": projected_completion_pi,
        "on_track": on_track,
        "burndown_data": burndown_data,
        "velocity_trend": velocity["trend"],
    }


def _project_pi_label(current_pi, additional_pis):
    """Estimate a future PI label by incrementing the current one.

    Supports labels like 'PI-25.3' (year.increment format).  Wraps at
    increment 6 to the next year (SAFe typically has 4-6 PIs/year).

    Args:
        current_pi:    Current PI label string (e.g. 'PI-25.3').
        additional_pis: Number of PIs to add.

    Returns:
        Projected PI label string.
    """
    try:
        prefix, version = current_pi.rsplit("-", 1)
        parts = version.split(".")
        year = int(parts[0])
        increment = int(parts[1])

        for _ in range(additional_pis):
            increment += 1
            if increment > 6:
                increment = 1
                year += 1

        return f"{prefix}-{year}.{increment}"
    except (ValueError, IndexError):
        return f"{current_pi}+{additional_pis}"


# ============================================================================
# 4. compare_pi_snapshots
# ============================================================================

def compare_pi_snapshots(plan_id, from_pi, to_pi, db_path=None):
    """Compute deltas between two PI snapshots for the same plan.

    Queries the latest pi_end (or manual) snapshot for each PI and
    computes the difference for all tracked metrics.

    Args:
        plan_id: Migration plan identifier.
        from_pi: Starting PI label (e.g. 'PI-25.2').
        to_pi:   Ending PI label (e.g. 'PI-25.3').
        db_path: Optional database path override.

    Returns:
        Dict with from_snapshot, to_snapshot, and deltas.
    """
    conn = _get_db(db_path)
    try:
        from_row = conn.execute(
            "SELECT * FROM migration_progress "
            "WHERE plan_id = ? AND pi_number = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (plan_id, from_pi),
        ).fetchone()

        to_row = conn.execute(
            "SELECT * FROM migration_progress "
            "WHERE plan_id = ? AND pi_number = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (plan_id, to_pi),
        ).fetchone()
    finally:
        conn.close()

    if not from_row:
        return {"error": f"No snapshot found for {from_pi}"}
    if not to_row:
        return {"error": f"No snapshot found for {to_pi}"}

    from_dict = dict(from_row)
    to_dict = dict(to_row)

    # Numeric fields to compare
    compare_fields = [
        "tasks_total", "tasks_completed", "tasks_in_progress", "tasks_blocked",
        "components_migrated", "components_remaining",
        "apis_migrated", "tables_migrated",
        "test_coverage", "compliance_score", "hours_spent",
    ]

    deltas = {}
    for field in compare_fields:
        from_val = from_dict.get(field, 0) or 0
        to_val = to_dict.get(field, 0) or 0
        deltas[field] = round(to_val - from_val, 4)

    # Highlight important changes
    highlights = []
    if deltas["tasks_completed"] > 0:
        highlights.append(f"{deltas['tasks_completed']} tasks completed")
    if deltas["tasks_blocked"] > 0:
        highlights.append(f"{deltas['tasks_blocked']} new blockers")
    if deltas["tasks_blocked"] < 0:
        highlights.append(f"{abs(deltas['tasks_blocked'])} blockers resolved")
    if deltas["compliance_score"] != 0:
        direction = "improved" if deltas["compliance_score"] > 0 else "decreased"
        highlights.append(
            f"Compliance score {direction} by "
            f"{abs(deltas['compliance_score']):.4f}"
        )
    if deltas["hours_spent"] > 0:
        highlights.append(f"{deltas['hours_spent']:.1f} hours spent")

    return {
        "plan_id": plan_id,
        "from_pi": from_pi,
        "to_pi": to_pi,
        "from_snapshot": from_dict,
        "to_snapshot": to_dict,
        "deltas": deltas,
        "highlights": highlights,
    }


# ============================================================================
# 5. assign_tasks_to_pi
# ============================================================================

def assign_tasks_to_pi(plan_id, pi_number, task_ids, db_path=None):
    """Assign one or more migration tasks to a specific PI.

    Updates the pi_number column on matching migration_tasks rows.

    Args:
        plan_id:   Migration plan identifier.
        pi_number: PI label to assign (e.g. 'PI-25.3').
        task_ids:  List of task identifier strings.
        db_path:   Optional database path override.

    Returns:
        Count of tasks successfully assigned.
    """
    if not task_ids:
        return 0

    conn = _get_db(db_path)
    try:
        assigned = 0
        for tid in task_ids:
            cursor = conn.execute(
                "UPDATE migration_tasks SET pi_number = ? "
                "WHERE id = ? AND plan_id = ?",
                (pi_number, tid, plan_id),
            )
            assigned += cursor.rowcount

        _log_audit(
            conn,
            event_type="project_updated",
            actor="migration-tracker",
            action=f"Assigned {assigned} tasks to {pi_number} in plan {plan_id}",
            project_id=plan_id,
            details={"pi_number": pi_number, "task_ids": task_ids},
        )

        conn.commit()
        return assigned
    finally:
        conn.close()


# ============================================================================
# 6. get_pi_tasks
# ============================================================================

def get_pi_tasks(plan_id, pi_number, db_path=None):
    """Retrieve all migration tasks assigned to a specific PI.

    Args:
        plan_id:   Migration plan identifier.
        pi_number: PI label to query.
        db_path:   Optional database path override.

    Returns:
        Dict with pi_number, tasks list, and by_status breakdown.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, task_type, priority, status, "
            "estimated_hours, actual_hours, assigned_to "
            "FROM migration_tasks "
            "WHERE plan_id = ? AND pi_number = ? "
            "ORDER BY priority ASC, title ASC",
            (plan_id, pi_number),
        ).fetchall()
    finally:
        conn.close()

    tasks = []
    by_status = {s: 0 for s in VALID_TASK_STATUSES}

    for r in rows:
        task = {
            "id": r["id"],
            "title": r["title"],
            "type": r["task_type"],
            "priority": r["priority"],
            "status": r["status"],
            "estimated_hours": r["estimated_hours"],
            "actual_hours": r["actual_hours"],
            "assigned_to": r["assigned_to"],
        }
        tasks.append(task)
        if r["status"] in by_status:
            by_status[r["status"]] += 1

    return {
        "pi_number": pi_number,
        "plan_id": plan_id,
        "task_count": len(tasks),
        "tasks": tasks,
        "by_status": by_status,
    }


# ============================================================================
# 7. generate_pi_migration_report
# ============================================================================

def generate_pi_migration_report(plan_id, pi_number, output_dir=None,
                                  db_path=None):
    """Generate a CUI-marked markdown PI migration report.

    Combines task data, velocity, burndown, snapshot comparison, and
    compliance gate status into a structured report.

    Args:
        plan_id:    Migration plan identifier.
        pi_number:  PI label for the report.
        output_dir: Optional directory to write the report file.
        db_path:    Optional database path override.

    Returns:
        Report content string, or file path if output_dir is provided.
    """
    # Gather data
    pi_data = get_pi_tasks(plan_id, pi_number, db_path=db_path)
    velocity = get_migration_velocity(plan_id, db_path=db_path)
    burndown = get_migration_burndown(plan_id, db_path=db_path)
    gate = check_pi_compliance_gate(plan_id, pi_number, db_path=db_path)

    # Plan metadata
    conn = _get_db(db_path)
    try:
        plan_row = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
    finally:
        conn.close()

    plan_name = plan_row["plan_name"] if plan_row else plan_id
    strategy = plan_row["strategy"] if plan_row else "N/A"

    # Determine previous PI for comparison
    prev_pi = _previous_pi_label(pi_number)
    comparison = compare_pi_snapshots(plan_id, prev_pi, pi_number, db_path=db_path)

    # Build report
    lines = []
    lines.append("CUI // SP-CTI")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"# PI Migration Report: {pi_number}")
    lines.append("")
    lines.append(f"**Plan:** {plan_name}  ")
    lines.append(f"**Plan ID:** {plan_id}  ")
    lines.append(f"**Strategy:** {strategy}  ")
    lines.append(f"**Generated:** {_now_iso()}  ")
    lines.append("")

    # --- Task Completion ---
    lines.append("## Task Completion")
    lines.append("")
    by_status = pi_data["by_status"]
    total_pi = pi_data["task_count"]
    completed = by_status.get("completed", 0)
    in_prog = by_status.get("in_progress", 0)
    blocked = by_status.get("blocked", 0)
    pending = by_status.get("pending", 0)
    skipped = by_status.get("skipped", 0)
    pct = round(completed / total_pi * 100, 1) if total_pi > 0 else 0.0

    lines.append(f"| Status      | Count | Percentage |")
    lines.append(f"|-------------|-------|------------|")
    lines.append(f"| Completed   | {completed:>5} | {pct:>9.1f}% |")
    if total_pi > 0:
        lines.append(f"| In Progress | {in_prog:>5} | {in_prog/total_pi*100:>9.1f}% |")
        lines.append(f"| Blocked     | {blocked:>5} | {blocked/total_pi*100:>9.1f}% |")
        lines.append(f"| Pending     | {pending:>5} | {pending/total_pi*100:>9.1f}% |")
        lines.append(f"| Skipped     | {skipped:>5} | {skipped/total_pi*100:>9.1f}% |")
    lines.append(f"| **Total**   | **{total_pi}** |            |")
    lines.append("")

    # --- Velocity ---
    lines.append("## Velocity")
    lines.append("")
    lines.append(f"**Trend:** {velocity['trend']}  ")
    lines.append(f"**Avg Tasks/PI:** {velocity['averages']['tasks']}  ")
    lines.append(f"**Avg Components/PI:** {velocity['averages']['components']}  ")
    lines.append(f"**Avg Hours/PI:** {velocity['averages']['hours']}  ")
    lines.append("")

    # ASCII bar chart of tasks per PI
    if velocity["snapshots"]:
        lines.append("```")
        max_tasks = max(
            (d["tasks_completed_delta"] for d in velocity["snapshots"]),
            default=1,
        )
        max_tasks = max(max_tasks, 1)
        bar_width = 40
        for d in velocity["snapshots"]:
            bar_len = int(d["tasks_completed_delta"] / max_tasks * bar_width)
            bar = "#" * bar_len
            lines.append(
                f"  {d['pi']:<10} | {bar:<{bar_width}} | "
                f"{d['tasks_completed_delta']} tasks"
            )
        lines.append("```")
        lines.append("")

    # --- Burndown ---
    lines.append("## Burndown Projection")
    lines.append("")
    lines.append(f"**Total Tasks:** {burndown.get('total_tasks', 'N/A')}  ")
    lines.append(f"**Completed:** {burndown.get('completed_tasks', 'N/A')}  ")
    lines.append(f"**Remaining:** {burndown.get('remaining_tasks', 'N/A')}  ")
    lines.append(f"**Avg Velocity:** {burndown.get('avg_tasks_per_pi', 'N/A')} tasks/PI  ")
    remaining_pis = burndown.get("remaining_pis")
    lines.append(
        f"**Projected Remaining PIs:** "
        f"{remaining_pis if remaining_pis is not None else 'N/A'}  "
    )
    lines.append(
        f"**Projected Completion:** "
        f"{burndown.get('projected_completion_pi', 'N/A')}  "
    )
    lines.append(f"**On Track:** {'YES' if burndown.get('on_track') else 'NO'}  ")
    lines.append("")

    # --- Hours ---
    lines.append("## Hours: Estimated vs Actual")
    lines.append("")
    est_total = sum(
        (t.get("estimated_hours") or 0) for t in pi_data["tasks"]
    )
    act_total = sum(
        (t.get("actual_hours") or 0) for t in pi_data["tasks"]
    )
    variance = act_total - est_total
    lines.append(f"| Metric    | Hours |")
    lines.append(f"|-----------|-------|")
    lines.append(f"| Estimated | {est_total:>5.1f} |")
    lines.append(f"| Actual    | {act_total:>5.1f} |")
    lines.append(f"| Variance  | {variance:>+5.1f} |")
    lines.append("")

    # --- Compliance Gate ---
    lines.append("## Compliance Gate")
    lines.append("")
    gate_status = "PASS" if gate.get("passed") else "FAIL"
    lines.append(f"**Status:** {gate_status}  ")
    lines.append(
        f"**Score:** {gate.get('score', 0):.4f} "
        f"(threshold: {gate.get('threshold', COMPLIANCE_THRESHOLD)})  "
    )
    if gate.get("issues"):
        lines.append("")
        lines.append("**Issues:**")
        for issue in gate["issues"]:
            lines.append(f"- {issue}")
    lines.append("")

    # --- Blockers & Risks ---
    lines.append("## Blockers & Risks")
    lines.append("")
    blocked_tasks = [t for t in pi_data["tasks"] if t["status"] == "blocked"]
    if blocked_tasks:
        for bt in blocked_tasks:
            lines.append(f"- **{bt['id']}** {bt['title']} (priority: {bt['priority']})")
    else:
        lines.append("No blocked tasks in this PI.")
    lines.append("")

    # --- Next PI Plan ---
    lines.append("## Next PI Plan")
    lines.append("")
    next_pi = _next_pi_label(pi_number)
    next_data = get_pi_tasks(plan_id, next_pi, db_path=db_path)
    if next_data["tasks"]:
        high_pri = [
            t for t in next_data["tasks"]
            if t["priority"] in (1, 2, "1", "2", "critical", "high")
        ]
        if high_pri:
            lines.append(f"High-priority tasks for {next_pi}:")
            for t in high_pri[:10]:
                lines.append(f"- [{t['id']}] {t['title']} (priority: {t['priority']})")
        else:
            lines.append(
                f"{next_data['task_count']} tasks assigned to {next_pi}."
            )
    else:
        lines.append(f"No tasks yet assigned to {next_pi}.")
    lines.append("")

    # --- Comparison vs previous PI ---
    if "error" not in comparison:
        lines.append(f"## Comparison: {prev_pi} -> {pi_number}")
        lines.append("")
        if comparison.get("highlights"):
            for h in comparison["highlights"]:
                lines.append(f"- {h}")
        lines.append("")

    lines.append("=" * 80)
    lines.append("CUI // SP-CTI")

    report_content = "\n".join(lines)

    # Write to file if output_dir provided
    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        filename = f"pi_migration_report_{plan_id}_{pi_number}.md"
        filepath = out_path / filename
        filepath.write_text(report_content, encoding="utf-8")
        return str(filepath)

    return report_content


def _previous_pi_label(pi_label):
    """Compute the previous PI label from the given one.

    Args:
        pi_label: PI label string (e.g. 'PI-25.3').

    Returns:
        Previous PI label string.
    """
    try:
        prefix, version = pi_label.rsplit("-", 1)
        parts = version.split(".")
        year = int(parts[0])
        increment = int(parts[1])
        increment -= 1
        if increment < 1:
            increment = 6
            year -= 1
        return f"{prefix}-{year}.{increment}"
    except (ValueError, IndexError):
        return pi_label


def _next_pi_label(pi_label):
    """Compute the next PI label from the given one.

    Args:
        pi_label: PI label string (e.g. 'PI-25.3').

    Returns:
        Next PI label string.
    """
    return _project_pi_label(pi_label, 1)


# ============================================================================
# 8. check_pi_compliance_gate
# ============================================================================

def check_pi_compliance_gate(plan_id, pi_number, db_path=None):
    """Verify that ATO compliance is maintained for the given PI.

    Checks:
        1. Compliance score >= 0.95
        2. No new critical blockers
        3. All compliance-affecting PI tasks are completed

    Args:
        plan_id:   Migration plan identifier.
        pi_number: PI label to check.
        db_path:   Optional database path override.

    Returns:
        Dict with passed (bool), score, threshold, and issues list.
    """
    conn = _get_db(db_path)
    try:
        # Get latest snapshot compliance score
        snap_row = conn.execute(
            "SELECT compliance_score FROM migration_progress "
            "WHERE plan_id = ? AND pi_number = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (plan_id, pi_number),
        ).fetchone()

        score = snap_row["compliance_score"] if snap_row else 0.0

        # Check for critical blocked tasks
        blocked_critical = conn.execute(
            "SELECT COUNT(*) as cnt FROM migration_tasks "
            "WHERE plan_id = ? AND pi_number = ? "
            "AND status = 'blocked' AND priority IN (1, 'critical')",
            (plan_id, pi_number),
        ).fetchone()["cnt"]

        # Check compliance-affecting tasks
        compliance_task_types = (
            "compliance_check", "stig_remediation", "ssp_update", "cui_marking",
        )
        placeholders = ",".join("?" * len(compliance_task_types))
        incomplete_compliance = conn.execute(
            f"SELECT COUNT(*) as cnt FROM migration_tasks "
            f"WHERE plan_id = ? AND pi_number = ? "
            f"AND task_type IN ({placeholders}) "
            f"AND status NOT IN ('completed', 'skipped')",
            (plan_id, pi_number) + compliance_task_types,
        ).fetchone()["cnt"]

    finally:
        conn.close()

    issues = []
    passed = True

    if score < COMPLIANCE_THRESHOLD:
        passed = False
        issues.append(
            f"Compliance score {score:.4f} is below threshold "
            f"{COMPLIANCE_THRESHOLD}"
        )

    if blocked_critical > 0:
        passed = False
        issues.append(
            f"{blocked_critical} critical blocker(s) remain unresolved"
        )

    if incomplete_compliance > 0:
        passed = False
        issues.append(
            f"{incomplete_compliance} compliance task(s) not yet completed"
        )

    return {
        "plan_id": plan_id,
        "pi_number": pi_number,
        "passed": passed,
        "score": score,
        "threshold": COMPLIANCE_THRESHOLD,
        "issues": issues,
    }


# ============================================================================
# 9. update_task_status
# ============================================================================

def update_task_status(task_id, status, actual_hours=None, db_path=None):
    """Update the status (and optionally actual_hours) of a migration task.

    If status is 'completed', also sets completed_at and increments the
    parent plan's completed_tasks counter.

    Args:
        task_id:      Migration task identifier.
        status:       New status string.
        actual_hours: Optional hours spent (float).
        db_path:      Optional database path override.

    Returns:
        Dict with the updated task fields.
    """
    if status not in VALID_TASK_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Valid: {VALID_TASK_STATUSES}"
        )

    conn = _get_db(db_path)
    try:
        # Fetch current task
        task_row = conn.execute(
            "SELECT * FROM migration_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not task_row:
            return {"error": f"Task {task_id} not found"}

        old_status = task_row["status"]
        plan_id = task_row["plan_id"]
        completed_at = None

        if status == "completed":
            completed_at = _now_iso()

        # Build update
        if actual_hours is not None and completed_at:
            conn.execute(
                "UPDATE migration_tasks "
                "SET status = ?, actual_hours = ?, completed_at = ? "
                "WHERE id = ?",
                (status, actual_hours, completed_at, task_id),
            )
        elif actual_hours is not None:
            conn.execute(
                "UPDATE migration_tasks SET status = ?, actual_hours = ? "
                "WHERE id = ?",
                (status, actual_hours, task_id),
            )
        elif completed_at:
            conn.execute(
                "UPDATE migration_tasks SET status = ?, completed_at = ? "
                "WHERE id = ?",
                (status, completed_at, task_id),
            )
        else:
            conn.execute(
                "UPDATE migration_tasks SET status = ? WHERE id = ?",
                (status, task_id),
            )

        # Update migration_plans completed_tasks count
        completed_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM migration_tasks "
            "WHERE plan_id = ? AND status = 'completed'",
            (plan_id,),
        ).fetchone()["cnt"]

        conn.execute(
            "UPDATE migration_plans SET completed_tasks = ?, updated_at = ? "
            "WHERE id = ?",
            (completed_count, _now_iso(), plan_id),
        )

        _log_audit(
            conn,
            event_type="project_updated",
            actor="migration-tracker",
            action=f"Task {task_id} status changed: {old_status} -> {status}",
            project_id=plan_id,
            details={
                "task_id": task_id,
                "old_status": old_status,
                "new_status": status,
                "actual_hours": actual_hours,
            },
        )

        conn.commit()

        # Re-fetch updated task
        updated = conn.execute(
            "SELECT * FROM migration_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

        return dict(updated) if updated else {"task_id": task_id, "status": status}

    finally:
        conn.close()


# ============================================================================
# 10. get_dashboard
# ============================================================================

def get_dashboard(plan_id, db_path=None):
    """Generate a summary dashboard for the migration plan.

    Combines plan overview, progress, current PI status, velocity,
    burndown, compliance, and blockers into a single view.

    Args:
        plan_id: Migration plan identifier.
        db_path: Optional database path override.

    Returns:
        Dict with all dashboard sections.
    """
    conn = _get_db(db_path)
    try:
        # Plan overview
        plan_row = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if not plan_row:
            return {"error": f"Plan {plan_id} not found"}

        plan = dict(plan_row)
        total_tasks = plan.get("total_tasks", 0) or 0
        completed_tasks = plan.get("completed_tasks", 0) or 0

        # Current PI: find the most recent pi_number in tasks
        current_pi_row = conn.execute(
            "SELECT pi_number, COUNT(*) as cnt FROM migration_tasks "
            "WHERE plan_id = ? AND pi_number IS NOT NULL "
            "GROUP BY pi_number ORDER BY pi_number DESC LIMIT 1",
            (plan_id,),
        ).fetchone()
        current_pi = current_pi_row["pi_number"] if current_pi_row else None

        # Current PI task breakdown
        current_pi_tasks = {"assigned": 0, "completed": 0}
        if current_pi:
            pi_stats = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM migration_tasks "
                "WHERE plan_id = ? AND pi_number = ? GROUP BY status",
                (plan_id, current_pi),
            ).fetchall()
            total_assigned = 0
            pi_completed = 0
            for r in pi_stats:
                total_assigned += r["cnt"]
                if r["status"] == "completed":
                    pi_completed = r["cnt"]
            current_pi_tasks = {
                "assigned": total_assigned,
                "completed": pi_completed,
            }

        # Blockers
        blockers = conn.execute(
            "SELECT id, title, priority FROM migration_tasks "
            "WHERE plan_id = ? AND status = 'blocked' "
            "ORDER BY priority ASC LIMIT 5",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()

    # Velocity and burndown
    velocity = get_migration_velocity(plan_id, db_path=db_path)
    burndown = get_migration_burndown(plan_id, db_path=db_path)

    # Compliance
    compliance_gate = None
    if current_pi:
        compliance_gate = check_pi_compliance_gate(
            plan_id, current_pi, db_path=db_path
        )

    # Progress percentage
    progress_pct = 0.0
    if total_tasks > 0:
        progress_pct = round(completed_tasks / total_tasks * 100, 1)

    # Progress bar (50-char wide)
    bar_width = 50
    filled = int(progress_pct / 100 * bar_width)
    progress_bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"

    return {
        "plan_id": plan_id,
        "plan_name": plan.get("plan_name", plan_id),
        "strategy": plan.get("strategy", "N/A"),
        "status": plan.get("status", "N/A"),
        "progress": {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "percentage": progress_pct,
            "bar": progress_bar,
            "estimated_hours": plan.get("estimated_hours", 0),
            "actual_hours": plan.get("actual_hours", 0),
        },
        "current_pi": {
            "pi_number": current_pi,
            "tasks_assigned": current_pi_tasks["assigned"],
            "tasks_completed": current_pi_tasks["completed"],
        },
        "velocity": {
            "last_3_pis": velocity["snapshots"][-3:]
                if velocity["snapshots"] else [],
            "trend": velocity["trend"],
            "averages": velocity["averages"],
        },
        "burndown": {
            "remaining_tasks": burndown.get("remaining_tasks", 0),
            "remaining_pis": burndown.get("remaining_pis"),
            "projected_completion": burndown.get("projected_completion_pi"),
            "on_track": burndown.get("on_track", False),
        },
        "compliance": {
            "score": compliance_gate["score"] if compliance_gate else 0.0,
            "gate_passed": compliance_gate["passed"]
                if compliance_gate else False,
            "issues": compliance_gate["issues"] if compliance_gate else [],
        },
        "blockers": {
            "count": len(blockers),
            "top_items": [
                {"id": b["id"], "title": b["title"], "priority": b["priority"]}
                for b in blockers
            ],
        },
    }


# ============================================================================
# CLI display helpers
# ============================================================================

def _print_dashboard(dashboard):
    """Pretty-print the dashboard to stdout with CUI markings.

    Args:
        dashboard: Dashboard dict from get_dashboard().
    """
    print("=" * 70)
    print("CUI // SP-CTI")
    print("=" * 70)
    print()
    print("MIGRATION DASHBOARD")
    print(f"  Plan:     {dashboard['plan_name']}")
    print(f"  ID:       {dashboard['plan_id']}")
    print(f"  Strategy: {dashboard['strategy']}")
    print(f"  Status:   {dashboard['status']}")
    print()

    prog = dashboard["progress"]
    print("PROGRESS")
    print(f"  {prog['bar']}  {prog['percentage']}%")
    print(f"  Tasks: {prog['completed_tasks']}/{prog['total_tasks']}")
    print(f"  Hours: {prog.get('actual_hours', 0)}/{prog.get('estimated_hours', 0)} (actual/estimated)")
    print()

    pi = dashboard["current_pi"]
    if pi["pi_number"]:
        print(f"CURRENT PI: {pi['pi_number']}")
        print(f"  Assigned:  {pi['tasks_assigned']}")
        print(f"  Completed: {pi['tasks_completed']}")
        print()

    vel = dashboard["velocity"]
    print("VELOCITY")
    print(f"  Trend: {vel['trend']}")
    print(f"  Avg Tasks/PI:      {vel['averages']['tasks']}")
    print(f"  Avg Components/PI: {vel['averages']['components']}")
    print(f"  Avg Hours/PI:      {vel['averages']['hours']}")
    if vel["last_3_pis"]:
        print("  Recent PIs:")
        for d in vel["last_3_pis"]:
            print(f"    {d['pi']}: {d['tasks_completed_delta']} tasks, "
                  f"{d['components_delta']} components, "
                  f"{d['hours_delta']} hours")
    print()

    bd = dashboard["burndown"]
    print("BURNDOWN")
    print(f"  Remaining Tasks: {bd['remaining_tasks']}")
    print(f"  Remaining PIs:   {bd['remaining_pis'] or 'N/A'}")
    print(f"  Projected Done:  {bd['projected_completion'] or 'N/A'}")
    print(f"  On Track:        {'YES' if bd['on_track'] else 'NO'}")
    print()

    comp = dashboard["compliance"]
    print("COMPLIANCE")
    print(f"  Score: {comp['score']:.4f}")
    print(f"  Gate:  {'PASS' if comp['gate_passed'] else 'FAIL'}")
    if comp["issues"]:
        for issue in comp["issues"]:
            print(f"    - {issue}")
    print()

    bl = dashboard["blockers"]
    print(f"BLOCKERS ({bl['count']})")
    if bl["top_items"]:
        for b in bl["top_items"]:
            print(f"  [{b['id']}] {b['title']} (priority: {b['priority']})")
    else:
        print("  None")
    print()

    print("=" * 70)
    print("CUI // SP-CTI")
    print("=" * 70)


# ============================================================================
# CLI entry point
# ============================================================================

def main():
    """CLI entry point for the PI-cadenced migration progress tracker.

    Parses arguments and dispatches to the appropriate function.
    """
    parser = argparse.ArgumentParser(
        description=(
            "CUI // SP-CTI -- SAFe PI-Cadenced Migration Progress Tracker. "
            "Track migration velocity, burndown, compliance gates, and "
            "generate PI reports for DoD modernization plans."
        ),
        epilog="CUI // SP-CTI",
    )

    parser.add_argument(
        "--plan-id", required=True,
        help="Migration plan identifier (e.g. MP-001)",
    )

    # Action flags (mutually exclusive)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--snapshot", action="store_true",
        help="Create a PI migration snapshot",
    )
    action.add_argument(
        "--velocity", action="store_true",
        help="Show velocity metrics across PIs",
    )
    action.add_argument(
        "--burndown", action="store_true",
        help="Show burndown projection",
    )
    action.add_argument(
        "--compare", action="store_true",
        help="Compare two PI snapshots (requires --from-pi and --to-pi)",
    )
    action.add_argument(
        "--assign", action="store_true",
        help="Assign tasks to a PI (requires --pi and --tasks)",
    )
    action.add_argument(
        "--pi-report", action="store_true",
        help="Generate a PI migration report (requires --pi)",
    )
    action.add_argument(
        "--gate", action="store_true",
        help="Check compliance gate for a PI (requires --pi)",
    )
    action.add_argument(
        "--update-task", action="store_true",
        help="Update a task status (requires --task-id and --status)",
    )
    action.add_argument(
        "--dashboard", action="store_true",
        help="Show migration dashboard summary",
    )

    # Supporting arguments
    parser.add_argument("--pi", help="PI label (e.g. PI-25.3)")
    parser.add_argument(
        "--type", dest="snapshot_type", default="manual",
        choices=VALID_SNAPSHOT_TYPES,
        help="Snapshot type (default: manual)",
    )
    parser.add_argument("--notes", help="Notes for the snapshot")
    parser.add_argument("--from-pi", help="Starting PI for comparison")
    parser.add_argument("--to-pi", help="Ending PI for comparison")
    parser.add_argument(
        "--tasks",
        help="Comma-separated list of task IDs (for --assign)",
    )
    parser.add_argument("--task-id", help="Task ID (for --update-task)")
    parser.add_argument(
        "--status",
        choices=VALID_TASK_STATUSES,
        help="Task status (for --update-task)",
    )
    parser.add_argument(
        "--hours", type=float,
        help="Actual hours spent (for --update-task)",
    )
    parser.add_argument("--output-dir", help="Output directory for reports")
    parser.add_argument(
        "--json", action="store_true", dest="output_json",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    try:
        # --- Snapshot ---
        if args.snapshot:
            if not args.pi:
                parser.error("--pi is required for --snapshot")
            result = create_pi_migration_snapshot(
                plan_id=args.plan_id,
                pi_number=args.pi,
                snapshot_type=args.snapshot_type,
                notes=args.notes,
            )
            if args.output_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)
                print(f"Snapshot created for {args.plan_id} at {args.pi}")
                print(f"  Type:               {result['snapshot_type']}")
                print(f"  Tasks Total:        {result['tasks_total']}")
                print(f"  Tasks Completed:    {result['tasks_completed']}")
                print(f"  Tasks In Progress:  {result['tasks_in_progress']}")
                print(f"  Tasks Blocked:      {result['tasks_blocked']}")
                print(f"  Components Migrated:{result['components_migrated']}")
                print(f"  APIs Migrated:      {result['apis_migrated']}")
                print(f"  Tables Migrated:    {result['tables_migrated']}")
                print(f"  Test Coverage:      {result['test_coverage']:.2%}")
                print(f"  Compliance Score:   {result['compliance_score']:.4f}")
                print(f"  Hours Spent:        {result['hours_spent']}")
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)

        # --- Velocity ---
        elif args.velocity:
            result = get_migration_velocity(args.plan_id)
            if args.output_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)
                print(f"Migration Velocity: {args.plan_id}")
                print(f"  Trend: {result['trend']}")
                print(f"  Avg Tasks/PI:      {result['averages']['tasks']}")
                print(f"  Avg Components/PI: {result['averages']['components']}")
                print(f"  Avg Hours/PI:      {result['averages']['hours']}")
                print()
                if result["snapshots"]:
                    print("  PI-by-PI Breakdown:")
                    for d in result["snapshots"]:
                        print(
                            f"    {d['pi']}: "
                            f"{d['tasks_completed_delta']} tasks, "
                            f"{d['components_delta']} components, "
                            f"{d['hours_delta']} hours"
                        )
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)

        # --- Burndown ---
        elif args.burndown:
            result = get_migration_burndown(args.plan_id)
            if args.output_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)
                print(f"Migration Burndown: {args.plan_id}")
                print(f"  Total Tasks:     {result.get('total_tasks', 'N/A')}")
                print(f"  Completed:       {result.get('completed_tasks', 'N/A')}")
                print(f"  Remaining:       {result.get('remaining_tasks', 'N/A')}")
                print(f"  Velocity:        {result.get('avg_tasks_per_pi', 'N/A')} tasks/PI")
                print(f"  Remaining PIs:   {result.get('remaining_pis', 'N/A')}")
                print(f"  Projected Done:  {result.get('projected_completion_pi', 'N/A')}")
                print(f"  On Track:        {'YES' if result.get('on_track') else 'NO'}")
                print()
                if result.get("burndown_data"):
                    print("  Burndown Data:")
                    for bd in result["burndown_data"]:
                        print(
                            f"    {bd['pi_number']}: "
                            f"{bd['tasks_remaining']} remaining "
                            f"(ideal: {bd['ideal_remaining']})"
                        )
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)

        # --- Compare ---
        elif args.compare:
            if not args.from_pi or not args.to_pi:
                parser.error("--from-pi and --to-pi are required for --compare")
            result = compare_pi_snapshots(
                args.plan_id, args.from_pi, args.to_pi,
            )
            if args.output_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                if "error" in result:
                    print(f"ERROR: {result['error']}")
                    sys.exit(1)
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)
                print(f"PI Comparison: {args.from_pi} -> {args.to_pi}")
                print(f"  Plan: {args.plan_id}")
                print()
                print("  Highlights:")
                for h in result.get("highlights", []):
                    print(f"    - {h}")
                print()
                print("  Deltas:")
                for key, val in result.get("deltas", {}).items():
                    print(f"    {key:<25} {val:>+10}")
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)

        # --- Assign ---
        elif args.assign:
            if not args.pi:
                parser.error("--pi is required for --assign")
            if not args.tasks:
                parser.error("--tasks is required for --assign")
            task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
            count = assign_tasks_to_pi(args.plan_id, args.pi, task_ids)
            if args.output_json:
                print(json.dumps({
                    "plan_id": args.plan_id,
                    "pi_number": args.pi,
                    "tasks_requested": len(task_ids),
                    "tasks_assigned": count,
                }, indent=2))
            else:
                print(f"Assigned {count}/{len(task_ids)} tasks to {args.pi}")

        # --- PI Report ---
        elif args.pi_report:
            if not args.pi:
                parser.error("--pi is required for --pi-report")
            result = generate_pi_migration_report(
                plan_id=args.plan_id,
                pi_number=args.pi,
                output_dir=args.output_dir,
            )
            if args.output_dir:
                print(f"Report written to: {result}")
            else:
                print(result)

        # --- Gate ---
        elif args.gate:
            if not args.pi:
                parser.error("--pi is required for --gate")
            result = check_pi_compliance_gate(args.plan_id, args.pi)
            if args.output_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)
                gate_label = "PASS" if result["passed"] else "FAIL"
                print(f"Compliance Gate: {gate_label}")
                print(f"  Plan:      {result['plan_id']}")
                print(f"  PI:        {result['pi_number']}")
                print(f"  Score:     {result['score']:.4f}")
                print(f"  Threshold: {result['threshold']}")
                if result["issues"]:
                    print("  Issues:")
                    for issue in result["issues"]:
                        print(f"    - {issue}")
                print("=" * 60)
                print("CUI // SP-CTI")
                print("=" * 60)

        # --- Update Task ---
        elif args.update_task:
            if not args.task_id:
                parser.error("--task-id is required for --update-task")
            if not args.status:
                parser.error("--status is required for --update-task")
            result = update_task_status(
                task_id=args.task_id,
                status=args.status,
                actual_hours=args.hours,
            )
            if args.output_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                if "error" in result:
                    print(f"ERROR: {result['error']}")
                    sys.exit(1)
                print(f"Task {args.task_id} updated to '{args.status}'")
                if args.hours is not None:
                    print(f"  Actual hours: {args.hours}")

        # --- Dashboard ---
        elif args.dashboard:
            result = get_dashboard(args.plan_id)
            if args.output_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                if "error" in result:
                    print(f"ERROR: {result['error']}")
                    sys.exit(1)
                _print_dashboard(result)

    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as exc:
        print(f"ERROR: Database error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
